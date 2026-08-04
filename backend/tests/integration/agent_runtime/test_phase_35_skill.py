"""Phase 35-05 integration test: SkillRun → ToolRun → Proposal → Approval → Materializer.

Prove Phase 35 deterministic canon fork capability (D-35-01..D-35-04 / REQ-FORK-01 +
REQ-AGENT-03/04/07) is consumed through the versioned create-canon-fork Skill and
that the Agent cannot bypass approval/materialization authority:

Positive chain:
  register (versioned manifest: 8-tool allowlist = 7 read +
  create_canon_fork action + empty write_permissions + the action declared in
  approval_required_for) → accept run (owner/novel/branch + input_hash binding) →
  stub loop calls the real facade action tool (create_canon_fork creates a
  candidate fork + pending Web ApprovalRequest) → finalize writes the candidate
  CanonForkProposal + CanonDeltaArtifact artifact → user confirms the approval →
  the deterministic Fork materializer (materialize_approved_fork) approves the
  fork (status=approved, active stays false, Original Canon unchanged).

Adversarial paths (all stable blocked/cancelled with zero authoritative writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (proposal_status / delta_status non-proposed),
  wrong branch, forged/pending/rejected approval, approval payload-hash tampering,
  forbidden action tool and Original-authority mutation attempts. FastAPI and the
  deterministic Fork materializer keep permission / evidence / state-transition /
  publication authority.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, undefer
from sqlalchemy.pool import NullPool

from app.models import Chapter, Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.models.canon_fork import CanonFork
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
from app.services.agent_tools.facade import ToolFacade
from app.services.canon_fork.materializer import (
    ForkMaterializeError,
    create_fork_proposal,
    materialize_approved_fork,
)
from app.services.canon_fork.snapshot import (
    ForkChapterRecord,
    compute_source_snapshot_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 35 编排 allowlist：7 个只读域工具 + 1 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "create_canon_fork",
]
APPROVAL_ACTIONS = ["create_canon_fork"]

# Deterministic chapter texts; source snapshot hash replays from them.
CHAPTER_TEXTS = {1: "chapter 1 body", 2: "chapter 2 body", 3: "chapter 3 body"}
DELTA_CONTENT = (
    "Aurora wakes before dawn and walks the southern wall, tracing the light "
    "that Arin described."
)
DELTA_HASH = hashlib.sha256(DELTA_CONTENT.encode("utf-8")).hexdigest()
FORK_KEY = "fork-aurora"
DELTA_KEY = "delta-aurora-01"


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
        "read_permissions": ["canon", "canon_fork"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "canon_fork:write",
            "canon_fork:materialize",
            "approval_request",
            "fork_materializer",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        # Phase 35：action 要求 Web ApprovalRequest（D-11/D-15）。
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "fork_key": {"type": "string"},
                "delta_key": {"type": "string"},
                "requested_actions": {"type": "array"},
            },
            "required": ["novel_id", "fork_key", "delta_key", "requested_actions"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "canon_fork_proposal"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + 3 chapters; return the frozen source snapshot hash."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p35_{suffix}",
            email=f"p35_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"P35 Novel {suffix}",
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
            "source_snapshot_hash": snapshot_hash,
            "contents": list(CHAPTER_TEXTS.values()),
        }
    engine.dispose()
    return data


def _fork_params(
    ids: dict[str, Any],
    *,
    fork_key: str = FORK_KEY,
    delta_key: str = DELTA_KEY,
    delta_content: str = DELTA_CONTENT,
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": ids.get("branch"),
        "fork": "fork-1" if ids.get("branch") else None,
        "fork_key": fork_key,
        "requested_cutoff_chapter": 2,
        "full_book_requested": False,
        "expected_source_snapshot_hash": ids["source_snapshot_hash"],
        "delta_key": delta_key,
        "delta_content": delta_content,
        "delta_evidence_refs": ["chapter:1", "chapter:2"],
        "run_id": ids.get("run_id"),
        "skill_version_id": ids.get("skill_version_id"),
        "artifact_id": ids.get("artifact_id"),
        "artifact_revision_id": ids.get("artifact_revision_id"),
    }
    base.update(overrides)
    return base


def _run_input(ids: dict[str, Any], *, branch: str | None = None) -> dict[str, Any]:
    return {
        "novel_id": ids["novel_id"],
        "branch": branch,
        "fork": "fork-1" if branch else None,
        "fork_key": FORK_KEY,
        "requested_cutoff_chapter": 2,
        "full_book_requested": False,
        "expected_source_snapshot_hash": ids["source_snapshot_hash"],
        "delta_key": DELTA_KEY,
        "delta_content": DELTA_CONTENT,
        "delta_evidence_refs": ["chapter:1", "chapter:2"],
        "requested_actions": ["create_canon_fork"],
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
            "calls": 2,
            "input_tokens": 400,
            "output_tokens": 200,
            "cost_usd": "0.0008",
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
                select(func.count())
                .select_from(model)
                .where(model.run_id == run_id)  # type: ignore[attr-defined]
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


async def _count_forks(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CanonFork)
                .where(CanonFork.owner_id == owner_id)
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
    ctx: dict[str, Any],
    *,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    proposal_status: str = "proposed",
    delta_status: str = "proposed",
    branch: str | None | object = None,
    delta_content: str = DELTA_CONTENT,
    delta_base: str | None = None,
    delta_content_hash: str | None = None,
    delta_key: str = DELTA_KEY,
) -> dict[str, Any]:
    """Build a CanonForkProposal envelope carrying the 26-06 normalization trail."""
    branch_value = ctx["branch"] if branch is None else branch
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"] if not wrong_owner else ctx["owner_id"] + 999,
        "novel_id": ctx["novel_id"],
        "branch": branch_value,
        "producing_skill": "create-canon-fork",
        "producing_skill_version": "1.0.0",
        "skill_version_id": (
            ctx["skill_version_id"]
            if not wrong_version
            else ctx["skill_version_id"] + 999
        ),
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": ctx["source_snapshot_hash"],
        },
        "input_hash": ctx["input_hash"] if not wrong_input_hash else "9" * 64,
        "evidence_refs": ["chapter:1", "chapter:2"],
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "create_canon_fork", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    proposal: dict[str, Any] = {
        "schema_version": "canon-fork-proposal.v1",
        "artifact_kind": "canon_fork_proposal",
        "fork_key": ctx.get("fork_key", FORK_KEY),
        "branch": ctx.get("branch"),
        "fork": "fork-1" if ctx.get("branch") else None,
        "source_version_key": ctx.get("source_version_key", "original:1"),
        "source_snapshot_id": ctx.get(
            "source_snapshot_id", f"novel:{ctx['novel_id']}:{ctx['source_snapshot_hash'][:16]}"
        ),
        "source_snapshot_hash": ctx["source_snapshot_hash"],
        "through_chapter": ctx.get("through_chapter", 2),
        "full_book_authorized": False,
        "cutoff_snapshot_hash": ctx.get("cutoff_snapshot_hash", "5" * 64),
        "scope_hash": ctx.get("scope_hash", "6" * 64),
        "manifest_hash": ctx.get("manifest_hash", "7" * 64),
        "citation_lineage": ctx.get(
            "citation_lineage",
            [
                {
                    "leaf_key": "chapter:1",
                    "chapter_number": 1,
                    "content_hash": "a" * 64,
                    "source_snapshot_hash": ctx["source_snapshot_hash"],
                }
            ],
        ),
        "authorization": ctx.get("authorization", {}),
        "proposal_status": proposal_status,
        "approval_request_id": None,
        "fork_id": None,
    }
    delta: dict[str, Any] = {
        "schema_version": "canon-delta.v1",
        "artifact_kind": "canon_delta",
        "delta_key": delta_key,
        "base_revision": delta_base or ctx.get("manifest_hash", "7" * 64),
        "content": delta_content,
        "content_hash": delta_content_hash
        or hashlib.sha256(delta_content.encode("utf-8")).hexdigest(),
        "evidence_refs": ["chapter:1", "chapter:2"],
        "delta_status": delta_status,
    }
    envelope: dict[str, Any] = {
        "type": "canon_fork_proposal",
        "schema_version": "canon-fork-proposal.v1",
        **common,
        "proposal": proposal,
        "delta": delta,
    }
    # 26-06 trail：repaired_hash 是对不含 trail 的 payload 的 canonical SHA-256。
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


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


async def _set_up(
    runtime_factory, sync_url: str, *, suffix: str, branch: str | None = None
) -> dict[str, Any]:
    """seed owner/novel/chapters + register skill + accept run."""
    ids = _seed(sync_url, suffix=suffix)
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"], name="create-canon-fork", tools=DEFAULT_TOOLS
        ),
    )
    run_input = _run_input(ids, branch=branch)
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
        input_data=run_input,
        branch=branch,
    )
    ids.update(
        {
            "skill_version_id": svid,
            "run_id": run_id,
            "input_hash": input_hash,
            "run_input": run_input,
            "branch": branch,
        }
    )
    return ids


async def _load_fork_context(runtime_factory, *, fork_id: int) -> dict[str, Any]:
    """Load the frozen fork manifest fields into the envelope context."""
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
    assert fork is not None
    return {
        "fork_id": fork.id,
        "fork_key": fork.fork_key,
        "source_version_key": fork.source_version_key,
        "source_snapshot_id": fork.source_snapshot_id,
        "source_snapshot_hash": fork.source_snapshot_hash,
        "through_chapter": fork.through_chapter,
        "full_book_authorized": fork.full_book_authorized,
        "cutoff_snapshot_hash": fork.cutoff_snapshot_hash,
        "scope_hash": fork.scope_hash,
        "manifest_hash": fork.manifest_hash,
        "citation_lineage": list(fork.citation_lineage or []),
        "authorization": dict(fork.authorization or {}),
    }


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase35_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 create-canon-fork manifest 注册成功：8 工具 allowlist（7 只读 + 1
    action）+ 零写权限 + approval_required_for = [create_canon_fork]。"""
    seed = _seed(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="create-canon-fork",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "create-canon-fork"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "create_canon_fork" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == APPROVAL_ACTIONS
        assert version.forbidden_spaces == [
            "canon:original",
            "canon_fork:write",
            "canon_fork:materialize",
            "approval_request",
            "fork_materializer",
        ]
        assert "canon" in version.read_permissions
        assert "canon_fork" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase35_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（materialize_fork_directly）→ 注册拒绝，零 active 行。"""
    seed = _seed(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="create-canon-fork",
        tools=list(DEFAULT_TOOLS) + ["materialize_fork_directly"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Approval→Materializer ──────────────────────────


async def test_phase35_happy_path_proposal_to_materialize(
    runtime_factory, migrated_postgres: str
):
    """正向链：真实 facade action 工具创建候选 fork + pending ApprovalRequest →
    finalize 写入 candidate CanonForkProposal + CanonDeltaArtifact → 用户确认 →
    确定性 Fork materializer 物化 fork（approved）。零 Original 写入、active 恒
    false。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
    )
    run_id = ctx["run_id"]

    # stub agent loop：真实调用 facade 只读工具 + action 工具。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        novel_view = await facade.execute(
            "get_novel",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"]},
        )
        assert novel_view is not None and novel_view["id"] == ctx["novel_id"]
        tool_view = await facade.execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "create_canon_fork"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "candidate"
    assert tool_view["active"] is False
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])

    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
        approval = await session.get(ApprovalRequest, approval_id)
    assert fork is not None and fork.status == "candidate"
    assert fork.active is False
    assert approval is not None and approval.status == "pending"
    assert approval.action == "create_canon_fork"
    assert approval.fork_id == fork_id

    # finalize 写入 candidate CanonForkProposal 产物。
    ctx.update(await _load_fork_context(runtime_factory, fork_id=fork_id))
    frozen_manifest = {
        "evidence_refs": ["chapter:1", "chapter:2"],
        "manifest_checksum": ctx["manifest_hash"],
    }
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory, run_id=run_id, envelope=envelope, frozen_manifest=frozen_manifest
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_id is not None
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
    assert artifact is not None and artifact.type == "canon_fork_proposal"
    assert artifact.schema_version == "canon-fork-proposal.v1"
    assert artifact.status == "candidate"
    assert run_row is not None and run_row.status == "completed"
    content = revision.content
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    assert content["owner_id"] == ctx["owner_id"]
    assert content["input_hash"] == ctx["input_hash"]
    proposal = content["proposal"]
    delta = content["delta"]
    assert proposal["proposal_status"] == "proposed"
    assert proposal["manifest_hash"] == ctx["manifest_hash"]
    assert delta["delta_status"] == "proposed"
    assert delta["base_revision"] == ctx["manifest_hash"]
    for forbidden in ("authority", "cutoff", "approval", "approval_state"):
        assert forbidden not in content
    # Original 零变更。
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())

    # 用户 Web 确认 → 确定性 Fork materializer → approved fork。
    async with runtime_factory() as session:
        await confirm(session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once")
        await session.commit()
        outcome = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=outcome.artifact_revision_id,
        )
        await session.commit()
        fresh_fork = await session.get(CanonFork, fork_id)
    assert outcome.fork.status == "approved"
    assert outcome.materialization_hash is not None
    assert len(outcome.materialization_hash) == 64
    assert fresh_fork is not None and fresh_fork.status == "approved"
    assert fresh_fork.active is False
    # active pointer 恒 false + Original 正文未动。
    async with runtime_factory() as session:
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
        fork_count = await session.scalar(
            select(func.count())
            .select_from(CanonFork)
            .where(CanonFork.owner_id == ctx["owner_id"], CanonFork.active.is_(True))
        )
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())
    assert int(fork_count or 0) == 0


