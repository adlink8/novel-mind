"""Phase 37-05 integration test: SkillRun → ToolRun → DraftArtifact → divergence
approval → revalidation → separate publish approval → deterministic publication.

Prove Phase 37 deterministic constrained derivative generation capability
(D-37-01..D-37-05 / REQ-FORK-03 + REQ-FORK-06 + REQ-AGENT-02/03/04/07) is
consumed through the versioned continue-derivative-story Skill and that the
Agent cannot bypass approval / publication authority:

Positive chain:
  register (versioned manifest: 9-tool allowlist = 7 read +
  allow_divergence / publish_derivative_revision actions + empty
  write_permissions + both actions in approval_required_for) → accept run
  (owner/novel/branch + input_hash binding) → deterministic generation job
  (sealed package + fixture provider candidate) → candidate row with a gate
  verdict → stub loop calls the real facade action tools →
  finalize writes the candidate DraftArtifact (with ContinuityReport and
  disabled-by-default BranchSuggestion[]) → for a divergence candidate:
  allow_divergence ApprovalRequest (bound to exact draft_hash +
  canon_delta_hash) → user confirms → full revalidation → separate
  publish_derivative_revision ApprovalRequest (identical hash binding) →
  user confirms → deterministic publisher (consume_publish_approval →
  approve_override) materializes the Fanfiction Canon derivative revision.
  BranchSuggestion is disabled-by-default, never auto-forks and never grants/
  reuses either approval.

Adversarial paths (all stable blocked/cancelled with zero authoritative writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (status non-candidate, BranchSuggestion
  enabled/default or missing field), wrong branch, forged/stale/pending/
  reused approval, skipped allow_divergence step, draft/canon-delta hash
  mismatch, revalidation failure and Original-authority mutation attempts.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, undefer
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models import Chapter, Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import DerivativeGenerationCandidate
from app.models.derivative_override import DerivativeOverride
from app.models.derivative_revision import DerivativeRevision
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.approvals import confirm
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    finalize_skill_run,
)
from app.services.agent_runtime.registry import (
    SkillContractError,
    canonical_input_hash,
    register_skill_version,
)
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.agent_tools.errors import InvalidInputError
from app.services.agent_tools.facade import ToolFacade
from app.services.canon_fork.snapshot import (
    ForkChapterRecord,
    compute_source_snapshot_hash,
)
from app.services.derivative_editor.chapters import create_chapter
from app.services.derivative_editor.projects import create_project
from app.services.derivative_generation.context_package import (
    budget_verdict,
    package_hash,
)
from app.services.derivative_generation.fixtures import (
    CANDIDATE_EVIDENCE_1,
    CANDIDATE_EVIDENCE_2,
    OUTSIDE_EVIDENCE,
    build_candidate_json,
    build_package,
)
from app.services.derivative_generation.agent_boundary import (
    ALLOW_DIVERGENCE_APPROVAL_ACTION,
    PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION,
    consume_publish_approval,
    divergence_approval_payload_hash,
    draft_hash_for_candidate,
)
from app.services.derivative_generation.overrides import OverrideError, override_hash
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 37 编排 allowlist：7 个只读域工具 + 2 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "allow_divergence",
    "publish_derivative_revision",
]
APPROVAL_ACTIONS = ["allow_divergence", "publish_derivative_revision"]

# Deterministic chapter texts; source snapshot hash replays from them.
CHAPTER_TEXTS = {1: "chapter 1 body", 2: "chapter 2 body", 3: "chapter 3 body"}
INITIAL_MARKDOWN = "# Draft\nAurora stands at the gate."
DIVERGENT_DRAFT = "为故事转折，阿宁在结尾露出他其实早已知晓秘密的神情。"
CLEAN_DRAFT = "阿宁在竹林入口迟疑片刻，终于迈步。"
FORK_KEY = "fork-aurora"
DELTA_KEY = "delta-aurora-01"
PACKAGE_KEY = "ctx:fork-aurora:continuation:2"

# Frozen BranchSuggestion fixture（六字段 + enabled_by_default=false，D-37-05）。
HEX64 = "a" * 64
BRANCH_SUGGESTION = [
    {
        "choice_text": "阿宁循着脚印深入竹林",
        "branch_summary": "继续追踪金色脚印，进入竹林深处",
        "triggering_conflict": "脚印在竹林深处消失",
        "canon_delta_hash": HEX64,
        "evidence_refs": [CANDIDATE_EVIDENCE_1],
        "enabled_by_default": False,
    }
]

DIVERGENCE = {
    "divergence_type": "character",
    "reason": "the twist requires the hero to know the secret early",
    "affected_evidence": [CANDIDATE_EVIDENCE_1],
    "scope": "derivative",
}


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(
    *, novel_id: int, name: str, tools: list[str], **overrides: Any
) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
        "version": "1.0.0",
        "allowed_tools": list(tools),
        "read_permissions": ["canon", "fanfiction_canon"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "derivative_generation:write",
            "approval_request",
            "revision_service",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        # Phase 37：两个 action 各自要求独立 Web ApprovalRequest（D-11/D-15）。
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "project_id": {"type": "integer"},
                "chapter_id": {"type": "integer"},
                "intent": {"type": "string"},
                "context_package_id": {"type": "integer"},
                "evidence_refs": {"type": "array"},
                "requested_actions": {"type": "array"},
            },
            "required": [
                "novel_id",
                "project_id",
                "chapter_id",
                "intent",
                "context_package_id",
                "evidence_refs",
                "requested_actions",
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "derivative_draft"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + 3 original chapters; return the source snapshot hash."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p37_{suffix}",
            email=f"p37_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"P37 Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=len(CHAPTER_TEXTS),
            word_count=sum(len(text) for text in CHAPTER_TEXTS.values()),
        )
        session.add(novel)
        session.flush()
        records: list[ForkChapterRecord] = []
        for number, content in sorted(CHAPTER_TEXTS.items()):
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=number,
                title=f"C{number}",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            records.append(
                ForkChapterRecord(
                    chapter_id=chapter.id,
                    chapter_number=number,
                    content=content,
                )
            )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=user.id,
            novel_id=novel.id,
            chapters=tuple(records),
        )
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "token": create_access_token({"sub": str(user.id)}),
            "source_snapshot_hash": snapshot_hash,
            "contents": list(CHAPTER_TEXTS.values()),
        }
    engine.dispose()
    return data


class _FixtureTransport:
    """Deterministic provider transport returning the frozen candidate JSON."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": "fixture-req-1",
            "content": self._content,
            "usage": {"prompt_tokens": 120, "completion_tokens": 60},
        }


