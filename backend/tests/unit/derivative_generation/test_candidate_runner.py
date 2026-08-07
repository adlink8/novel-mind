"""Unit tests for the budgeted candidate runner and strict gates (Phase 37-02).

Covers the D-37-02 contract layer: strict candidate parsing, the deterministic
gate chain (package hash replay, evidence allowlist, divergence) and the
budgeted runner state machine with an injectable fake gateway:

- sealed package -> ai_router -> strict schema candidate -> deterministic gate;
- fake gateway is deterministic/replayable and records prompt/model/package hash,
  reserved vs actual usage/cost and budget lineage;
- budget overrun and schema violations never call or publish;
- terminal jobs are never silently re-called; paused jobs are recoverable;
- provider output only ever lands in ``derivative_generation_candidates`` —
  Original chapter content is never mutated (REQ-FORK-03 / REQ-CRE-06).
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.derivative_generation import DerivativeGenerationJobService
from app.models.base import Base
from app.models.canon_fork import CanonFork
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DerivativeGenerationAttempt,
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.derivative_generation.candidate import (
    GateVerdict,
    apply_deterministic_gates,
    parse_candidate,
    schema_hash,
)
from app.services.derivative_generation.context_package import (
    DimensionStatus,
    assemble_package_payload,
    budget_verdict,
    dimension_view,
    package_hash,
)
from app.services.derivative_generation.runner import (
    BudgetExceeded,
    CandidateRunError,
    DerivativeBudgetGate,
    DerivativeBudgetPolicy,
    DerivativeCandidateRunner,
    build_generation_idempotency_key,
    compile_prompt,
    config_hash,
    prompt_hash as compute_prompt_hash,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
CANDIDATE_KEY = "fork:ff-test:chapter:1"

# Subset of tables required by the generation control plane (SQLite unit DB).
TABLES = [
    "users",
    "novels",
    "chapters",
    "canon_forks",
    "derivative_context_packages",
    "derivative_generation_jobs",
    "derivative_generation_attempts",
    "derivative_generation_candidates",
]


# ---------------------------------------------------------------------------
# Deterministic fixture payloads (mirror test_context_package.py)
# ---------------------------------------------------------------------------


def _lineage(**overrides):
    data = {
        "source_version_key": "original:abc",
        "source_snapshot_hash": HEX64,
        "through_chapter": 3,
        "full_book_authorized": False,
        "cutoff_snapshot_hash": HEX64,
        "scope_hash": HEX64,
        "manifest_hash": HEX64,
    }
    data.update(overrides)
    return data


def _dimensions(**overrides):
    data = {
        "world_state": dimension_view(
            status=DimensionStatus.AVAILABLE, items=[{"entity_key": "hero"}]
        ),
        "timeline": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "unresolved_clues": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "world_rules": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"rule_key": "magic-no-resurrection"}],
        ),
        "evidence": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"candidate_key": CANDIDATE_KEY, "chapter_number": 1}],
        ),
        "user_intent": {"status": "available", "kind": "continuation", "hash": HEX64},
    }
    data.update(overrides)
    return data


def _payload(*, intent="continuation", dimensions=None, budget_estimate=None):
    core = assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=7,
        fork_key="ff-test",
        intent=intent,
        lineage=_lineage(),
        dimensions=dimensions if dimensions is not None else _dimensions(),
        budget_estimate={},
    )
    core["budget_estimate"] = (
        budget_estimate if budget_estimate is not None else budget_verdict(core, None)
    )
    return core


class FakeTransport:
    """Deterministic, replayable fake gateway (A3)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def _candidate_json(
    *,
    intent="continuation",
    citations=None,
    divergence=None,
    draft="阿宁走进竹林。",
    branch=None,
):
    payload = {
        "schema_version": "derivative-candidate.v1",
        "intent": intent,
        "draft_text": draft,
        "citation_keys": citations or [CANDIDATE_KEY],
        "divergence": divergence,
        "branch_suggestions": branch or [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _divergence_payload():
    return {
        "divergence_type": "character",
        "reason": "hero must act out of character for the twist",
        "affected_evidence": [CANDIDATE_KEY],
        "scope": "derivative",
    }


def _gate(*, max_calls=20):
    return DerivativeBudgetGate(
        DerivativeBudgetPolicy(
            max_calls=max_calls,
            max_input_tokens=100_000,
            max_output_tokens=100_000,
            max_cost_usd=Decimal("100"),
        )
    )


def _tiny_gate():
    return DerivativeBudgetGate(
        DerivativeBudgetPolicy(
            max_calls=1,
            max_input_tokens=1,
            max_output_tokens=1,
            max_cost_usd=Decimal("0.0000000001"),
        )
    )


# ---------------------------------------------------------------------------
# SQLite unit DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(
                sync, tables=[Base.metadata.tables[t] for t in TABLES]
            )
        )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _seed(
    session,
    *,
    intent="continuation",
    dimensions=None,
    fork_key="ff-test",
    budget_estimate=None,
    chapter_content="chapter 1 body",
):
    user = User(username="u", email="u@e.com", hashed_password="x")
    session.add(user)
    await session.flush()
    novel = Novel(
        title="N",
        owner_id=user.id,
        status="ready",
        reading_progress={},
        chapter_count=1,
        word_count=len(chapter_content),
    )
    session.add(novel)
    await session.flush()
    fork = CanonFork(
        owner_id=user.id,
        novel_id=novel.id,
        fork_key=fork_key,
        space="fanfiction_canon",
        status="approved",
        source_version_key="original:abc",
        source_snapshot_id="snap-1",
        source_snapshot_hash=HEX64,
        through_chapter=3,
        full_book_authorized=False,
        cutoff_snapshot_hash=HEX64,
        scope_hash=HEX64,
        manifest_hash=HEX64,
        citation_lineage=[],
        authorization={},
    )
    session.add(fork)
    await session.flush()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="C1",
        content=chapter_content,
        word_count=len(chapter_content),
    )
    session.add(chapter)
    await session.flush()
    payload = _payload(
        intent=intent,
        dimensions=dimensions,
        budget_estimate=budget_estimate,
    )
    sealed = package_hash(payload)
    pkg = ContextPackageRecord(
        owner_id=user.id,
        novel_id=novel.id,
        fork_id=fork.id,
        package_key=f"ctx:{fork_key}:{intent}:3",
        space="fanfiction_canon",
        intent=intent,
        fork_key=fork_key,
        source_version_key="original:abc",
        source_snapshot_hash=HEX64,
        through_chapter=3,
        full_book_authorized=False,
        cutoff_snapshot_hash=HEX64,
        scope_hash=HEX64,
        manifest_hash=HEX64,
        canonical_payload=payload,
        budget_estimate=payload["budget_estimate"],
        package_hash=sealed,
    )
    session.add(pkg)
    await session.flush()
    return {
        "owner_id": user.id,
        "novel_id": novel.id,
        "fork_id": fork.id,
        "package_id": pkg.id,
        "package_hash": sealed,
        "payload": payload,
        "chapter_id": chapter.id,
    }


