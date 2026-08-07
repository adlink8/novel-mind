"""Phase 36-05 integration test: SkillRun → ToolRun → Proposal → Approval → deterministic apply.

Prove Phase 36 deterministic derivative editor capability (D-36-01..D-36-04 /
REQ-FORK-02 + REQ-AGENT-03/04/07) is consumed through the versioned
edit-derivative-story Skill and that the Agent cannot bypass approval / apply
authority:

Positive chain:
  register (versioned manifest: 7-tool allowlist = 6 read +
  apply_derivative_edit action + empty write_permissions + the action declared
  in approval_required_for) → accept run (owner/novel/branch + input_hash
  binding) → stub loop calls the real facade action tool (apply_derivative_edit
  creates a candidate DerivativeEditProposal + pending Web ApprovalRequest) →
  finalize writes the candidate DerivativeEditProposal artifact → user confirms
  the approval → the deterministic Revision Service (apply_agent_edit) applies
  the approved proposal as an append-only agent_proposal revision (chapter head
  advances, Original Canon unchanged, user draft/autosave untouched).

Adversarial paths (all stable blocked/cancelled with zero authoritative writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (proposal_status non-proposed, content_hash
  drift), wrong branch, forged/pending/rejected approval, approval payload-hash
  tampering, forbidden action tool, Original-authority mutation attempts,
  concurrent user_autosave versus agent_proposal (one committed child, one
  recoverable 409, no last-write-wins, no cross-path event/approval leakage),
  and idempotent apply replay. FastAPI and the deterministic Revision Service
  keep permission / evidence / state-transition / apply authority.
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
from app.services.derivative_editor.revisions import (
    autosave_revision,
    derivative_edit_content_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 36 编排 allowlist：6 个只读域工具 + 1 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "apply_derivative_edit",
]
APPROVAL_ACTIONS = ["apply_derivative_edit"]

# Deterministic chapter texts; source snapshot hash replays from them.
CHAPTER_TEXTS = {1: "chapter 1 body", 2: "chapter 2 body", 3: "chapter 3 body"}
INITIAL_MARKDOWN = "# Draft\nAurora stands at the gate."
PROPOSED_CONTENT = (
    "# Draft\nAurora walks the southern wall, tracing the light that Arin described."
)
PROPOSAL_KEY = "edit-aurora-01"
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
        "read_permissions": ["canon", "fanfiction_canon"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "approval_request",
            "revision_service",
        ],
        "budget": {
            "max_calls": 30,
            "max_input_tokens": 30_000,
            "max_output_tokens": 10_000,
            "max_cost_usd": "3.00",
        },
        # Phase 36：action 要求 Web ApprovalRequest（D-11/D-15）。
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "project_id": {"type": "integer"},
                "chapter_id": {"type": "integer"},
                "proposal_key": {"type": "string"},
                "base_revision": {"type": "integer"},
                "requested_actions": {"type": "array"},
            },
            "required": [
                "novel_id",
                "project_id",
                "chapter_id",
                "proposal_key",
                "base_revision",
                "requested_actions",
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "derivative_edit_proposal"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + 3 original chapters; return the source snapshot hash."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p36_{suffix}",
            email=f"p36_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"P36 Novel {suffix}",
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


def _edit_params(
    ids: dict[str, Any],
    *,
    content: str = PROPOSED_CONTENT,
    proposal_key: str = PROPOSAL_KEY,
    base_revision: int | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": ids.get("branch"),
        "fork": "fork-1" if ids.get("branch") else None,
        "project_id": ids["project_id"],
        "chapter_id": ids["chapter_id"],
        "chapter_number": ids.get("chapter_number", 1),
        "proposal_key": proposal_key,
        "base_revision": base_revision or ids["base_revision"],
        "content": content,
        "source_snapshot_id": f"novel:{ids['novel_id']}:fork-1",
        "source_snapshot_hash": ids["source_snapshot_hash"],
        "evidence_refs": ["chapter:1"],
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
        "project_id": ids["project_id"],
        "chapter_id": ids["chapter_id"],
        "chapter_number": ids.get("chapter_number", 1),
        "proposal_key": PROPOSAL_KEY,
        "base_revision": ids["base_revision"],
        "content": PROPOSED_CONTENT,
        "source_snapshot_id": f"novel:{ids['novel_id']}:fork-1",
        "source_snapshot_hash": ids["source_snapshot_hash"],
        "evidence_refs": ["chapter:1"],
        "requested_actions": ["apply_derivative_edit"],
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
            budget_snapshot={"max_calls": 30},
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
    branch: str | None | object = None,
    content: str = PROPOSED_CONTENT,
    content_hash: str | None = None,
    base_revision: int | None = None,
    source_snapshot_hash: str | None = None,
    proposal_key: str | None = None,
) -> dict[str, Any]:
    """Build a DerivativeEditProposal envelope carrying the 26-06 normalization trail."""
    branch_value = ctx["branch"] if branch is None else branch
    key_value = proposal_key or ctx.get("proposal_key", PROPOSAL_KEY)
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"] if not wrong_owner else ctx["owner_id"] + 999,
        "novel_id": ctx["novel_id"],
        "branch": branch_value,
        "producing_skill": "edit-derivative-story",
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
            "source_snapshot_hash": source_snapshot_hash or ctx["source_snapshot_hash"],
        },
        "input_hash": ctx["input_hash"] if not wrong_input_hash else "9" * 64,
        "evidence_refs": ["chapter:1"],
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "apply_derivative_edit", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    proposal: dict[str, Any] = {
        "schema_version": "derivative-edit-proposal.v1",
        "artifact_kind": "derivative_edit_proposal",
        "proposal_key": key_value,
        "authority_space": "derivative",
        "branch": branch_value,
        "fork": "fork-1" if branch_value else None,
        "project_id": ctx["project_id"],
        "chapter_id": ctx["chapter_id"],
        "chapter_number": ctx.get("chapter_number", 1),
        "base_revision": base_revision or ctx["base_revision"],
        "content": content,
        "content_hash": content_hash or derivative_edit_content_hash(content),
        "source_snapshot_id": f"novel:{ctx['novel_id']}:fork-1",
        "source_snapshot_hash": source_snapshot_hash or ctx["source_snapshot_hash"],
        "evidence_refs": ["chapter:1"],
        "proposal_status": proposal_status,
        "approval_request_id": None,
        "artifact_id": None,
        "validator_report": None,
    }
    envelope: dict[str, Any] = {
        "type": "derivative_edit_proposal",
        "schema_version": "derivative-edit-proposal.v1",
        **common,
        "proposal": proposal,
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


@pytest_asyncio.fixture
async def api_client(runtime_factory):
    """ASGI client bound to the module-migrated PostgreSQL (head incl. 36-05)."""

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


async def _set_up(
    runtime_factory, sync_url: str, *, suffix: str, branch: str | None = None
) -> dict[str, Any]:
    """seed owner/novel/chapters + candidate fork + project + chapter + register skill + run."""
    ids = _seed(sync_url, suffix=suffix)
    branch_value = "deriv-branch" if branch is None else branch
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
                "fork": "fork-1" if branch_value else None,
                "fork_key": f"{FORK_KEY}-{suffix}",
                "requested_cutoff_chapter": 2,
                "full_book_requested": False,
                "expected_source_snapshot_hash": ids["source_snapshot_hash"],
                "delta_key": f"{DELTA_KEY}-{suffix}",
                "delta_content": PROPOSED_CONTENT,
                "delta_evidence_refs": ["chapter:1", "chapter:2"],
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
    ids["base_revision"] = chapter_view.revision

    # 4. register the versioned edit-derivative-story skill.
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"], name="edit-derivative-story", tools=DEFAULT_TOOLS
        ),
    )

    # 5. accept the run.
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
        }
    )
    return ids


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase36_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 edit-derivative-story manifest 注册成功：7 工具 allowlist（6 只读 +
    1 action）+ 零写权限 + approval_required_for = [apply_derivative_edit]。"""
    seed = _seed(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="edit-derivative-story",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "edit-derivative-story"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "apply_derivative_edit" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == APPROVAL_ACTIONS
        assert version.forbidden_spaces == [
            "canon:original",
            "user_interpretation",
            "derivative:autosave",
            "derivative:direct_write",
            "approval_request",
            "revision_service",
        ]
        assert "canon" in version.read_permissions
        assert "fanfiction_canon" in version.read_permissions
        assert int(version.budget["max_calls"]) == 30


async def test_phase36_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（apply_derivative_edit_directly）→ 注册拒绝，零 active 行。"""
    seed = _seed(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="edit-derivative-story",
        tools=list(DEFAULT_TOOLS) + ["apply_derivative_edit_directly"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Approval→Apply ──────────────────────────


async def test_phase36_happy_path_proposal_to_apply(
    runtime_factory, migrated_postgres: str, api_client
):
    """正向链：真实 facade action 工具创建候选 proposal + pending ApprovalRequest →
    finalize 写入 candidate DerivativeEditProposal → 用户确认 → HTTP apply 端点 →
    确定性 Revision Service 应用 append-only agent_proposal 修订。零 Original 写入、
    user autosave 不动。"""
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
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "apply_derivative_edit"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "candidate"
    assert tool_view["proposal_key"] == PROPOSAL_KEY
    approval_id = int(tool_view["approval_request_id"])

    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
    assert approval is not None and approval.status == "pending"
    assert approval.action == "apply_derivative_edit"
    assert approval.fork_id is not None

    # finalize 写入 candidate DerivativeEditProposal 产物。
    frozen_manifest = {"evidence_refs": ["chapter:1"]}
    envelope = _build_envelope(ctx)
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
        run_row = await session.get(SkillRun, run_id)
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
    assert artifact is not None and artifact.type == "derivative_edit_proposal"
    assert artifact.schema_version == "derivative-edit-proposal.v1"
    assert artifact.status == "candidate"
    assert run_row is not None and run_row.status == "completed"
    content = revision.content
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    assert content["owner_id"] == ctx["owner_id"]
    assert content["input_hash"] == ctx["input_hash"]
    proposal = content["proposal"]
    assert proposal["proposal_status"] == "proposed"
    assert proposal["content_hash"] == derivative_edit_content_hash(PROPOSED_CONTENT)
    assert proposal["base_revision"] == ctx["base_revision"]
    for forbidden in ("authority", "cutoff", "approval", "approval_state"):
        assert forbidden not in content
    # Original 零变更 + user autosave 未被触碰（仅 root create 行）。
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())

    # 用户 Web 确认 → HTTP apply 端点 → 确定性 Revision Service 应用。
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["applied"] is True
    assert body["status"] == "applied"
    assert body["event"] == "derivative.agent_proposal.applied"
    assert body["proposal_key"] == PROPOSAL_KEY
    assert body["validator_report"]["verdict"] == "pass"
    assert body["validator_report"]["approval"]["payload_hash_replayed"] is True
    assert body["chapter"]["markdown"] == PROPOSED_CONTENT
    assert body["chapter"]["revision"] == ctx["base_revision"] + 1
    assert body["revision"]["kind"] == "agent_proposal"
    assert body["revision"]["approval_state"] == "approved"
    assert body["revision"]["content"] == PROPOSED_CONTENT

    # append-only lineage：root create + agent_proposal 两行；无 autosave 行。
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
    assert [row.kind for row in rows] == ["create", "agent_proposal"]
    assert rows[1].approval_state == "approved"
    assert rows[1].reason == f"agent_proposal:{PROPOSAL_KEY}:approval:{approval_id}"
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


async def test_phase36_cancellation_no_write(runtime_factory, migrated_postgres: str):
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
        branch=ctx["branch"],
        cancel_requested=True,
    )
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase36_wrong_owner_lineage_blocks(
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
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_wrong_skill_version_lineage_blocks(
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
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
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
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift：proposal_status 非 proposed（直接应用伪造）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, proposal_status="applied")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert (
        outcome.status_reason is not None and "proposal_status" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_content_hash_drift_blocks(
    runtime_factory, migrated_postgres: str
):
    """proposal content_hash 不从内容重放（schema drift）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"dhash_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, content_hash="c" * 64)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "content_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_wrong_branch_blocks(runtime_factory, migrated_postgres: str):
    """wrong branch：run 绑定 derivative 分支，envelope 声称别的分支（branch 血缘
    不符）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, branch="other-branch")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase36_approval_not_confirmed_blocks_apply(
    runtime_factory, migrated_postgres: str, api_client
):
    """Approval 未确认（pending）→ apply 端点 fail closed，无权威写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"noconf_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-noconf"),
        )
        await session.commit()
    assert tool_view["approval_status"] == "pending"
    envelope = _build_envelope(ctx, proposal_key="edit-noconf")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    assert outcome.artifact_revision_id is not None

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "approval_not_approved"
    # 无权威写入：chapter 仍在 revision 1，无 agent_proposal 行。
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
        rows = (
            await session.scalars(
                select(DerivativeRevision).where(
                    DerivativeRevision.owner_id == ctx["owner_id"]
                )
            )
        ).all()
    assert chapter is not None and chapter.revision == ctx["base_revision"]
    assert all(row.kind != "agent_proposal" for row in rows)