def _run_input(ids: dict[str, Any], *, branch: str | None = None) -> dict[str, Any]:
    return {
        "novel_id": ids["novel_id"],
        "branch": branch,
        "fork": "fork-1" if branch else None,
        "project_id": ids["project_id"],
        "chapter_id": ids["chapter_id"],
        "chapter_number": ids.get("chapter_number", 1),
        "intent": "continuation",
        "context_package_id": ids["context_package_id"],
        "source_snapshot_id": f"novel:{ids['novel_id']}:fork-1",
        "source_snapshot_hash": ids["source_snapshot_hash"],
        "evidence_refs": [CANDIDATE_EVIDENCE_1],
        "requested_actions": ["allow_divergence", "publish_derivative_revision"],
    }


async def _register_skill(
    factory, *, owner_id: int, novel_id: int, contract: SkillVersionRegister
) -> int:
    async with factory() as session:
        _, version = await register_skill_version(
            session, owner_id=owner_id, novel_id=novel_id, contract=contract
        )
        await session.commit()
        return version.id


async def _create_run(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    input_data: dict[str, Any],
    branch: str | None = None,
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=branch,
            input=input_data,
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 40},
            cancel_requested=cancel_requested,
        )
        session.add(run)
        await session.commit()
        return run.id


async def _finalize(
    factory,
    *,
    run_id: int,
    envelope: dict[str, Any],
    frozen_manifest: dict[str, Any] | None = None,
    stop_reason: str = "stop",
):
    return await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason=stop_reason,
        envelope=envelope,
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions=dict(envelope.get("source_versions") or {}),
        usage={
            "calls": 4,
            "input_tokens": 600,
            "output_tokens": 300,
            "cost_usd": "0.0015",
        },
        frozen_manifest=frozen_manifest,
    )


async def _count(
    factory, model, *, run_id: int | None = None, owner_id: int | None = None
) -> int:
    async with factory() as session:
        if owner_id is not None:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.owner_id == owner_id)  # type: ignore[attr-defined]
                )
                or 0
            )
        if run_id is None:
            return int(
                await session.scalar(select(func.count()).select_from(model)) or 0
            )
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.run_id == run_id)  # type: ignore[attr-defined]
            )
            or 0
        )


async def _count_revisions(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ArtifactRevision)
                .join(Artifact, ArtifactRevision.artifact_id == Artifact.id)
                .where(Artifact.run_id == run_id)
            )
            or 0
        )


async def _count_approvals(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
            )
            or 0
        )


async def _count_derivative_rows(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(DerivativeRevision)
                .where(DerivativeRevision.owner_id == owner_id)
            )
            or 0
        )


async def _count_forks(factory, *, owner_id: int) -> int:
    from app.models.canon_fork import CanonFork

    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CanonFork)
                .where(CanonFork.owner_id == owner_id)
            )
            or 0
        )


