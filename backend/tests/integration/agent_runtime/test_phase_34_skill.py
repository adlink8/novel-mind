"""Phase 34-05 integration test: SkillRun → ToolRun → Proposal → Approval → Publisher.

Prove Phase 34 deterministic anchor capability (D-34-01..D-34-04 / REQ-VIS-05 +
REQ-AGENT-03/04/07) is consumed through the versioned propose-illustration-anchor
Skill and that the Agent cannot bypass approval/publication authority:

Positive chain:
  register (versioned manifest: 5-tool allowlist = 3 read +
  publish_illustration / attach_illustration_to_text action + empty
  write_permissions + both actions declared in approval_required_for) →
  accept run (owner/novel/branch + input_hash binding) → stub loop calls the real
  facade action tool (publish_illustration creates a candidate proposal + pending
  Web ApprovalRequest) → finalize writes the candidate IllustrationAnchorProposal
  artifact → user confirms the approval → the deterministic publisher
  (publish_anchor) creates the valid anchor + frozen manifest.

Adversarial paths (all stable blocked/cancelled with zero authoritative writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (proposal_status non-proposed), wrong
  branch/fork, forged/pending/rejected approval, approval payload-hash tampering,
  forbidden action tool and Original-authority mutation attempts. FastAPI and the
  deterministic publisher keep permission / evidence / state-transition /
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
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import (
    IllustrationAnchor,
    IllustrationAnchorProposal,
)
from app.models.illustration_job import IllustrationJob
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
from app.services.illustration_anchors.publish import (
    AnchorPublishError,
    publish_anchor,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 34 编排 allowlist：3 个只读域工具 + 2 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "publish_illustration",
    "attach_illustration_to_text",
]
APPROVAL_ACTIONS = ["publish_illustration", "attach_illustration_to_text"]

# Deterministic mock chapter text (code-point offsets are stable in Python).
CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
_EXCERPT_START = CHAPTER_TEXT.index("The lanterns")
_EXCERPT_END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[_EXCERPT_START:_EXCERPT_END]
CHAPTER_CONTENT_HASH = hashlib.sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()

HEX64 = "a" * 64
HEX64_B = "b" * 64
SNAPSHOT_HASH = "4" * 64
SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
CONFIG_HASH = "5" * 64
ASSET_BYTES_HASH = "6" * 64


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
        "read_permissions": ["canon", "illustration"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "illustration:write",
            "illustration:publish",
            "approval_request",
            "publisher",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        # Phase 34：两个 action 都要求 Web ApprovalRequest（D-11/D-15）。
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "chapter_id": {"type": "integer"},
                "proposal_key": {"type": "string"},
                "requested_actions": {"type": "array"},
            },
            "required": ["novel_id", "chapter_id", "proposal_key", "requested_actions"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "illustration_anchor_proposal"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """Seed owner + novel + chapter + succeeded job + proposal-ready cleared asset."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p34_{suffix}",
            email=f"p34_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(title=f"P34 Novel {suffix}", owner_id=user.id)
        session.add(novel)
        session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=4,
            title="The Lantern Courtyard",
            content=CHAPTER_TEXT,
            word_count=len(CHAPTER_TEXT),
        )
        session.add(chapter)
        session.flush()
        job = IllustrationJob(
            owner_id=user.id,
            novel_id=novel.id,
            job_key=f"job-anchor-{suffix}",
            idempotency_key=hashlib.sha256(f"job-{suffix}".encode("utf-8")).hexdigest(),
            status="succeeded",
            status_reason="generated",
            scene_spec_hash=SCENE_SPEC_HASH,
            prompt_revision_id=101,
            prompt_revision_hash=PROMPT_HASH,
            visual_bible_revision_id=None,
            visual_bible_revision_hash=VB_HASH,
            source_snapshot_id="ss-1",
            source_snapshot_hash=SNAPSHOT_HASH,
            cutoff_chapter=8,
            model_lineage={},
            config_hash=CONFIG_HASH,
            price_snapshot={},
            response_hash=None,
            schema_version="illustration.v1",
        )
        session.add(job)
        session.flush()
        asset = AssetRevision(
            owner_id=user.id,
            novel_id=novel.id,
            job_id=job.id,
            revision_key="rev-1",
            revision_number=1,
            asset_id="asset-1",
            storage_key=f"assets/{user.id}/{novel.id}/{ASSET_BYTES_HASH}.png",
            mime_type="image/png",
            width=1024,
            height=1024,
            size_bytes=42,
            bytes_hash=ASSET_BYTES_HASH,
            scene_spec_hash=SCENE_SPEC_HASH,
            prompt_revision_id=101,
            prompt_revision_hash=PROMPT_HASH,
            visual_bible_revision_hash=VB_HASH,
            source_snapshot_id="ss-1",
            source_snapshot_hash=SNAPSHOT_HASH,
            cutoff_chapter=8,
            model_lineage={},
            config_hash=CONFIG_HASH,
            provider="mock",
            provider_model="mock-img-v1",
            provider_request_id="req-1",
            provider_response={},
            provenance={},
            rights_status="cleared",
            approval_state="proposal_ready",
            approved_by="editor",
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=hashlib.sha256(
                f"asset-{suffix}".encode("utf-8")
            ).hexdigest(),
            projection_hash=HEX64,
            schema_version="illustration-asset.v1",
        )
        session.add(asset)
        session.flush()
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_id": chapter.id,
            "job_id": job.id,
            "asset_id": asset.id,
            "contents": [CHAPTER_TEXT],
        }
    engine.dispose()
    return data