async def _make_job(
    session,
    seed,
    *,
    job_key="job-1",
    status="queued",
    package_hash=None,
    intent="continuation",
    prompt_hash=None,
):
    payload = seed["payload"]
    row = DerivativeGenerationJob(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        fork_id=seed["fork_id"],
        context_package_id=seed["package_id"],
        package_hash=package_hash or seed["package_hash"],
        intent=intent,
        job_key=job_key,
        idempotency_key=build_generation_idempotency_key(
            seed["owner_id"],
            seed["novel_id"],
            package_hash=package_hash or seed["package_hash"],
            intent=intent,
            job_key=job_key,
        ),
        status=status,
        prompt_hash=prompt_hash or compute_prompt_hash(payload, intent=intent),
        schema_hash=schema_hash(),
        config_hash=config_hash(),
    )
    session.add(row)
    await session.flush()
    return row


def _runner(session, transport, *, gate=None):
    return DerivativeCandidateRunner(
        session, transport=transport, budget_gate=gate or _gate()
    )


def _original_content(session, novel_id, chapter_id):
    return session.scalar(
        select(Chapter.content).where(
            Chapter.id == chapter_id, Chapter.novel_id == novel_id
        )
    )


# ---------------------------------------------------------------------------
# Strict candidate parsing + deterministic gates (DB-free)
# ---------------------------------------------------------------------------


def test_parse_candidate_accepts_strict_payload():
    draft = parse_candidate(_candidate_json())
    assert draft.intent.value == "continuation"
    assert draft.draft_text == "阿宁走进竹林。"
    assert draft.citation_keys == [CANDIDATE_KEY]
    assert draft.has_divergence is False
    assert draft.branch_suggestions == []