async def _count_overrides(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(DerivativeOverride)
                .where(DerivativeOverride.owner_id == owner_id)
            )
            or 0
        )


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count(factory, Artifact, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _build_envelope(
    ctx: dict[str, Any], *, candidate: Any, mutate=None
) -> dict[str, Any]:
    """Build a canonical DraftArtifact envelope from the candidate row + run lineage.

    ``candidate`` is the persisted DerivativeGenerationCandidate whose frozen
    output the agent re-emits as the DraftArtifact ``draft`` payload. The
    BranchSuggestion list (draft + top-level) is the candidate's
    disabled-by-default output (D-37-05).

    ``mutate`` (optional) is applied **before** the 26-06 normalization trail is
    computed, so an adversarial mutation keeps a replay-consistent trail and the
    failure lands on the intended gate rather than on ``repaired_hash``.
    """
    divergence = dict(candidate.divergence or {}) if candidate.divergence else None
    branch_suggestions = list(candidate.branch_suggestions or [])
    evidence_refs = [CANDIDATE_EVIDENCE_1]
    if divergence:
        evidence_refs.extend(divergence.get("affected_evidence") or [])
    for suggestion in branch_suggestions:
        evidence_refs.extend(suggestion.get("evidence_refs") or [])
    evidence_refs = sorted(set(evidence_refs))
    draft: dict[str, Any] = {
        "schema_version": "derivative-candidate.v1",
        "artifact_kind": "derivative_draft",
        "authority_space": "derivative",
        "intent": candidate.intent,
        "draft_text": candidate.draft_text,
        "summary": candidate.summary,
        "citation_keys": list(candidate.citation_keys or []),
        "divergence": divergence,
        "branch_suggestions": branch_suggestions,
        "fork": "fork-1",
        "source_snapshot_id": f"novel:{ctx['novel_id']}:fork-1",
        "source_snapshot_hash": ctx["source_snapshot_hash"],
        "package_hash": candidate.package_hash,
        "manifest_hash": HEX64,
        "draft_hash": ctx["draft_hash"],
        "canon_delta_hash": candidate.canon_delta_hash,
    }
    verdict = candidate.gate_verdict
    envelope: dict[str, Any] = {
        "type": "derivative_draft",
        "schema_version": "draft-artifact.v1",
        "owner_id": ctx["owner_id"],
        "novel_id": ctx["novel_id"],
        "branch": ctx["branch"],
        "producing_skill": "continue-derivative-story",
        "producing_skill_version": "1.0.0",
        "skill_version_id": ctx["skill_version_id"],
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": ctx["source_snapshot_hash"],
        },
        "input_hash": ctx["input_hash"],
        "evidence_refs": evidence_refs,
        "draft": draft,
        "continuity_report": {
            "verdict": verdict,
            "reason": candidate.gate_reason,
            "detail": f"gate verdict {verdict} for candidate {candidate.id}",
            "violations": [],
            "branch_suggestions": branch_suggestions,
        },
        "branch_suggestions": branch_suggestions,
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "get_chapter", "calls": 1},
            {"tool_name": "allow_divergence", "calls": 1},
            {"tool_name": "publish_derivative_revision", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    if mutate is not None:
        mutate(envelope)
    # 26-06 trail：repaired_hash 是对不含 trail 的 payload 的 canonical SHA-256。
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


def _divergence_params(
    ctx: dict[str, Any],
    *,
    reason: str = "the twist requires the hero to know the secret early",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": ctx["branch"],
        "fork": "fork-1",
        "project_id": ctx["project_id"],
        "chapter_id": ctx["chapter_id"],
        "candidate_id": ctx["candidate_id"],
        "reason": reason,
        "affected_evidence": [CANDIDATE_EVIDENCE_1],
        "kind": None,
        "draft_hash": ctx["draft_hash"],
        "canon_delta_hash": ctx["canon_delta_hash"],
        "run_id": ctx["run_id"],
        "skill_version_id": ctx["skill_version_id"],
    }
    base.update(overrides)
    return base


def _publish_params(
    ctx: dict[str, Any], *, override_id: int, **overrides: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": ctx["branch"],
        "fork": "fork-1",
        "override_id": override_id,
        "draft_hash": ctx["draft_hash"],
        "canon_delta_hash": ctx["canon_delta_hash"],
        "approval_note": "publish the approved divergence",
        "run_id": ctx["run_id"],
        "skill_version_id": ctx["skill_version_id"],
    }
    base.update(overrides)
    return base


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(runtime_factory):
    """ASGI client bound to the module-migrated PostgreSQL (head incl. 37-05)."""

    async def override_get_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def _create_context_package(
    factory, *, owner_id: int, novel_id: int, fork_id: int
) -> int:
    """Seal a ContextPackageRecord bound to the test fork (fixture dimensions)."""
    pkg = build_package(fork_id=fork_id)
    pkg["owner_id"] = owner_id
    pkg["novel_id"] = novel_id
    pkg["fork_key"] = "fork-aurora"
    pkg["budget_estimate"] = budget_verdict(pkg)
    sealed = package_hash(pkg)
    version = pkg["version"]
    async with factory() as session:
        row = ContextPackageRecord(
            owner_id=owner_id,
            novel_id=novel_id,
            fork_id=fork_id,
            package_key=PACKAGE_KEY,
            space="fanfiction_canon",
            intent="continuation",
            fork_key="fork-aurora",
            source_version_key=version["source_version_key"],
            source_snapshot_hash=version["source_snapshot_hash"],
            through_chapter=version["through_chapter"],
            full_book_authorized=version["full_book_authorized"],
            cutoff_snapshot_hash=version["cutoff_snapshot_hash"],
            scope_hash=version["scope_hash"],
            manifest_hash=version["manifest_hash"],
            canonical_payload=pkg,
            budget_estimate=pkg["budget_estimate"],
            package_hash=sealed,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _create_candidate(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    context_package_id: int,
    candidate_content: str,
    job_key: str,
) -> tuple[DerivativeGenerationCandidate, str, str]:
    """Create + run one budgeted generation job; return (candidate, draft_hash, canon_delta_hash)."""
    from app.api.derivative_generation import DerivativeGenerationJobService
    from app.services.derivative_generation.runner import (
        DEFAULT_DERIVATIVE_BUDGET,
        DerivativeBudgetGate,
    )

    async with factory() as session:
        service = DerivativeGenerationJobService(
            session,
            transport=_FixtureTransport(candidate_content),
            budget_gate=DerivativeBudgetGate(DEFAULT_DERIVATIVE_BUDGET),
        )
        job, _ = await service.create_job(
            owner_id=owner_id,
            novel_id=novel_id,
            context_package_id=context_package_id,
            intent="continuation",
            job_key=job_key,
        )
        result = await service.run_job(
            owner_id=owner_id, novel_id=novel_id, job_id=job.id
        )
        await session.commit()
    assert result.candidate is not None, result.status
    candidate = result.candidate
    draft_hash = draft_hash_for_candidate(candidate)
    return candidate, draft_hash, candidate.canon_delta_hash


async def _set_up(
    runtime_factory, sync_url: str, *, suffix: str, candidate_content: str
) -> dict[str, Any]:
    """seed owner/novel/chapters + fork + project + chapter + package + skill + run + candidate."""
    ids = _seed(sync_url, suffix=suffix)
    branch_value = "deriv-branch"
    # 1. candidate canon fork via the real facade action tool.
    async with runtime_factory() as session:
        novel = await session.get(Novel, ids["novel_id"])
        fork_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ids["owner_id"],
            params={
                "branch": branch_value,
                "fork": "fork-1",
                "fork_key": f"{FORK_KEY}-{suffix}",
                "requested_cutoff_chapter": 2,
                "full_book_requested": False,
                "expected_source_snapshot_hash": ids["source_snapshot_hash"],
                "delta_key": f"{DELTA_KEY}-{suffix}",
                "delta_content": DIVERGENT_DRAFT,
                "delta_evidence_refs": [CANDIDATE_EVIDENCE_1, CANDIDATE_EVIDENCE_2],
            },
        )
        await session.commit()
    fork_id = int(fork_view["fork_id"])

    # 2. derivative project bound to the fork (frozen fanfiction_canon lineage).
    async with runtime_factory() as session:
        project_view = await create_project(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            fork_id=fork_id,
            name=f"Proj {suffix}",
        )
        await session.commit()
    ids["project_id"] = project_view.id
    ids["source_snapshot_hash"] = project_view.source_snapshot_hash

    # 3. one derivative chapter (root revision 1).
    async with runtime_factory() as session:
        chapter_view, _scope = await create_chapter(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=project_view.id,
            title="T1",
            markdown=INITIAL_MARKDOWN,
        )
        await session.commit()
    ids["chapter_id"] = chapter_view.id
    ids["chapter_number"] = chapter_view.position + 1

    # 4. sealed context package bound to the fork.
    ids["context_package_id"] = await _create_context_package(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        fork_id=fork_id,
    )

    # 5. register the versioned continue-derivative-story skill.
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"],
            name="continue-derivative-story",
            tools=DEFAULT_TOOLS,
        ),
    )

    # 6. accept the run.
    run_input = _run_input(ids, branch=branch_value)
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
        input_data=run_input,
        branch=branch_value,
    )
    ids.update(
        {
            "skill_version_id": svid,
            "run_id": run_id,
            "input_hash": input_hash,
            "run_input": run_input,
            "branch": branch_value,
            "fork_id": fork_id,
        }
    )

    # 7. deterministic generation job -> candidate row (candidate|blocked|needs_override).
    candidate, draft_hash, canon_delta_hash = await _create_candidate(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        context_package_id=ids["context_package_id"],
        candidate_content=candidate_content,
        job_key=f"job-{suffix}",
    )
    ids.update(
        {
            "candidate_id": candidate.id,
            "candidate": candidate,
            "gate_verdict": candidate.gate_verdict,
            "draft_hash": draft_hash,
            "canon_delta_hash": canon_delta_hash,
        }
    )
    return ids


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase37_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 continue-derivative-story manifest 注册成功：9 工具 allowlist（7 只读 +
    2 action）+ 零写权限 + approval_required_for 恰为两个 action。"""
    seed = _seed(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="continue-derivative-story",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "continue-derivative-story"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "allow_divergence" in version.allowed_tools
        assert "publish_derivative_revision" in version.allowed_tools
        assert version.write_permissions == []
        assert set(version.approval_required_for) == set(APPROVAL_ACTIONS)
        assert version.forbidden_spaces == [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "derivative_generation:write",
            "approval_request",
            "revision_service",
        ]
        assert "canon" in version.read_permissions
        assert "fanfiction_canon" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase37_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（publish_derivative_directly）→ 注册拒绝，零 active 行。"""
    seed = _seed(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="continue-derivative-story",
        tools=list(DEFAULT_TOOLS) + ["publish_derivative_directly"],
    )
    with pytest.raises(SkillContractError):
        await _register_skill(
            runtime_factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            contract=contract,
        )
    async with runtime_factory() as session:
        registry_count = await session.scalar(
            select(func.count())
            .select_from(SkillRegistry)
            .where(SkillRegistry.owner_id == seed["owner_id"])
        )
    assert int(registry_count or 0) == 0


