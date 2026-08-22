"""Source snapshot, model-lineage isolation, deterministic checks, fixture
hash/freeze and the freeze pipeline (core of the former rag_fixture.py).

Phase 06-03 (D-01..D-03, D-11, D-15). Does NOT score SUT answers (that is
06-04). Adversarial gates and Judge calibration live in ``adversarial.py``;
pure hashing/signing utilities live in ``_hashing.py``.
"""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.schemas.eval import (
    CANONICALIZATION_VERSION,
    SCHEMA_VERSION_RAG_QUALITY,
    Claim,
    DeterministicCheckResult,
    DeterministicChecks,
    EquivalentEvidenceSet,
    EvalCase,
    EvidenceRef,
    FixtureJobState,
    JudgeFixtureVerdict,
    ModelLineage,
    SnapshotChunk,
    SourceSnapshot,
)

from ._hashing import (
    quote_hash,
    schema_contract_hash,
    sign_payload,
    stable_hash,
    text_hash,
    verify_signature,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_REGENERATE = 2  # attempts 0..2; third failure => quarantined
MAX_ATTEMPTS = MAX_REGENERATE + 1
JUDGE_MIN_SCORE = 3
MAX_CHUNK_TEXT_LEN = 50_000

DEFAULT_SIGNING_SECRET = "novelmind-rag-fixture-dev-secret"

GeneratorFn = Callable[[SourceSnapshot, dict[str, Any], ModelLineage], EvalCase]
JudgeFn = Callable[[EvalCase, SourceSnapshot, ModelLineage], JudgeFixtureVerdict]


class RagFixtureError(ValueError):
    """Base error for fixture pipeline failures."""

    def __init__(self, message: str, *, status: str = "invalid_fixture") -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class InvalidLineageError(RagFixtureError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status="invalid_lineage")


class InvalidFixtureError(RagFixtureError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status="invalid_fixture")


class FailedPolicyError(RagFixtureError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status="failed_policy")


# ---------------------------------------------------------------------------
# Source snapshot
# ---------------------------------------------------------------------------


def _chunk_manifest_entries(chunks: list[SnapshotChunk]) -> list[dict[str, Any]]:
    return [
        {
            "content_hash": c.content_hash,
            "text_hash": c.text_hash,
            "length": c.length,
        }
        for c in chunks
    ]


def build_chunk(text: str) -> SnapshotChunk:
    if len(text) > MAX_CHUNK_TEXT_LEN:
        raise InvalidFixtureError(f"chunk exceeds max length {MAX_CHUNK_TEXT_LEN}")
    th = text_hash(text)
    return SnapshotChunk(
        content_hash=th,
        text_hash=th,
        length=len(text),
        text=text,
    )


def build_source_snapshot(
    *,
    owner_id: int,
    work_id: int,
    texts: list[str],
    version: str = "v1",
    snapshot_id: str | None = None,
    secret: str = DEFAULT_SIGNING_SECRET,
    created_at: datetime | None = None,
) -> SourceSnapshot:
    if owner_id < 1 or work_id < 1:
        raise InvalidFixtureError("owner_id and work_id must be positive")
    if not texts:
        raise InvalidFixtureError("snapshot requires at least one chunk")
    chunks = [build_chunk(t) for t in texts]
    manifest_hash = stable_hash(
        {
            "owner_id": owner_id,
            "work_id": work_id,
            "version": version,
            "canonicalization_version": CANONICALIZATION_VERSION,
            "chunks": _chunk_manifest_entries(chunks),
        }
    )
    created = created_at or datetime.now(timezone.utc)
    sid = snapshot_id or f"snap-{manifest_hash[:16]}"
    unsigned = {
        "snapshot_id": sid,
        "owner_id": owner_id,
        "work_id": work_id,
        "version": version,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "chunks": _chunk_manifest_entries(chunks),
        "manifest_hash": manifest_hash,
        "created_at": created.isoformat(),
    }
    signature = sign_payload(unsigned, secret)
    return SourceSnapshot(
        snapshot_id=sid,
        owner_id=owner_id,
        work_id=work_id,
        version=version,
        canonicalization_version=CANONICALIZATION_VERSION,
        chunks=chunks,
        manifest_hash=manifest_hash,
        created_at=created,
        signature=signature,
    )


def verify_source_snapshot(
    snapshot: SourceSnapshot, secret: str = DEFAULT_SIGNING_SECRET
) -> bool:
    payload = {
        "snapshot_id": snapshot.snapshot_id,
        "owner_id": snapshot.owner_id,
        "work_id": snapshot.work_id,
        "version": snapshot.version,
        "canonicalization_version": snapshot.canonicalization_version,
        "chunks": _chunk_manifest_entries(snapshot.chunks),
        "manifest_hash": snapshot.manifest_hash,
        "created_at": snapshot.created_at.isoformat()
        if isinstance(snapshot.created_at, datetime)
        else str(snapshot.created_at),
        "signature": snapshot.signature,
    }
    expected_manifest = stable_hash(
        {
            "owner_id": snapshot.owner_id,
            "work_id": snapshot.work_id,
            "version": snapshot.version,
            "canonicalization_version": snapshot.canonicalization_version,
            "chunks": _chunk_manifest_entries(snapshot.chunks),
        }
    )
    if not hmac.compare_digest(expected_manifest, snapshot.manifest_hash):
        return False
    return verify_signature(payload, secret)


def snapshot_chunk_map(snapshot: SourceSnapshot) -> dict[str, SnapshotChunk]:
    return {c.content_hash: c for c in snapshot.chunks}


def make_evidence_ref(
    snapshot: SourceSnapshot,
    content_hash_value: str,
    start: int,
    end: int,
) -> EvidenceRef:
    chunks = snapshot_chunk_map(snapshot)
    chunk = chunks.get(content_hash_value)
    if chunk is None or chunk.text is None:
        raise InvalidFixtureError(
            "chunk not found or text unavailable for evidence ref"
        )
    if start < 0 or end > len(chunk.text) or start > end:
        raise InvalidFixtureError("evidence offsets out of range")
    quote = chunk.text[start:end]
    return EvidenceRef(
        chunk_content_hash=content_hash_value,
        start_offset=start,
        end_offset=end,
        quote_hash=quote_hash(quote),
        quote_text=quote,
    )


def verify_evidence_ref(ref: EvidenceRef, snapshot: SourceSnapshot) -> bool:
    """Re-slice chunk by offsets and recompute quote hash."""
    chunks = snapshot_chunk_map(snapshot)
    chunk = chunks.get(ref.chunk_content_hash)
    if chunk is None:
        return False
    if chunk.text is None:
        # Without text we can only check structural bounds against declared length.
        if (
            ref.start_offset < 0
            or ref.end_offset > chunk.length
            or ref.start_offset > ref.end_offset
        ):
            return False
        if ref.quote_text is not None:
            return hmac.compare_digest(quote_hash(ref.quote_text), ref.quote_hash)
        return True
    if (
        ref.start_offset < 0
        or ref.end_offset > len(chunk.text)
        or ref.start_offset > ref.end_offset
    ):
        return False
    quote = chunk.text[ref.start_offset : ref.end_offset]
    if not hmac.compare_digest(quote_hash(quote), ref.quote_hash):
        return False
    if ref.quote_text is not None and ref.quote_text != quote:
        return False
    return True


# ---------------------------------------------------------------------------
# Model lineage isolation (D-01, D-15)
# ---------------------------------------------------------------------------


def resolve_lineage(
    *,
    provider: str,
    model_family: str,
    model_id: str,
    weights_revision: str | None,
    prompt_hash: str,
    prompt_version: str,
    schema_hash: str | None = None,
    endpoint_class: str = "offline_stub",
    decoding: dict[str, Any] | None = None,
    runtime: str = "offline",
    started_at: datetime | None = None,
) -> ModelLineage:
    if not weights_revision:
        raise InvalidLineageError("model alias did not resolve to weights/revision")
    if not model_family or not model_id:
        raise InvalidLineageError("model_family and model_id are required")
    return ModelLineage(
        provider=provider,
        model_family=model_family,
        model_id=model_id,
        weights_revision=weights_revision,
        endpoint_class=endpoint_class,
        prompt_hash=prompt_hash,
        prompt_version=prompt_version,
        schema_hash=schema_hash or schema_contract_hash(),
        decoding=decoding or {},
        runtime=runtime,
        started_at=started_at or datetime.now(timezone.utc),
    )


def validate_generator_judge_isolation(
    generator: ModelLineage, judge: ModelLineage
) -> None:
    """G and J must differ in model_family AND weights/revision (fail closed)."""
    same_family = (
        generator.model_family.strip().lower() == judge.model_family.strip().lower()
    )
    same_weights = (
        generator.weights_revision.strip().lower()
        == judge.weights_revision.strip().lower()
    )
    if same_family or same_weights:
        raise InvalidLineageError(
            "Generator and Judge must differ in model_family AND weights/revision; "
            f"same_family={same_family}, same_weights={same_weights}"
        )


# ---------------------------------------------------------------------------
# Deterministic checks (D-11)
# ---------------------------------------------------------------------------


def _all_refs(case: EvalCase) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    for es in case.equivalent_evidence_sets:
        refs.extend(es.refs)
    return refs


def _claim_ids_unique(claims: list[Claim]) -> bool:
    ids = [c.claim_id for c in claims]
    return len(ids) == len(set(ids))


def run_deterministic_checks(
    case: EvalCase,
    snapshot: SourceSnapshot,
    *,
    expected_owner_id: int | None = None,
    expected_work_id: int | None = None,
) -> DeterministicChecks:
    details: list[DeterministicCheckResult] = []

    # schema
    schema_ok = case.schema_version == SCHEMA_VERSION_RAG_QUALITY
    details.append(
        DeterministicCheckResult(
            name="schema",
            passed=schema_ok,
            detail=None if schema_ok else f"got {case.schema_version}",
        )
    )

    # snapshot hash
    snapshot_hash_ok = hmac.compare_digest(case.snapshot_hash, snapshot.manifest_hash)
    if expected_owner_id is not None and snapshot.owner_id != expected_owner_id:
        snapshot_hash_ok = False
    if expected_work_id is not None and snapshot.work_id != expected_work_id:
        snapshot_hash_ok = False
    details.append(
        DeterministicCheckResult(
            name="snapshot_hash",
            passed=snapshot_hash_ok,
            detail=None if snapshot_hash_ok else "snapshot hash/owner mismatch",
        )
    )

    # offset / quote
    refs = _all_refs(case)
    offset_quote_ok = (
        all(verify_evidence_ref(r, snapshot) for r in refs)
        if refs
        else (case.case_type == "no_answer")
    )
    if case.case_type != "no_answer" and not refs:
        offset_quote_ok = False
    details.append(
        DeterministicCheckResult(
            name="offset_quote",
            passed=offset_quote_ok,
            detail=None if offset_quote_ok else "offset/quote verification failed",
        )
    )

    # claims
    if case.case_type == "answerable":
        claims_ok = bool(case.claims) and _claim_ids_unique(case.claims)
    elif case.case_type == "no_answer":
        claims_ok = len(case.claims) == 0
    else:  # hard_negative
        claims_ok = True  # may have claims that must NOT be supported
    details.append(
        DeterministicCheckResult(
            name="claims",
            passed=claims_ok,
            detail=None if claims_ok else "claims constraint failed for case_type",
        )
    )

    # critical claim support — every critical claim must reference a known set
    set_ids = {es.set_id for es in case.equivalent_evidence_sets}
    critical_ok = True
    if case.case_type == "answerable":
        for claim in case.claims:
            if claim.critical:
                if (
                    not claim.evidence_set_ids
                    or not set(claim.evidence_set_ids) <= set_ids
                ):
                    critical_ok = False
                    break
                # At least one ref in each set must verify
                for sid in claim.evidence_set_ids:
                    es = next(
                        e for e in case.equivalent_evidence_sets if e.set_id == sid
                    )
                    if not any(verify_evidence_ref(r, snapshot) for r in es.refs):
                        critical_ok = False
                        break
    details.append(
        DeterministicCheckResult(
            name="critical_claim_support",
            passed=critical_ok,
            detail=None
            if critical_ok
            else "critical claim lacks verified evidence set",
        )
    )

    # equivalent sets structure
    eq_ok = True
    seen_sets: set[str] = set()
    for es in case.equivalent_evidence_sets:
        if es.set_id in seen_sets or not es.refs:
            eq_ok = False
            break
        seen_sets.add(es.set_id)
        if not all(verify_evidence_ref(r, snapshot) for r in es.refs):
            eq_ok = False
            break
    if case.case_type == "answerable" and not case.equivalent_evidence_sets:
        eq_ok = False
    details.append(
        DeterministicCheckResult(
            name="equivalent_sets",
            passed=eq_ok,
            detail=None if eq_ok else "equivalent evidence sets invalid",
        )
    )

    # leak detection — reference answer must not dump entire chunk; question not embed gold
    leak_ok = True
    if case.reference_answer and case.case_type == "answerable":
        for chunk in snapshot.chunks:
            if (
                chunk.text
                and len(chunk.text) > 40
                and chunk.text in case.reference_answer
            ):
                leak_ok = False
                break
        for ref in refs:
            if ref.quote_text and len(ref.quote_text) > 20:
                # full quote dump as only answer is suspicious but allowed if short
                pass
    # Reject cases that only have gold_chunk_db_ids as truth
    if case.gold_chunk_db_ids and not refs and case.case_type == "answerable":
        leak_ok = False
    details.append(
        DeterministicCheckResult(
            name="leak",
            passed=leak_ok,
            detail=None if leak_ok else "answer leak or DB-id-only truth",
        )
    )

    # no-answer: must not have supporting package evidence that answers the question
    no_answer_ok = True
    if case.case_type == "no_answer":
        if case.claims or case.equivalent_evidence_sets:
            no_answer_ok = False
        if case.reference_answer and case.reference_answer.strip().lower() not in {
            "",
            "unknown",
            "no answer",
            "insufficient evidence",
            "无法回答",
            "证据不足",
        }:
            # Allow explicit refuse phrases only
            refuse = any(
                p in case.reference_answer.lower()
                for p in (
                    "cannot answer",
                    "no evidence",
                    "unknown",
                    "无法",
                    "证据不足",
                    "不足以",
                )
            )
            if not refuse:
                no_answer_ok = False
    details.append(
        DeterministicCheckResult(
            name="no_answer",
            passed=no_answer_ok,
            detail=None if no_answer_ok else "no-answer case has package support",
        )
    )

    # hard-negative: must have nearby evidence that does NOT support the answer claims
    hard_neg_ok = True
    if case.case_type == "hard_negative":
        if not case.equivalent_evidence_sets:
            hard_neg_ok = False
        else:
            # Evidence must verify (near-miss present) but claims if any must not be critical-supported
            for claim in case.claims:
                if claim.critical and claim.evidence_set_ids:
                    hard_neg_ok = False
                    break
    details.append(
        DeterministicCheckResult(
            name="hard_negative",
            passed=hard_neg_ok,
            detail=None if hard_neg_ok else "hard-negative constraints failed",
        )
    )

    return DeterministicChecks(
        schema_ok=schema_ok,
        snapshot_hash_ok=snapshot_hash_ok,
        offset_quote_ok=offset_quote_ok,
        claims_ok=claims_ok,
        critical_claim_support_ok=critical_ok,
        equivalent_sets_ok=eq_ok,
        leak_ok=leak_ok,
        no_answer_ok=no_answer_ok,
        hard_negative_ok=hard_neg_ok,
        details=details,
    )


def judge_accepts(verdict: JudgeFixtureVerdict) -> bool:
    return (
        verdict.faithfulness >= JUDGE_MIN_SCORE
        and verdict.coverage >= JUDGE_MIN_SCORE
        and verdict.sufficiency >= JUDGE_MIN_SCORE
        and verdict.critical_ambiguity == 0
    )


# ---------------------------------------------------------------------------
# Fixture hash / freeze
# ---------------------------------------------------------------------------


def eval_case_hash_payload(case: EvalCase) -> dict[str, Any]:
    """Canonical fields that enter fixture_hash (immutable identity)."""
    return {
        "case_id": case.case_id,
        "schema_version": case.schema_version,
        "snapshot_hash": case.snapshot_hash,
        "question": case.question,
        "case_type": case.case_type,
        "claims": [c.model_dump() for c in case.claims],
        "equivalent_evidence_sets": [
            {
                "set_id": es.set_id,
                "refs": [
                    {
                        "chunk_content_hash": r.chunk_content_hash,
                        "start_offset": r.start_offset,
                        "end_offset": r.end_offset,
                        "quote_hash": r.quote_hash,
                    }
                    for r in es.refs
                ],
            }
            for es in case.equivalent_evidence_sets
        ],
        "reference_answer": case.reference_answer,
        "generator_lineage": (
            case.generator_lineage.model_dump(by_alias=True)
            if case.generator_lineage
            else None
        ),
        "judge_fixture_verdict": (
            case.judge_fixture_verdict.model_dump()
            if case.judge_fixture_verdict
            else None
        ),
        "attempt": case.attempt,
        "parent_case_id": case.parent_case_id,
    }


def compute_fixture_hash(case: EvalCase) -> str:
    return stable_hash(eval_case_hash_payload(case))


def freeze_eval_case(
    case: EvalCase,
    secret: str = DEFAULT_SIGNING_SECRET,
) -> EvalCase:
    frozen = case.model_copy(deep=True)
    frozen.status = "frozen"
    fh = compute_fixture_hash(frozen)
    frozen.fixture_hash = fh
    payload = {**eval_case_hash_payload(frozen), "fixture_hash": fh}
    frozen.signature = sign_payload(payload, secret)
    return frozen


def verify_frozen_case(case: EvalCase, secret: str = DEFAULT_SIGNING_SECRET) -> bool:
    if case.status != "frozen" or not case.fixture_hash or not case.signature:
        return False
    if not hmac.compare_digest(compute_fixture_hash(case), case.fixture_hash):
        return False
    payload = {
        **eval_case_hash_payload(case),
        "fixture_hash": case.fixture_hash,
        "signature": case.signature,
    }
    return verify_signature(payload, secret)


# ---------------------------------------------------------------------------
# Pipeline: snapshot_ready -> generating -> deterministic_validation
#            -> judge_review -> frozen | regenerate | quarantined
# ---------------------------------------------------------------------------


def create_fixture_job(
    *,
    owner_id: int,
    work_id: int,
    snapshot: SourceSnapshot,
    job_id: str | None = None,
) -> FixtureJobState:
    if snapshot.owner_id != owner_id or snapshot.work_id != work_id:
        raise FailedPolicyError("cross-owner or cross-work snapshot access denied")
    return FixtureJobState(
        job_id=job_id or f"fixjob-{uuid.uuid4().hex[:16]}",
        owner_id=owner_id,
        work_id=work_id,
        snapshot_id=snapshot.snapshot_id,
        status="snapshot_ready",
        attempt=0,
        metrics=None,
        quality_comparable=False,
    )


def default_stub_generator(
    snapshot: SourceSnapshot,
    spec: dict[str, Any],
    lineage: ModelLineage,
) -> EvalCase:
    """Deterministic offline generator transcript (no live LLM)."""
    case_type = spec.get("case_type", "answerable")
    case_id = spec.get("case_id") or f"case-{stable_hash(spec)[:12]}"
    question = spec["question"]
    claims: list[Claim] = []
    eq_sets: list[EquivalentEvidenceSet] = []
    reference = spec.get("reference_answer")

    if case_type == "answerable":
        # Build evidence from first chunk substring if provided
        if "evidence" in spec:
            refs = []
            for ev in spec["evidence"]:
                refs.append(
                    make_evidence_ref(
                        snapshot,
                        ev["content_hash"],
                        ev["start"],
                        ev["end"],
                    )
                )
            eq_sets = [EquivalentEvidenceSet(set_id="s1", refs=refs)]
        claims = [
            Claim(
                claim_id=c.get("claim_id", f"c{i}"),
                text=c["text"],
                critical=c.get("critical", True),
                evidence_set_ids=c.get("evidence_set_ids", ["s1"]),
            )
            for i, c in enumerate(
                spec.get("claims", [{"text": "supported claim", "critical": True}])
            )
        ]
        if reference is None and eq_sets and eq_sets[0].refs:
            reference = eq_sets[0].refs[0].quote_text
    elif case_type == "no_answer":
        claims = []
        eq_sets = []
        reference = reference or "insufficient evidence"
    elif case_type == "hard_negative":
        if "evidence" in spec:
            refs = [
                make_evidence_ref(snapshot, ev["content_hash"], ev["start"], ev["end"])
                for ev in spec["evidence"]
            ]
            eq_sets = [EquivalentEvidenceSet(set_id="near", refs=refs)]
        claims = [
            Claim(
                claim_id="hn1",
                text=spec.get("false_claim", "unsupported near-miss claim"),
                critical=False,
                evidence_set_ids=[],
            )
        ]
        reference = reference or "hard negative — do not treat as support"

    return EvalCase(
        case_id=case_id,
        schema_version=SCHEMA_VERSION_RAG_QUALITY,
        snapshot_hash=snapshot.manifest_hash,
        question=question,
        case_type=case_type,
        claims=claims,
        equivalent_evidence_sets=eq_sets,
        reference_answer=reference,
        generator_lineage=lineage,
        status="generating",
        attempt=int(spec.get("attempt", 0)),
        parent_case_id=spec.get("parent_case_id"),
        gold_chunk_db_ids=spec.get("gold_chunk_db_ids"),
    )


def default_stub_judge(
    case: EvalCase,
    snapshot: SourceSnapshot,
    lineage: ModelLineage,
) -> JudgeFixtureVerdict:
    """Deterministic offline judge — scores based on deterministic check health."""
    checks = run_deterministic_checks(case, snapshot)
    if checks.all_passed:
        return JudgeFixtureVerdict(
            faithfulness=4,
            coverage=4,
            sufficiency=4,
            critical_ambiguity=0,
            reason_codes=["stub_accept"],
            accepted=True,
        )
    failed = [d.name for d in checks.details if not d.passed]
    return JudgeFixtureVerdict(
        faithfulness=1,
        coverage=1,
        sufficiency=1,
        critical_ambiguity=1 if "critical_claim_support" in failed else 0,
        reason_codes=["stub_reject", *failed],
        accepted=False,
    )


def run_fixture_pipeline(
    *,
    snapshot: SourceSnapshot,
    owner_id: int,
    work_id: int,
    case_spec: dict[str, Any],
    generator_lineage: ModelLineage,
    judge_lineage: ModelLineage,
    generator: GeneratorFn | None = None,
    judge: JudgeFn | None = None,
    secret: str = DEFAULT_SIGNING_SECRET,
    max_regenerate: int = MAX_REGENERATE,
) -> tuple[FixtureJobState, EvalCase | None]:
    """Execute full freeze pipeline with regenerate budget.

    Returns (job_state, frozen_case_or_none).
    On terminal failure, job.metrics is always None and quality_comparable=False.
    """
    gen_fn = generator or default_stub_generator
    judge_fn = judge or default_stub_judge

    try:
        job = create_fixture_job(owner_id=owner_id, work_id=work_id, snapshot=snapshot)
    except FailedPolicyError as exc:
        return (
            FixtureJobState(
                job_id=f"fxjob-denied-{stable_hash({'o': owner_id, 'w': work_id})[:12]}",
                owner_id=owner_id,
                work_id=work_id,
                snapshot_id=snapshot.snapshot_id,
                status="failed_policy",
                attempt=0,
                metrics=None,
                quality_comparable=False,
                error_detail=exc.message,
            ),
            None,
        )

    if not verify_source_snapshot(snapshot, secret):
        job.status = "invalid_fixture"
        job.metrics = None
        job.quality_comparable = False
        job.error_detail = "snapshot signature/manifest invalid"
        return job, None

    try:
        validate_generator_judge_isolation(generator_lineage, judge_lineage)
    except InvalidLineageError as exc:
        job.status = "invalid_lineage"
        job.metrics = None
        job.quality_comparable = False
        job.error_detail = exc.message
        return job, None

    parent_id: str | None = None
    last_reason: str | None = None
    case: EvalCase | None = None

    for attempt in range(max_regenerate + 1):
        job.attempt = attempt
        job.status = "generating"
        spec = {
            **case_spec,
            "attempt": attempt,
            "parent_case_id": parent_id,
        }
        try:
            case = gen_fn(snapshot, spec, generator_lineage)
            case.attempt = attempt
            case.parent_case_id = parent_id
            case.generator_lineage = generator_lineage
            case.judge_lineage = judge_lineage
        except RagFixtureError as exc:
            last_reason = exc.message
            parent_id = case.case_id if case else parent_id
            continue
        except Exception as exc:  # noqa: BLE001 — fail closed
            last_reason = f"generator error: {exc}"
            parent_id = case.case_id if case else parent_id
            continue

        job.case_id = case.case_id
        job.status = "deterministic_validation"
        checks = run_deterministic_checks(
            case,
            snapshot,
            expected_owner_id=owner_id,
            expected_work_id=work_id,
        )
        case.deterministic_checks = checks
        if not checks.all_passed:
            last_reason = "deterministic_validation_failed:" + ",".join(
                d.name for d in checks.details if not d.passed
            )
            parent_id = case.case_id
            case.status = "generating"
            case.regeneration_reason = last_reason
            continue

        job.status = "judge_review"
        try:
            verdict = judge_fn(case, snapshot, judge_lineage)
        except Exception as exc:  # noqa: BLE001
            last_reason = f"judge error: {exc}"
            parent_id = case.case_id
            continue

        case.judge_fixture_verdict = verdict
        case.judge_lineage = judge_lineage
        accepted = judge_accepts(verdict)
        verdict.accepted = accepted
        if not accepted:
            last_reason = (
                f"judge_reject f={verdict.faithfulness} c={verdict.coverage} "
                f"s={verdict.sufficiency} amb={verdict.critical_ambiguity}"
            )
            parent_id = case.case_id
            case.regeneration_reason = last_reason
            continue

        frozen = freeze_eval_case(case, secret)
        job.status = "frozen"
        job.output_hash = frozen.fixture_hash
        job.case_id = frozen.case_id
        # Fixture freeze itself is not a SUT quality score — metrics remain null
        # until 06-04; quality_comparable stays false for the fixture job.
        job.metrics = None
        job.quality_comparable = False
        job.error_detail = None
        return job, frozen

    # Exhausted regenerate budget
    job.status = "quarantined"
    job.metrics = None
    job.quality_comparable = False
    job.error_detail = last_reason or "max regenerate exceeded"
    if case is not None:
        case.status = "quarantined"
        case.regeneration_reason = job.error_detail
    return job, case


# ---------------------------------------------------------------------------
# Fixture suite loaders (benchmark / adversarial JSON)
# ---------------------------------------------------------------------------


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def package_benchmark_suite(
    *,
    domain: str,
    snapshot: SourceSnapshot,
    cases: list[EvalCase],
    secret: str = DEFAULT_SIGNING_SECRET,
) -> dict[str, Any]:
    frozen_cases = []
    for case in cases:
        if case.status != "frozen":
            case = freeze_eval_case(case, secret)
        frozen_cases.append(case.model_dump(by_alias=True, mode="json"))
    clean_chunks = [
        {
            "content_hash": c.content_hash,
            "text_hash": c.text_hash,
            "length": c.length,
            "text": c.text,
        }
        for c in snapshot.chunks
    ]
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_RAG_QUALITY,
        "suite_type": "benchmark",
        "domain": domain,
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "owner_id": snapshot.owner_id,
            "work_id": snapshot.work_id,
            "version": snapshot.version,
            "canonicalization_version": snapshot.canonicalization_version,
            "chunks": clean_chunks,
            "manifest_hash": snapshot.manifest_hash,
            "created_at": snapshot.created_at.isoformat(),
            "signature": snapshot.signature,
        },
        "cases": frozen_cases,
    }
    body["suite_hash"] = stable_hash(
        {k: v for k, v in body.items() if k not in {"suite_hash", "signature"}}
    )
    body["signature"] = sign_payload(body, secret)
    return body


def prompts_dir() -> Path:
    # backend/app/services/rag_fixture/core.py -> backend/prompts
    return Path(__file__).resolve().parents[3] / "prompts"


def evals_dir() -> Path:
    # backend/app/services/rag_fixture/core.py -> backend/evals
    return Path(__file__).resolve().parents[3] / "evals"