def _anchor_params(
    ids: dict[str, Any],
    *,
    action: str = "publish_illustration",
    proposal_key: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": None,
        "fork": None,
        "chapter_id": ids["chapter_id"],
        "chapter_number": 4,
        "proposal_key": proposal_key or f"anchor-lantern-{action}",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "source_start": _EXCERPT_START,
        "source_end": _EXCERPT_END,
        "paragraph_start": 2,
        "paragraph_end": 2,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "asset_revision_id": ids["asset_id"],
        "caption": "The lanterns flickered in the wind",
        "alt_text": "Illustration of flickering lanterns in the courtyard",
        "citation": "Chapter 4",
        "run_id": ids.get("run_id"),
        "skill_version_id": ids.get("skill_version_id"),
    }
    base.update(overrides)
    return base


def _run_input(ids: dict[str, Any], *, branch: str | None = None) -> dict[str, Any]:
    return {
        "novel_id": ids["novel_id"],
        "branch": branch,
        "fork": "fork-1" if branch else None,
        "chapter_id": ids["chapter_id"],
        "chapter_number": 4,
        "proposal_key": "anchor-lantern-publish_illustration",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "source_start": _EXCERPT_START,
        "source_end": _EXCERPT_END,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "asset_revision_id": ids["asset_id"],
        "presentation": {
            "caption": "The lanterns flickered in the wind",
            "alt_text": "Illustration of flickering lanterns in the courtyard",
            "citation": "Chapter 4",
        },
        "requested_actions": ["publish_illustration", "attach_illustration_to_text"],
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


async def _count_anchors(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(IllustrationAnchor)
                .where(IllustrationAnchor.owner_id == owner_id)
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
    authority_space: str = "original",
    fork: str | None = None,
    requested_action: str = "publish_illustration",
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 IllustrationAnchorProposal 信封。"""
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"] if not wrong_owner else ctx["owner_id"] + 999,
        "novel_id": ctx["novel_id"],
        "branch": ctx["branch"] if branch is None else branch,
        "producing_skill": "propose-illustration-anchor",
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
            "source_snapshot_hash": SNAPSHOT_HASH,
        },
        "input_hash": ctx["input_hash"] if not wrong_input_hash else "9" * 64,
        "evidence_refs": ["ev-lantern-courtyard"],
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "publish_illustration", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    payload: dict[str, Any] = {
        "schema_version": "illustration-anchor-proposal.v1",
        "artifact_kind": "illustration_anchor_proposal",
        "proposal_key": "anchor-lantern-publish_illustration",
        "authority_space": authority_space,
        "fork": fork,
        "chapter_id": ctx["chapter_id"],
        "chapter_number": 4,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "range": {
            "source_start": _EXCERPT_START,
            "source_end": _EXCERPT_END,
            "paragraph_start": 2,
            "paragraph_end": 2,
        },
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "proposal_asset_revision_id": ctx["asset_id"],
        "presentation": {
            "caption": "The lanterns flickered in the wind",
            "alt_text": "Illustration of flickering lanterns in the courtyard",
            "citation": "Chapter 4",
        },
        "requested_action": requested_action,
        "proposal_status": proposal_status,
        "approval_request_id": None,
        "proposal_id": None,
    }
    envelope: dict[str, Any] = {
        "type": "illustration_anchor_proposal",
        "schema_version": "illustration-anchor-proposal.v1",
        **common,
        "illustration_anchor_proposal": payload,
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
    """seed owner/novel/chapter/proposal-ready asset + register skill + accept run."""
    ids = _seed(sync_url, suffix=suffix)
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"],
            name="propose-illustration-anchor",
            tools=DEFAULT_TOOLS,
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
            "evidence_keys": ["ev-lantern-courtyard"],
        }
    )
    return ids


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase34_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 propose-illustration-anchor manifest 注册成功：5 工具 allowlist（3 只读
    + 2 action）+ 零写权限 + approval_required_for = 两个 Phase 34 action。"""
    seed = _seed(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="propose-illustration-anchor",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "propose-illustration-anchor"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "publish_illustration" in version.allowed_tools
        assert "attach_illustration_to_text" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == APPROVAL_ACTIONS
        assert version.forbidden_spaces == [
            "canon:original",
            "illustration:write",
            "illustration:publish",
            "approval_request",
            "publisher",
        ]
        assert "illustration" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase34_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（publish_everything）→ 注册拒绝，零 active 行。"""
    seed = _seed(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="propose-illustration-anchor",
        tools=list(DEFAULT_TOOLS) + ["publish_everything"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Approval→Publisher ──────────────────────────


async def test_phase34_happy_path_proposal_to_publish(
    runtime_factory, migrated_postgres: str
):
    """正向链：真实 facade action 工具创建候选 proposal + pending ApprovalRequest →
    finalize 写入 candidate IllustrationAnchorProposal → 用户确认 → 确定性
    publisher 创建 valid anchor + frozen manifest。零 Original 写入。"""
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
            "publish_illustration",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(
                ctx, proposal_key="anchor-lantern-publish_illustration"
            ),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "publish_illustration"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "pending_approval"
    proposal_id = int(tool_view["proposal_id"])
    approval_id = int(tool_view["approval_request_id"])

    async with runtime_factory() as session:
        proposal = await session.get(IllustrationAnchorProposal, proposal_id)
        approval = await session.get(ApprovalRequest, approval_id)
    assert proposal is not None and proposal.status == "pending_approval"
    assert approval is not None and approval.status == "pending"
    assert approval.payload_hash == proposal.canonical_payload_hash
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 0
    )

    # finalize 写入 candidate IllustrationAnchorProposal 产物。
    frozen_manifest = {
        "evidence_refs": ctx["evidence_keys"],
        "manifest_checksum": "m" * 64,
    }
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
        chapter_row = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_id"])
        )
    assert artifact is not None and artifact.type == "illustration_anchor_proposal"
    assert artifact.schema_version == "illustration-anchor-proposal.v1"
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
    payload = content["illustration_anchor_proposal"]
    assert payload["proposal_status"] == "proposed"
    assert payload["authority_space"] == "original"
    assert payload["anchor_hash"] == ANCHOR_HASH
    assert payload["proposal_asset_revision_id"] == ctx["asset_id"]
    for forbidden in ("authority", "cutoff", "approval", "approval_state"):
        assert forbidden not in content
    # Original 零变更：章节正文未动、无 valid anchor、无 published。
    assert chapter_row is not None and chapter_row.content == CHAPTER_TEXT
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 0
    )

    # 用户 Web 确认 → 确定性 publisher → valid anchor + frozen manifest。
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
        anchor = await publish_anchor(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            proposal_id=proposal_id,
        )
        await session.commit()
        fresh_proposal = await session.get(IllustrationAnchorProposal, proposal_id)
    assert anchor.status == "valid"
    assert anchor.published_asset_revision_id == ctx["asset_id"]
    assert anchor.approval_request_id == approval_id
    assert fresh_proposal is not None and fresh_proposal.status == "valid"
    assert anchor.publish_manifest_hash == fresh_proposal.publish_manifest_hash
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 1
    )
    # 提案投影推进到 valid。
    async with runtime_factory() as session:
        proposal = await session.get(IllustrationAnchorProposal, proposal_id)
        assert proposal is not None and proposal.status == "valid"
        assert proposal.published_asset_revision_id == ctx["asset_id"]
        assert proposal.publish_manifest_hash is not None


# ────────────────────────── 对抗路径（fail closed，零权威写入） ──────────────────────────


async def test_phase34_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest/anchor（cancel-without-write）。"""
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
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase34_wrong_owner_lineage_blocks(
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
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase34_wrong_skill_version_lineage_blocks(
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
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase34_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
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
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase34_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift：proposal_status 非 proposed（approval bypass / published 伪造）
    → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, proposal_status="published")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert (
        outcome.status_reason is not None and "proposal_status" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase34_wrong_branch_blocks(runtime_factory, migrated_postgres: str):
    """wrong branch/fork：run 绑定 original 主线（branch=None），envelope 声称
    derivative 模式（branch + fork）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, branch="deriv-branch", authority_space="derivative", fork="fork-1"
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase34_approval_not_confirmed_blocks_publish(
    runtime_factory, migrated_postgres: str
):
    """Approval 未确认（pending）→ deterministic publisher fail closed，零 anchor。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"noconf_{uuid.uuid4().hex[:6]}",
    )
    # 真实 action 工具创建候选 proposal + pending approval（不确认）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_illustration",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(ctx, proposal_key="anchor-lantern-noconf"),
        )
        await session.commit()
    proposal_id = int(tool_view["proposal_id"])
    with pytest.raises(AnchorPublishError):
        async with runtime_factory() as session:
            await publish_anchor(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                proposal_id=proposal_id,
            )
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 0
    )


async def test_phase34_approval_payload_hash_drift_blocks_publish(
    runtime_factory, migrated_postgres: str
):
    """approval payload_hash 被篡改（伪造批准）→ publisher fail closed，零 anchor。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"fakep_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "publish_illustration",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(ctx, proposal_key="anchor-lantern-fakep"),
        )
        await session.commit()
    proposal_id = int(tool_view["proposal_id"])
    approval_id = int(tool_view["approval_request_id"])
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希
        await session.commit()
    with pytest.raises(AnchorPublishError) as exc:
        async with runtime_factory() as session:
            await publish_anchor(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                proposal_id=proposal_id,
            )
    assert "payload hash" in str(exc.value)
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 0
    )


async def test_phase34_original_authority_untouched(
    runtime_factory, migrated_postgres: str
):
    """Original 权威零变更：章节正文、本 run 无 ApprovalRequest/anchor/proposal
    写入（Agent 不直接写锚点域表；run 本身不创建 approval——action 工具才创建）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        chapter = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_id"])
        )
        approvals_for_run = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == ctx["run_id"])
        )
        anchors_for_owner = await session.scalar(
            select(func.count())
            .select_from(IllustrationAnchor)
            .where(IllustrationAnchor.owner_id == ctx["owner_id"])
        )
        proposals_for_owner = await session.scalar(
            select(func.count())
            .select_from(IllustrationAnchorProposal)
            .where(IllustrationAnchorProposal.owner_id == ctx["owner_id"])
        )
    assert chapter is not None and chapter.content == CHAPTER_TEXT
    assert int(approvals_for_run or 0) == 0
    assert int(anchors_for_owner or 0) == 0
    assert int(proposals_for_owner or 0) == 0