def test_parse_candidate_rejects_extra_fields():
    with pytest.raises(ValueError) as exc:
        parse_candidate(
            '{"schema_version":"derivative-candidate.v1","intent":"continuation",'
            '"draft_text":"x","citation_keys":[],"divergence":null,'
            '"branch_suggestions":[],"bonus_field":1}'
        )
    assert "schema_invalid" in str(exc.value)


def test_parse_candidate_rejects_empty_draft():
    with pytest.raises(ValueError):
        parse_candidate("")
    with pytest.raises(ValueError):
        parse_candidate(
            '{"schema_version":"derivative-candidate.v1","intent":"continuation",'
            '"draft_text":"","citation_keys":[]}'
        )


def test_parse_candidate_rejects_unknown_intent():
    with pytest.raises(ValueError):
        parse_candidate(
            '{"schema_version":"derivative-candidate.v1","intent":"autofork",'
            '"draft_text":"x","citation_keys":[]}'
        )


def test_branch_suggestion_must_be_disabled_by_default():
    with pytest.raises(ValueError):
        parse_candidate(
            json.dumps(
                {
                    "schema_version": "derivative-candidate.v1",
                    "intent": "continuation",
                    "draft_text": "x",
                    "citation_keys": [],
                    "branch_suggestions": [
                        {
                            "choice_text": "a",
                            "branch_summary": "b",
                            "triggering_conflict": "c",
                            "canon_delta_hash": HEX64,
                            "evidence_refs": [],
                            "enabled_by_default": True,
                        }
                    ],
                }
            )
        )


def test_deterministic_gates_evidence_outside_package_blocked():
    payload = _payload()
    draft = parse_candidate(_candidate_json(citations=["fork:ff-test:future:99"]))
    gate = apply_deterministic_gates(
        draft,
        payload,
        expected_package_hash=package_hash(payload),
        package_intent="continuation",
    )
    assert gate.verdict is GateVerdict.BLOCKED
    assert gate.reason == "evidence_outside_package"


def test_deterministic_gates_package_hash_mismatch_fails_closed():
    payload = _payload()
    draft = parse_candidate(_candidate_json())
    gate = apply_deterministic_gates(
        draft, payload, expected_package_hash="1" * 64, package_intent="continuation"
    )
    assert gate.verdict is GateVerdict.BLOCKED
    assert gate.reason == "package_hash_mismatch"


def test_deterministic_gates_divergence_needs_override():
    payload = _payload()
    draft = parse_candidate(_candidate_json(divergence=_divergence_payload()))
    gate = apply_deterministic_gates(
        draft,
        payload,
        expected_package_hash=package_hash(payload),
        package_intent="continuation",
    )
    assert gate.verdict is GateVerdict.NEEDS_OVERRIDE
    assert gate.reason == "divergence_requires_override"


def test_deterministic_gates_intent_mismatch_blocked():
    payload = _payload(intent="rewrite")
    draft = parse_candidate(_candidate_json(intent="continuation"))
    gate = apply_deterministic_gates(
        draft,
        payload,
        expected_package_hash=package_hash(payload),
        package_intent="rewrite",
    )
    assert gate.verdict is GateVerdict.BLOCKED
    assert gate.reason == "intent_mismatch"


def test_deterministic_gates_clean_candidate_passes():
    payload = _payload()
    draft = parse_candidate(_candidate_json())
    gate = apply_deterministic_gates(
        draft,
        payload,
        expected_package_hash=package_hash(payload),
        package_intent="continuation",
    )
    assert gate.verdict is GateVerdict.CANDIDATE
    assert gate.reason is None


# ---------------------------------------------------------------------------
# Prompt / lineage determinism (replayable fake gateway)
# ---------------------------------------------------------------------------


def test_compile_prompt_and_prompt_hash_are_deterministic():
    payload = _payload()
    a = compile_prompt(payload, intent="continuation")
    b = compile_prompt(payload, intent="continuation")
    assert a == b
    assert compute_prompt_hash(payload, intent="continuation") == compute_prompt_hash(
        payload, intent="continuation"
    )
    assert compute_prompt_hash(payload, intent="continuation") != compute_prompt_hash(
        payload, intent="rewrite"
    )
    assert len(compute_prompt_hash(payload, intent="continuation")) == 64


def test_compile_prompt_carries_allowlisted_evidence_only():
    payload = _payload()
    messages = compile_prompt(payload, intent="continuation")
    user_body = messages[1]["content"]
    assert CANDIDATE_KEY in user_body
    assert "allowed_evidence_keys" in user_body
    # Never prompt with original chapter bodies outside the package.
    assert "chapter 1 body" not in user_body