async def test_phase36_approval_payload_hash_drift_blocks_apply(
    runtime_factory, migrated_postgres: str, api_client
):
    """approval payload_hash 被篡改（伪造批准）→ apply fail closed，不应用。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"fakep_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-fakep"),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    envelope = _build_envelope(ctx, proposal_key="edit-fakep")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "approval_not_found"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None and chapter.revision == ctx["base_revision"]


async def test_phase36_apply_rejected_when_approval_rejected(
    runtime_factory, migrated_postgres: str, api_client
):
    """approval 被用户拒绝（rejected）→ apply fail closed，不应用。"""
    from app.services.agent_runtime.approvals import reject

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-rej"),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    envelope = _build_envelope(ctx, proposal_key="edit-rej")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await reject(session, request_id=approval_id, owner_id=ctx["owner_id"])
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "approval_not_approved"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None and chapter.revision == ctx["base_revision"]


async def test_phase36_stale_base_revision_blocks_apply(
    runtime_factory, migrated_postgres: str, api_client
):
    """stale base：autosave 先推进 revision，approved proposal 的 base_revision 已 stale →
    apply 409，不覆盖新内容（无 last-write-wins）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"stale_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-stale"),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    envelope = _build_envelope(ctx, proposal_key="edit-stale")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        # user autosave 在 proposal 之后推进 revision（base 1 → 2）。
        await autosave_revision(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            project_id=ctx["project_id"],
            chapter_id=ctx["chapter_id"],
            content="# Draft\nA user wrote something new.",
            base_revision=ctx["base_revision"],
            actor_id=ctx["owner_id"],
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "revision_conflict"
    # user 新内容未被覆盖。
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
    assert chapter is not None
    assert chapter.revision == ctx["base_revision"] + 1
    assert "# Draft\nA user wrote something new." in chapter.markdown


async def test_phase36_apply_is_idempotent(
    runtime_factory, migrated_postgres: str, api_client
):
    """apply 幂等：重复 apply 同一 approved proposal → 第二次 noop，不追加新行。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-idem"),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    envelope = _build_envelope(ctx, proposal_key="edit-idem")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    url = f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply"
    first = await api_client.post(url, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "applied"
    second = await api_client.post(url, headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "noop"
    assert second.json()["revision"]["id"] == first.json()["revision"]["id"]

    async with runtime_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(DerivativeRevision).where(
                        DerivativeRevision.owner_id == ctx["owner_id"]
                    )
                )
            ).all()
        )
    assert len(rows) == 2  # create + one agent_proposal (no duplicate)


async def test_phase36_concurrent_autosave_and_proposal_never_last_write_win(
    runtime_factory, migrated_postgres: str, api_client
):
    """并发 user_autosave × agent_proposal 同 base → 一胜一 409，无 last-write-wins；
    无跨路径 approval/event 泄漏。"""
    import asyncio

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"conc_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-conc"),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    envelope = _build_envelope(ctx, proposal_key="edit-conc")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ["chapter:1"]},
    )
    assert outcome.status == "completed"
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()

    headers = {"Authorization": f"Bearer {ctx['token']}"}
    apply_url = f"/api/agent/derivative-edit-proposals/{outcome.artifact_id}/apply"
    autosave_url = (
        f"/api/novels/{ctx['novel_id']}/derivative-projects/{ctx['project_id']}"
        f"/chapters/{ctx['chapter_id']}/autosave"
    )

    async def apply_proposal():
        return await api_client.post(apply_url, headers=headers)

    async def user_autosave():
        return await api_client.post(
            autosave_url,
            json={
                "content": "# Draft\nUser concurrent draft.",
                "base_revision": ctx["base_revision"],
            },
            headers=headers,
        )

    results = await asyncio.gather(apply_proposal(), user_autosave())
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [r.text for r in results]
    winner = next(r for r in results if r.status_code == 200)
    loser = next(r for r in results if r.status_code == 409)
    if winner.json().get("status") == "applied":
        assert loser.json()["detail"].get("code") == "revision_conflict"
        # 只有 agent_proposal 被提交；autosave 409。
        async with runtime_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(DerivativeRevision).where(
                            DerivativeRevision.owner_id == ctx["owner_id"]
                        )
                    )
                ).all()
            )
        assert [row.kind for row in rows] == ["create", "agent_proposal"]
        assert winner.json()["revision"]["kind"] == "agent_proposal"
        assert loser.json()["detail"]["code"] == "revision_conflict"
    else:
        # user autosave won; the proposal apply conflicts (approved but stale base).
        assert loser.json()["detail"].get("code") == "revision_conflict"
        async with runtime_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(DerivativeRevision).where(
                            DerivativeRevision.owner_id == ctx["owner_id"]
                        )
                    )
                ).all()
            )
        assert [row.kind for row in rows] == ["create", "autosave"]
        assert winner.json()["status"] == "saved"


async def test_phase36_forbidden_tool_never_applies(
    runtime_factory, migrated_postgres: str
):
    """forbidden Tool/action：action 工具只创建候选 proposal + pending approval
    （proposal_status=proposed），绝不直接应用 / 触碰 Original Canon / user autosave。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"tool_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-tool"),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "apply_derivative_edit"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "candidate"
    async with runtime_factory() as session:
        chapter = await session.get(DerivativeChapter, ctx["chapter_id"])
        rows = (
            await session.scalars(
                select(DerivativeRevision).where(
                    DerivativeRevision.owner_id == ctx["owner_id"]
                )
            )
        ).all()
    assert chapter is not None and chapter.revision == ctx["base_revision"]
    assert all(row.kind != "agent_proposal" for row in rows)