# ────────────────────────── Task 2：candidate → DraftArtifact → BranchSuggestion ──────────────────────────


async def test_phase37_clean_candidate_draft_artifact_and_disabled_branch_suggestion(
    runtime_factory, migrated_postgres: str
):
    """正向（无 divergence）：确定性 runner 产出 candidate → finalize 写入 candidate
    DraftArtifact + ContinuityReport（verdict=candidate）+ disabled-by-default
    BranchSuggestion（六字段）。BranchSuggestion 不自动 fork、不写 Original。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            branch=BRANCH_SUGGESTION,
        ),
    )
    assert ctx["gate_verdict"] == "candidate"
    run_id = ctx["run_id"]

    frozen_manifest = {"evidence_refs": [CANDIDATE_EVIDENCE_1]}
    envelope = _build_envelope(ctx, candidate=ctx["candidate"])
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_id is not None
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    assert artifact is not None and artifact.type == "derivative_draft"
    assert artifact.schema_version == "draft-artifact.v1"
    assert artifact.status == "candidate"
    content = revision.content
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    assert content["type"] == "derivative_draft"
    assert content["owner_id"] == ctx["owner_id"]
    assert content["input_hash"] == ctx["input_hash"]
    assert content["draft"]["authority_space"] == "derivative"
    assert content["draft"]["fork"] == "fork-1"
    assert content["continuity_report"]["verdict"] == "candidate"
    # BranchSuggestion 六字段 + enabled_by_default=false（D-37-05）。
    for source in (
        content["draft"]["branch_suggestions"],
        content["continuity_report"]["branch_suggestions"],
        content["branch_suggestions"],
    ):
        assert len(source) == 1
        suggestion = source[0]
        assert set(suggestion.keys()) == {
            "choice_text",
            "branch_summary",
            "triggering_conflict",
            "canon_delta_hash",
            "evidence_refs",
            "enabled_by_default",
        }
        assert suggestion["enabled_by_default"] is False
    # candidate-only：不自动 fork、不写 Original。
    assert await _count_forks(runtime_factory, owner_id=ctx["owner_id"]) == 1
    async with runtime_factory() as session:
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())


async def test_phase37_divergence_approval_revalidate_publish_sequence(
    runtime_factory, migrated_postgres: str
):
    """正向 divergence 链：allow_divergence approval（绑定 exact draft_hash +
    canon_delta_hash）→ finalize candidate DraftArtifact → 用户确认 → 完整
    revalidation → 独立 publish_derivative_revision approval（相同 hash 绑定）→
    用户确认 → 确定性 publisher 物化 Fanfiction Canon revision。两个 approval
    payload_hash 相同；绝不写 Original。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"div_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    assert ctx["gate_verdict"] == "needs_override"
    run_id = ctx["run_id"]

    # stub agent loop：真实调用 allow_divergence action 工具。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == ALLOW_DIVERGENCE_APPROVAL_ACTION
    assert tool_view["approval_status"] == "pending"
    assert tool_view["canon_delta_hash"] == ctx["canon_delta_hash"]
    override_id = int(tool_view["override_id"])
    divergence_approval_id = int(tool_view["approval_request_id"])
    approval_payload_hash = str(tool_view["approval_payload_hash"])
    assert approval_payload_hash == divergence_approval_payload_hash(
        draft_hash=ctx["draft_hash"], canon_delta_hash=ctx["canon_delta_hash"]
    )

    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, divergence_approval_id)
        override = await session.get(DerivativeOverride, override_id)
    assert approval is not None and approval.status == "pending"
    assert approval.action == ALLOW_DIVERGENCE_APPROVAL_ACTION
    assert override is not None and override.approval_state == "pending"
    assert override.canon_delta_hash == ctx["canon_delta_hash"]

    # finalize 写入 candidate DraftArtifact（verdict=needs_override）。
    frozen_manifest = {"evidence_refs": [CANDIDATE_EVIDENCE_1]}
    envelope = _build_envelope(ctx, candidate=ctx["candidate"])
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    async with runtime_factory() as session:
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    content = revision.content
    assert content["continuity_report"]["verdict"] == "needs_override"
    assert content["draft"]["divergence"]["divergence_type"] == "character"
    assert content["draft"]["draft_hash"] == ctx["draft_hash"]

    # 用户 Web 确认 allow_divergence approval。
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=divergence_approval_id,
            owner_id=ctx["owner_id"],
            mode="once",
        )
        await session.commit()

    # 独立 publish approval（revalidation 通过 + 相同 hash 绑定）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        publish_view = await ToolFacade().execute(
            "publish_derivative_revision",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx, override_id=override_id),
        )
        await session.commit()
    assert publish_view["candidate_only"] is True
    assert (
        publish_view["approval_action"] == PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION
    )
    assert publish_view["approval_status"] == "pending"
    assert publish_view["divergence_approval_id"] == divergence_approval_id
    assert publish_view["divergence_approval_status"] == "approved"
    # 相同 hash 绑定：两个 approval payload_hash 完全一致（D-37-03）。
    assert publish_view["approval_payload_hash"] == approval_payload_hash
    publish_approval_id = int(publish_view["approval_request_id"])

    # 用户 Web 确认独立 publish approval → 确定性 publisher 物化。
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=publish_approval_id,
            owner_id=ctx["owner_id"],
            mode="once",
        )
        result = await consume_publish_approval(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            override_id=override_id,
            publish_approval_id=publish_approval_id,
            approval_note="publish the approved divergence",
            actor_id=ctx["owner_id"],
        )
        await session.commit()
    assert result.status == "applied"
    published = result.published
    assert published.status == "derivative_revision"
    assert published.owner_id == ctx["owner_id"]
    assert published.project_id == ctx["project_id"]
    assert published.fork_id == ctx["fork_id"]
    assert published.review["canon_delta_hash"] == ctx["canon_delta_hash"]
    assert published.approval["approval_state"] == "approved"

    # append-only lineage：root create + divergence override revision 两行。
    async with runtime_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(DerivativeRevision)
                    .where(DerivativeRevision.owner_id == ctx["owner_id"])
                    .order_by(DerivativeRevision.revision_number)
                )
            ).all()
        )
    assert len(rows) == 2
    assert rows[1].kind == "agent_proposal"
    assert rows[1].approval_state == "approved"
    # Original 零变更。
    async with runtime_factory() as session:
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())