def test_idempotency_key_deterministic_and_sensitive():
    a = build_generation_idempotency_key(
        1, 2, package_hash=HEX64, intent="continuation", job_key="j1"
    )
    b = build_generation_idempotency_key(
        1, 2, package_hash=HEX64, intent="continuation", job_key="j1"
    )
    assert a == b and len(a) == 64
    assert a != build_generation_idempotency_key(
        1, 2, package_hash=HEX64, intent="rewrite", job_key="j1"
    )
    assert a != build_generation_idempotency_key(
        1, 2, package_hash=HEX64, intent="continuation", job_key="j2"
    )


def test_budget_gate_reserve_settle_and_pause():
    gate = _gate(max_calls=1)
    reservation = gate.reserve(
        "k1",
        input_tokens=10,
        output_tokens=10,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    assert reservation.status == "reserved"
    gate.settle(
        "k1",
        actual_input_tokens=10,
        actual_output_tokens=10,
        actual_cost_usd=Decimal("0.00002"),
    )
    assert gate.reservations["k1"].status == "settled"
    with pytest.raises(ValueError):
        gate.settle(
            "k1",
            actual_input_tokens=1,
            actual_output_tokens=1,
            actual_cost_usd=Decimal("0"),
        )


def test_budget_gate_unknown_pricing_fails_closed():
    gate = _gate()
    with pytest.raises(BudgetExceeded):
        gate.reserve(
            "k1",
            input_tokens=10,
            output_tokens=10,
            input_price_per_million=None,
            output_price_per_million=None,
        )
    assert gate.paused is True


# ---------------------------------------------------------------------------
# Runner state machine (fake gateway, SQLite control-plane DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_produces_candidate_only_and_never_writes_original(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport(
        [
            {
                "content": _candidate_json(),
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "id": "req-1",
            }
        ]
    )
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "succeeded"
    assert result.gate_verdict == "candidate"
    candidate = result.candidate
    assert candidate is not None
    assert candidate.gate_verdict == "candidate"
    assert candidate.draft_text == "阿宁走进竹林。"
    assert candidate.usage["input_tokens"] == 100
    assert candidate.cost_usd is not None
    assert candidate.package_hash == seed["package_hash"]
    assert candidate.approval_state == "candidate"
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "succeeded"
    assert result.attempts[0].reserved_input_tokens >= 1
    assert len(transport.calls) == 1
    # D-37-02: the provider output only landed in the candidate table.
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationCandidate))
        == 1
    )
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationAttempt))
        == 1
    )
    # REQ-FORK-03: Original chapter content is untouched.
    assert (
        await _original_content(db, seed["novel_id"], seed["chapter_id"])
        == "chapter 1 body"
    )
    # job reload shows terminal succeeded with response lineage.
    job = await db.get(DerivativeGenerationJob, job.id)
    assert job.status == "succeeded" and job.response_hash is not None