async def test_phase36_original_authority_untouched(
    runtime_factory, migrated_postgres: str
):
    """Original 权威零变更：章节正文、本 run 无 ApprovalRequest 写入（run 本身不
    创建 approval——action 工具才创建）。"""
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
        revisions_for_owner = await session.scalar(
            select(func.count())
            .select_from(DerivativeRevision)
            .where(DerivativeRevision.owner_id == ctx["owner_id"])
        )
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())
    assert int(approvals_for_run or 0) == 0
    assert int(revisions_for_owner or 0) == 1  # root create only


async def test_phase36_tool_idempotent_replay(runtime_factory, migrated_postgres: str):
    """apply_derivative_edit 幂等：重复 proposal_key + content → 重放既有 approval
    （一个 approval）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem2_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-idem2"),
        )
        second = await facade.execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-idem2"),
        )
        await session.commit()
    assert first["approval_request_id"] == second["approval_request_id"]
    assert second["replayed"] is True
    async with runtime_factory() as session:
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"],
                    ApprovalRequest.action == "apply_derivative_edit",
                )
            )
        ).all()
    assert len(approvals) == 1


async def test_phase36_proposal_conflict_blocks_second_intent(
    runtime_factory, migrated_postgres: str
):
    """同一 project 上不同 proposal intent → proposal_conflict fail closed。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"conf_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        await facade.execute(
            "apply_derivative_edit",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_edit_params(ctx, proposal_key="edit-conf-1"),
        )
        await session.commit()
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "apply_derivative_edit",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_edit_params(
                    ctx, proposal_key="edit-conf-2", content=PROPOSED_CONTENT + " extra"
                ),
            )
    assert "proposal_conflict" in str(exc.value)