# ────────────────────────── 对抗路径（fail closed，零权威写入） ──────────────────────────


async def test_phase37_http_action_routes_wired(
    runtime_factory, migrated_postgres: str, api_client
):
    """HTTP 路由连通：POST /api/agent-tools/allow_divergence 经 require_owned_novel
    注入 owner/novel 后创建 pending override + approval（candidate-only）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"http_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        "/api/agent-tools/allow_divergence",
        params={"novel_id": ctx["novel_id"]},
        headers=headers,
        json=_divergence_params(ctx),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_only"] is True
    assert body["approval_action"] == ALLOW_DIVERGENCE_APPROVAL_ACTION
    assert body["approval_status"] == "pending"
    assert body["canon_delta_hash"] == ctx["canon_delta_hash"]
    assert body["approval_payload_hash"] == divergence_approval_payload_hash(
        draft_hash=ctx["draft_hash"], canon_delta_hash=ctx["canon_delta_hash"]
    )


async def test_phase37_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"cancel_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        input_data=ctx["run_input"],
        branch=ctx["branch"],
        cancel_requested=True,
    )
    envelope = _build_envelope(ctx, candidate=ctx["candidate"])
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase37_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"own_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    envelope = _build_envelope(
        ctx,
        candidate=ctx["candidate"],
        mutate=lambda e: e.__setitem__("owner_id", ctx["owner_id"] + 999),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ver_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    envelope = _build_envelope(
        ctx,
        candidate=ctx["candidate"],
        mutate=lambda e: e.__setitem__(
            "skill_version_id", ctx["skill_version_id"] + 999
        ),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"hash_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    envelope = _build_envelope(
        ctx,
        candidate=ctx["candidate"],
        mutate=lambda e: e.__setitem__("input_hash", "9" * 64),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_schema_drift_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """schema drift：DraftArtifact status 非 candidate（直接发布伪造）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    envelope = _build_envelope(
        ctx,
        candidate=ctx["candidate"],
        mutate=lambda e: e.__setitem__("status", "published"),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "status" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_branch_suggestion_enabled_blocks(
    runtime_factory, migrated_postgres: str
):
    """BranchSuggestion enabled_by_default=true（D-37-05 默认禁用）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"bs_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            branch=BRANCH_SUGGESTION,
        ),
    )
    enabled = [dict(s, enabled_by_default=True) for s in BRANCH_SUGGESTION]

    def _enable(e):
        e["draft"]["branch_suggestions"] = enabled
        e["branch_suggestions"] = enabled

    envelope = _build_envelope(ctx, candidate=ctx["candidate"], mutate=_enable)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert (
        outcome.status_reason is not None
        and "enabled_by_default" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_branch_suggestion_missing_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """BranchSuggestion 缺六字段之一（triggering_conflict）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"bs2_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            branch=BRANCH_SUGGESTION,
        ),
    )
    broken = {
        k: v for k, v in BRANCH_SUGGESTION[0].items() if k != "triggering_conflict"
    }

    def _drop(e):
        e["draft"]["branch_suggestions"] = [broken]
        e["branch_suggestions"] = [broken]

    envelope = _build_envelope(ctx, candidate=ctx["candidate"], mutate=_drop)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert (
        outcome.status_reason is not None
        and "triggering_conflict" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_wrong_branch_blocks(runtime_factory, migrated_postgres: str):
    """wrong branch：run 绑定 derivative 分支，envelope 声称别的分支（branch 血缘
    不符）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )

    def _wrong_branch(e):
        e["branch"] = "other-branch"
        e["draft"]["fork"] = "fork-other"

    envelope = _build_envelope(ctx, candidate=ctx["candidate"], mutate=_wrong_branch)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_evidence_outside_envelope_blocks(
    runtime_factory, migrated_postgres: str
):
    """draft citation 越出 envelope evidence_refs（leaf-evidence 资格门）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ev_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1, CANDIDATE_EVIDENCE_2]
        ),
    )
    # 保留一个 citation 在 envelope 之外（envelope evidence_refs 只含 EVIDENCE_1）。
    envelope = _build_envelope(
        ctx,
        candidate=ctx["candidate"],
        mutate=lambda e: e.__setitem__("evidence_refs", [CANDIDATE_EVIDENCE_1]),
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [CANDIDATE_EVIDENCE_1]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "evidence" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase37_publish_without_divergence_approval_fails(
    runtime_factory, migrated_postgres: str
):
    """跳过 allow_divergence approval（未批准）→ publish tool fail closed（skipped step）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"skip_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    override_id = int(tool_view["override_id"])
    # 未确认 allow_divergence approval 就请求 publish → fail closed。
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_revision",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(ctx, override_id=override_id),
            )
    assert "approval_not_approved" in str(exc.value)
    async with runtime_factory() as session:
        pending = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.action
                    == PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION,
                    ApprovalRequest.owner_id == ctx["owner_id"],
                )
            )
        ).all()
    assert len(pending) == 0
    # 零权威写入：无发布 approval、无 revision。
    assert await _count_derivative_rows(runtime_factory, owner_id=ctx["owner_id"]) == 1


async def test_phase37_draft_hash_mismatch_fails(
    runtime_factory, migrated_postgres: str
):
    """allow_divergence 携带错误 draft_hash（候选血缘不重放）→ fail closed，无 override。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"dhash_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "allow_divergence",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_divergence_params(ctx, draft_hash="b" * 64),
            )
    assert "draft_hash" in str(exc.value)
    assert await _count_overrides(runtime_factory, owner_id=ctx["owner_id"]) == 0
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase37_revalidation_failure_blocks_publish(
    runtime_factory, migrated_postgres: str
):
    """revalidation 失败：candidate 重新验证不是 needs_override（basic gates 仍
    blocked）→ publish tool fail closed，不创建 publish approval。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"rev_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft="阿宁在竹林里走得越来越深。",
            citations=[OUTSIDE_EVIDENCE],
        ),
    )
    assert ctx["gate_verdict"] == "blocked"
    # blocked 候选可创建 override（OVERRIDABLE_VERDICTS），但 revalidation 必然失败。
    # 该候选无 declared CanonDelta → canon_delta_hash 由服务端 override_hash 派生。

    expected_canon = override_hash(
        kind="character",
        reason="the twist requires the hero to know the secret early",
        affected_evidence=[CANDIDATE_EVIDENCE_1],
        package_hash=ctx["candidate"].package_hash,
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(
                ctx,
                kind="character",
                canon_delta_hash=expected_canon,
            ),
        )
        await session.commit()
    assert tool_view["approval_status"] == "pending"
    override_id = int(tool_view["override_id"])
    # 用工具返回的真实 canon_delta_hash 构造 publish 请求。
    real_canon_delta_hash = str(tool_view["canon_delta_hash"])
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=int(tool_view["approval_request_id"]),
            owner_id=ctx["owner_id"],
            mode="once",
        )
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_revision",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(
                    ctx,
                    override_id=override_id,
                    canon_delta_hash=real_canon_delta_hash,
                ),
            )
    assert "revalidation_failed" in str(exc.value)
    # 无 publish approval 创建；override 仍在 pending。
    async with runtime_factory() as session:
        publish_count = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(
                ApprovalRequest.action == PUBLISH_DERIVATIVE_REVISION_APPROVAL_ACTION,
                ApprovalRequest.owner_id == ctx["owner_id"],
            )
        )
        override = await session.get(DerivativeOverride, override_id)
    assert int(publish_count or 0) == 0
    assert override is not None and override.approval_state == "pending"


async def test_phase37_consume_publish_approval_pending_fails(
    runtime_factory, migrated_postgres: str
):
    """确定性 publisher 消费 pending publish approval（未确认）→ approval_not_approved，
    零权威写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"pend_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    override_id = int(tool_view["override_id"])
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=int(tool_view["approval_request_id"]),
            owner_id=ctx["owner_id"],
            mode="once",
        )
        publish_view = await ToolFacade().execute(
            "publish_derivative_revision",
            db=session,
            novel=await session.get(Novel, ctx["novel_id"]),
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx, override_id=override_id),
        )
        await session.commit()
    publish_approval_id = int(publish_view["approval_request_id"])
    # 未确认 publish approval 就调用确定性 publisher → fail closed。

    with pytest.raises(OverrideError) as exc:
        async with runtime_factory() as session:
            await consume_publish_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                override_id=override_id,
                publish_approval_id=publish_approval_id,
                approval_note="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None and chapter.revision == 1
    assert await _count_derivative_rows(runtime_factory, owner_id=ctx["owner_id"]) == 1