@pytest.mark.asyncio
async def test_run_records_prompt_model_package_hashes(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport(
        [
            {
                "content": _candidate_json(),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ]
    )
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    attempt = result.attempts[0]
    assert len(attempt.request_hash) == 64
    assert len(attempt.response_hash) == 64
    assert attempt.provider and attempt.model_id
    assert result.candidate.model_lineage["provider"]
    assert result.candidate.package_hash == seed["package_hash"]
    assert result.candidate.prompt_hash == job.prompt_hash
    assert result.candidate.schema_hash == job.schema_hash


@pytest.mark.asyncio
async def test_schema_invalid_blocks_without_publishing(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport(
        [
            {
                "content": "not json at all",
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        ]
    )
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "blocked"
    assert result.error_code == "schema_invalid"
    assert result.candidate is None
    assert result.attempts[0].status == "failed"
    assert result.attempts[0].error_code == "schema_invalid"
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationCandidate))
        == 0
    )


@pytest.mark.asyncio
async def test_evidence_outside_package_blocked_with_candidate_lineage(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport(
        [
            {
                "content": _candidate_json(citations=["fork:ff-test:future:99"]),
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        ]
    )
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "blocked"
    assert result.error_code == "evidence_outside_package"
    candidate = result.candidate
    assert candidate is not None
    assert candidate.gate_verdict == "blocked"
    assert candidate.gate_reason == "evidence_outside_package"
    assert candidate.approval_state == "candidate"


@pytest.mark.asyncio
async def test_divergence_yields_needs_override_candidate(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport(
        [
            {
                "content": _candidate_json(divergence=_divergence_payload()),
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        ]
    )
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "needs_override"
    assert result.error_code == "divergence_requires_override"
    candidate = result.candidate
    assert candidate.gate_verdict == "needs_override"
    assert candidate.approval_state == "needs_override"
    assert candidate.divergence["divergence_type"] == "character"
    assert candidate.canon_delta_hash is not None


@pytest.mark.asyncio
async def test_budget_exhausted_never_calls_provider(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport, gate=_tiny_gate()).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "paused_budget"
    assert result.error_code == "budget_exhausted"
    assert transport.calls == []
    assert result.candidate is None
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationCandidate))
        == 0
    )
    # The rejected attempt is still audited (failure lineage).
    assert result.attempts[0].error_code == "budget_exhausted"
    # A paused job is recoverable: a fresh budget gate lets the retry run.
    retry_transport = FakeTransport(
        [
            {
                "content": _candidate_json(),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ]
    )
    retry = await _runner(db, retry_transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert retry.status == "succeeded"
    # Two runs happened: the budget-rejected run and the retry.
    assert retry.job.retry_count == 2


@pytest.mark.asyncio
async def test_provider_timeout_is_outcome_unknown_not_publish(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport([asyncio.TimeoutError("gateway timeout")])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "outcome_unknown"
    assert result.error_code == "provider_timeout"
    assert result.candidate is None
    assert result.attempts[0].status == "outcome_unknown"
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationCandidate))
        == 0
    )


@pytest.mark.asyncio
async def test_generic_provider_error_is_outcome_unknown(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    transport = FakeTransport([RuntimeError("boom")])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "outcome_unknown"
    assert result.error_code == "provider_error"
    assert result.candidate is None


@pytest.mark.asyncio
async def test_terminal_job_is_never_silently_recalled(db):
    seed = await _seed(db)
    job = await _make_job(db, seed, status="failed")
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    runner = _runner(db, transport)
    with pytest.raises(CandidateRunError) as exc:
        await runner.run(
            owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
        )
    assert exc.value.code == "job_not_runnable"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_cancel_before_run_returns_cancelled_without_call(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    job.cancel_requested = True
    await db.flush()
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "cancelled"
    assert result.error_code == "cancelled"
    assert transport.calls == []
    assert result.candidate is None


@pytest.mark.asyncio
async def test_package_hash_mismatch_fails_before_provider_call(db):
    seed = await _seed(db)
    job = await _make_job(db, seed, package_hash="b" * 64)
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "failed"
    assert result.error_code == "package_hash_mismatch"
    assert transport.calls == []
    assert result.candidate is None


@pytest.mark.asyncio
async def test_intent_mismatch_fails_before_provider_call(db):
    seed = await _seed(db, intent="rewrite")
    job = await _make_job(
        db, seed, intent="continuation"
    )  # job intent != package intent
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "failed"
    assert result.error_code == "intent_mismatch"
    assert transport.calls == []
    assert result.candidate is None


@pytest.mark.asyncio
async def test_prompt_hash_drift_fails_before_provider_call(db):
    seed = await _seed(db)
    job = await _make_job(db, seed, job_key="drift", prompt_hash="b" * 64)
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "failed"
    assert result.error_code == "prompt_hash_mismatch"
    assert transport.calls == []
    assert result.candidate is None


@pytest.mark.asyncio
async def test_config_hash_drift_fails_before_provider_call(db):
    seed = await _seed(db)
    job = await _make_job(db, seed, job_key="cfg")
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    # A runner built with a different decoding config must not replay the job.
    runner = DerivativeCandidateRunner(
        db, transport=transport, budget_gate=_gate(), max_output_tokens=1234
    )
    result = await runner.run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert result.status == "failed"
    assert result.error_code == "config_hash_mismatch"
    assert transport.calls == []
    assert result.candidate is None


@pytest.mark.asyncio
async def test_cross_fork_package_is_rejected(db):
    """A job bound to a package outside its owner/novel scope fails closed."""
    seed = await _seed(db)
    # A second owner with no package: job points at the first owner's package.
    other_user = User(username="other", email="o@e.com", hashed_password="x")
    db.add(other_user)
    await db.flush()
    payload = seed["payload"]
    other_job = DerivativeGenerationJob(
        owner_id=other_user.id,
        novel_id=seed["novel_id"],
        fork_id=seed["fork_id"],
        context_package_id=seed["package_id"],
        package_hash=seed["package_hash"],
        intent="continuation",
        job_key="other-job",
        idempotency_key=build_generation_idempotency_key(
            other_user.id,
            seed["novel_id"],
            package_hash=seed["package_hash"],
            intent="continuation",
            job_key="other-job",
        ),
        status="queued",
        prompt_hash=compute_prompt_hash(payload, intent="continuation"),
        schema_hash=schema_hash(),
        config_hash=config_hash(),
    )
    db.add(other_job)
    await db.flush()
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    result = await _runner(db, transport).run(
        owner_id=other_user.id, novel_id=seed["novel_id"], job_id=other_job.id
    )
    assert result.status == "failed"
    assert result.error_code == "package_not_found"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_fake_gateway_replay_produces_identical_candidates(db):
    """Two identical runs yield identical response lineage (replayable fake)."""
    seed = await _seed(db)
    first_job = await _make_job(db, seed, job_key="replay-a")
    second_job = await _make_job(db, seed, job_key="replay-b")
    response = {
        "content": _candidate_json(),
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "id": "req-x",
    }
    r1 = await _runner(db, FakeTransport([dict(response)])).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=first_job.id
    )
    r2 = await _runner(db, FakeTransport([dict(response)])).run(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=second_job.id
    )
    assert r1.status == r2.status == "succeeded"
    assert r1.candidate.response_hash == r2.candidate.response_hash
    assert r1.candidate.draft_text == r2.candidate.draft_text
    assert r1.attempts[0].usage == r2.attempts[0].usage


# ---------------------------------------------------------------------------
# Job service: idempotency, cross-fork create rejection, cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job_is_idempotent(db):
    seed = await _seed(db)
    service = DerivativeGenerationJobService(
        db, transport=FakeTransport([]), budget_gate=_gate()
    )
    job, replayed = await service.create_job(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        context_package_id=seed["package_id"],
        intent="continuation",
        job_key="dup-key",
    )
    assert replayed is False
    job2, replayed2 = await service.create_job(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        context_package_id=seed["package_id"],
        intent="continuation",
        job_key="dup-key",
    )
    assert replayed2 is True
    assert job2.id == job.id
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationJob)) == 1
    )