# ────────────────────────── 对抗路径（fail closed，零权威写入） ──────────────────────────


async def test_phase35_cancellation_no_write(
    runtime_factory, migrated_postgres: str
):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"cancel_{uuid.uuid4().hex[:6]}",
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        input_data=ctx["run_input"],
        cancel_requested=True,
    )
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase35_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"own_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_owner=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase35_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"ver_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_version=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "skill_version_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase35_stale_input_hash_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"hash_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_input_hash=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase35_schema_drift_blocks(
    runtime_factory, migrated_postgres: str
):
    """schema drift：proposal_status / delta_status 非 proposed（approval bypass /
    物化伪造）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, proposal_status="approved")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert (
        outcome.status_reason is not None
        and "proposal_status" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])

    ctx2 = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift2_{uuid.uuid4().hex[:6]}",
    )
    envelope2 = _build_envelope(ctx2, delta_status="approved")
    outcome2 = await _finalize(
        runtime_factory,
        run_id=ctx2["run_id"],
        envelope=envelope2,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome2.status == "failed"
    assert outcome2.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome2.status_reason is not None and "delta_status" in outcome2.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx2["run_id"])


async def test_phase35_delta_hash_drift_blocks(
    runtime_factory, migrated_postgres: str
):
    """delta content_hash 不从内容重放（schema drift）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"dhash_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, delta_content_hash="c" * 64)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "content_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase35_wrong_branch_blocks(
    runtime_factory, migrated_postgres: str
):
    """wrong branch：run 绑定 original 主线（branch=None），envelope 声称 derivative
    分支（branch + fork）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, branch="deriv-branch")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase35_approval_not_confirmed_blocks_materialize(
    runtime_factory, migrated_postgres: str
):
    """Approval 未确认（pending）→ 确定性 materializer fail closed，fork 不物化。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"noconf_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-noconf"),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])
    ctx.update(await _load_fork_context(runtime_factory, fork_id=fork_id))
    envelope = _build_envelope(ctx, delta_key="delta-noconf")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "completed"
    assert outcome.artifact_revision_id is not None
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=outcome.artifact_revision_id,
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
    assert fork is not None and fork.status == "candidate"