async def test_phase37_forged_publish_approval_hash_fails(
    runtime_factory, migrated_postgres: str
):
    """伪造 approval：确认 publish approval 后篡改 payload_hash（hash 绑定漂移）
    → 确定性 publisher fail closed，不物化。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"forge_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    override_id = int(tool_view["override_id"])
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=int(tool_view["approval_request_id"]),
            owner_id=ctx["owner_id"],
            mode="once",
        )
        publish_view = await ToolFacade().execute(
            "publish_derivative_revision",
            db=session,
            novel=await session.get(Novel, ctx["novel_id"]),
            owner_id=ctx["owner_id"],
            params=_publish_params(ctx, override_id=override_id),
        )
        await session.commit()
    publish_approval_id = int(publish_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=publish_approval_id,
            owner_id=ctx["owner_id"],
            mode="once",
        )
        approval = await session.get(ApprovalRequest, publish_approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希（伪造批准）
        await session.commit()

    with pytest.raises(OverrideError) as exc:
        async with runtime_factory() as session:
            await consume_publish_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                override_id=override_id,
                publish_approval_id=publish_approval_id,
                approval_note="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None and chapter.revision == 1


async def test_phase37_divergence_approval_not_reusable(
    runtime_factory, migrated_postgres: str
):
    """allow_divergence approval 不能复用为 publish approval：消费时 action 不符 →
    fail closed（不发布）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"reuse_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    override_id = int(tool_view["override_id"])
    divergence_approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session,
            request_id=divergence_approval_id,
            owner_id=ctx["owner_id"],
            mode="once",
        )
        await session.commit()

    # 直接把已批准的 allow_divergence approval 当 publish approval 消费 → action 不符。
    with pytest.raises(OverrideError) as exc:
        async with runtime_factory() as session:
            await consume_publish_approval(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                override_id=override_id,
                publish_approval_id=divergence_approval_id,
                approval_note="publish",
                actor_id=ctx["owner_id"],
            )
    assert exc.value.code == "approval_not_found"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None and chapter.revision == 1