@pytest.mark.asyncio
async def test_create_job_rejects_cross_fork_package(db):
    seed = await _seed(db)
    other_user = User(username="other2", email="o2@e.com", hashed_password="x")
    db.add(other_user)
    await db.flush()
    service = DerivativeGenerationJobService(
        db, transport=FakeTransport([]), budget_gate=_gate()
    )
    with pytest.raises(Exception) as exc:
        await service.create_job(
            owner_id=other_user.id,
            novel_id=seed["novel_id"],
            context_package_id=seed["package_id"],
            intent="continuation",
            job_key="foreign",
        )
    assert getattr(exc.value, "code", None) == "package_not_found"
    assert (
        await db.scalar(select(func.count()).select_from(DerivativeGenerationJob)) == 0
    )


@pytest.mark.asyncio
async def test_create_job_rejects_intent_mismatch(db):
    seed = await _seed(db, intent="rewrite")
    service = DerivativeGenerationJobService(
        db, transport=FakeTransport([]), budget_gate=_gate()
    )
    with pytest.raises(Exception) as exc:
        await service.create_job(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            context_package_id=seed["package_id"],
            intent="continuation",
            job_key="wrong-intent",
        )
    assert getattr(exc.value, "code", None) == "intent_mismatch"


@pytest.mark.asyncio
async def test_cancel_then_run_is_cancelled_without_call(db):
    seed = await _seed(db)
    job = await _make_job(db, seed)
    service = DerivativeGenerationJobService(
        db, transport=FakeTransport([]), budget_gate=_gate()
    )
    cancelled = await service.cancel_job(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested is True
    # A terminal cancelled job is never re-called (recovery is explicit).
    transport = FakeTransport([{"content": _candidate_json(), "usage": {}}])
    runner = _runner(db, transport)
    with pytest.raises(CandidateRunError) as exc:
        await runner.run(
            owner_id=seed["owner_id"], novel_id=seed["novel_id"], job_id=job.id
        )
    assert exc.value.code == "job_not_runnable"
    assert transport.calls == []