async def test_phase35_approval_payload_hash_drift_blocks_materialize(
    runtime_factory, migrated_postgres: str
):
    """approval payload_hash 被篡改（伪造批准）→ materializer fail closed，不物化。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"fakep_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-fakep"),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])
    ctx.update(await _load_fork_context(runtime_factory, fork_id=fork_id))
    envelope = _build_envelope(ctx, delta_key="delta-fakep")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once")
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=outcome.artifact_revision_id,
            )
    assert exc.value.code == "approval_payload_mismatch"
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
    assert fork is not None and fork.status == "candidate"


async def test_phase35_stale_base_revision_blocks_materialize(
    runtime_factory, migrated_postgres: str
):
    """delta base_revision 与 fork manifest 不符（stale base）→ materializer 拒绝。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"stale_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-stale"),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])
    ctx.update(await _load_fork_context(runtime_factory, fork_id=fork_id))
    envelope = _build_envelope(ctx, delta_base="b" * 64, delta_key="delta-stale")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once")
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=outcome.artifact_revision_id,
            )
    assert exc.value.code == "delta_base_mismatch"
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
    assert fork is not None and fork.status == "candidate"


async def test_phase35_original_authority_untouched(
    runtime_factory, migrated_postgres: str
):
    """Original 权威零变更：章节正文、本 run 无 ApprovalRequest/fork 写入（run 本身
    不创建 approval/fork——action 工具才创建）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
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
        forks_for_owner = await session.scalar(
            select(func.count())
            .select_from(CanonFork)
            .where(CanonFork.owner_id == ctx["owner_id"])
        )
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())
    assert int(approvals_for_run or 0) == 0
    assert int(forks_for_owner or 0) == 0


async def test_phase35_forbidden_tool_action_never_materializes(
    runtime_factory, migrated_postgres: str
):
    """forbidden Tool/action：action 工具只创建候选 fork + pending approval
    （status=candidate），绝不物化 fork / 绝不触碰 Original Canon。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"tool_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-tool"),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "create_canon_fork"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "candidate"
    assert tool_view["active"] is False
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, int(tool_view["fork_id"]))
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
            )
        ).all()
    assert fork is not None and fork.status == "candidate"
    assert fork.active is False
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())