async def test_phase37_rejected_divergence_blocks_publish(
    runtime_factory, migrated_postgres: str
):
    """allow_divergence approval 被拒绝（rejected）→ publish tool fail closed。"""
    from app.services.agent_runtime.approvals import reject

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    override_id = int(tool_view["override_id"])
    async with runtime_factory() as session:
        await reject(
            session,
            request_id=int(tool_view["approval_request_id"]),
            owner_id=ctx["owner_id"],
        )
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "publish_derivative_revision",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_publish_params(ctx, override_id=override_id),
            )
    assert "approval_not_approved" in str(exc.value)
    assert await _count_derivative_rows(runtime_factory, owner_id=ctx["owner_id"]) == 1


async def test_phase37_original_authority_untouched(
    runtime_factory, migrated_postgres: str
):
    """Original 权威零变更：章节正文不变；run 本身不创建任何 ApprovalRequest（只有
    action 工具创建）；divergence/publish 是独立 approval。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=CLEAN_DRAFT, citations=[CANDIDATE_EVIDENCE_1]
        ),
    )
    async with runtime_factory() as session:
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
        approvals_for_run = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == ctx["run_id"])
        )
        revisions_for_owner = await session.scalar(
            select(func.count())
            .select_from(DerivativeRevision)
            .where(DerivativeRevision.owner_id == ctx["owner_id"])
        )
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())
    assert int(approvals_for_run or 0) == 0
    assert int(revisions_for_owner or 0) == 1  # root create only


async def test_phase37_forbidden_tool_never_publishes(
    runtime_factory, migrated_postgres: str
):
    """forbidden Tool/action：allow_divergence / publish_derivative_revision 只创建
    override + pending approval（candidate-only），绝不发布 / 触碰 Original Canon。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"tool_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_status"] == "pending"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
        rows = (
            await session.scalars(
                select(DerivativeRevision).where(
                    DerivativeRevision.owner_id == ctx["owner_id"]
                )
            )
        ).all()
    assert chapter is not None and chapter.revision == 1
    assert all(row.kind != "agent_proposal" for row in rows)


async def test_phase37_allow_divergence_is_idempotent(
    runtime_factory, migrated_postgres: str
):
    """allow_divergence 幂等：重复 candidate + 相同 reason/evidence → 重放既有 override
    （一个 override + 一个 allow_divergence approval）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
        candidate_content=build_candidate_json(
            draft=DIVERGENT_DRAFT,
            citations=[CANDIDATE_EVIDENCE_1],
            divergence=DIVERGENCE,
        ),
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(ctx),
        )
        # 第二次：不同 reason → create_override 幂等返回同一 override。
        second = await facade.execute(
            "allow_divergence",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_divergence_params(
                ctx,
                reason="the twist requires the hero to know the secret early (replay)",
            ),
        )
        await session.commit()
    assert first["override_id"] == second["override_id"]
    assert first["approval_request_id"] == second["approval_request_id"]
    async with runtime_factory() as session:
        overrides = (
            await session.scalars(
                select(DerivativeOverride).where(
                    DerivativeOverride.owner_id == ctx["owner_id"]
                )
            )
        ).all()
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"],
                    ApprovalRequest.action == ALLOW_DIVERGENCE_APPROVAL_ACTION,
                )
            )
        ).all()
    assert len(overrides) == 1
    assert len(approvals) == 1