async def test_phase34_forbidden_tool_action_never_publishes(
    runtime_factory, migrated_postgres: str
):
    """forbidden Tool/action：action 工具只创建候选 proposal + pending approval
    （status=pending_approval），绝不创建 valid anchor / published 状态。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"tool_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "attach_illustration_to_text",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(
                ctx,
                action="attach_illustration_to_text",
                proposal_key="anchor-lantern-attach",
            ),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == "attach_illustration_to_text"
    assert tool_view["approval_status"] == "pending"
    assert tool_view["status"] == "pending_approval"
    async with runtime_factory() as session:
        proposal = await session.get(
            IllustrationAnchorProposal, int(tool_view["proposal_id"])
        )
    assert proposal is not None and proposal.status == "pending_approval"
    assert proposal.published_asset_revision_id is None
    assert proposal.publish_manifest_hash is None
    assert (
        await _count(runtime_factory, IllustrationAnchor, owner_id=ctx["owner_id"]) == 0
    )


async def test_phase34_tool_idempotent_replay(runtime_factory, migrated_postgres: str):
    """publish_illustration 幂等：重复 span/asset/proposal_key → 重放既有候选
    proposal + approval（一个 proposal、一个 approval）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "publish_illustration",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(ctx, proposal_key="anchor-lantern-idem"),
        )
        second = await facade.execute(
            "publish_illustration",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_anchor_params(ctx, proposal_key="anchor-lantern-idem"),
        )
        await session.commit()
    assert first["proposal_id"] == second["proposal_id"]
    assert first["approval_request_id"] == second["approval_request_id"]
    assert first["replayed"] is False and second["replayed"] is True
    async with runtime_factory() as session:
        proposals = (
            await session.scalars(
                select(IllustrationAnchorProposal).where(
                    IllustrationAnchorProposal.owner_id == ctx["owner_id"]
                )
            )
        ).all()
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"]
                )
            )
        ).all()
    assert len(proposals) == 1
    assert len(approvals) == 1