async def test_phase35_tool_idempotent_replay(
    runtime_factory, migrated_postgres: str
):
    """create_canon_fork 幂等：重复 fork_key + delta → 重放既有候选 fork + approval
    （一个 fork、一个 approval）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-idem"),
        )
        second = await facade.execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-idem"),
        )
        await session.commit()
    assert first["fork_id"] == second["fork_id"]
    assert first["approval_request_id"] == second["approval_request_id"]
    assert second["replayed"] is True
    async with runtime_factory() as session:
        forks = (
            await session.scalars(
                select(CanonFork).where(CanonFork.owner_id == ctx["owner_id"])
            )
        ).all()
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"]
                )
            )
        ).all()
    assert len(forks) == 1
    assert len(approvals) == 1


async def test_phase35_materializer_replays_approved_fork(
    runtime_factory, migrated_postgres: str
):
    """materialize 幂等：第二次对已 approved fork 的调用返回 replayed=True，不再写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"repl_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-repl"),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])
    ctx.update(await _load_fork_context(runtime_factory, fork_id=fork_id))
    envelope = _build_envelope(ctx, delta_key="delta-repl")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once")
        await session.commit()
        first = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=outcome.artifact_revision_id,
        )
        await session.commit()
        second = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=outcome.artifact_revision_id,
        )
        await session.commit()
    assert first.replayed is False and first.fork.status == "approved"
    assert second.replayed is True and second.fork.status == "approved"
    assert first.materialization_hash == second.materialization_hash
    async with runtime_factory() as session:
        fork_count = await session.scalar(
            select(func.count())
            .select_from(CanonFork)
            .where(CanonFork.owner_id == ctx["owner_id"])
        )
    assert int(fork_count or 0) == 1
