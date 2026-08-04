"""Phase 39-05 integration test: SkillRun → ToolRun → candidate ExportPreparationArtifact →
approve_export approval → deterministic materialize_export bundle.

Prove Phase 39 derivative export capability (D-39-01..D-39-03 / REQ-FORK-05 +
REQ-AGENT-02/03/04/07) is consumed through the versioned prepare-export Skill and
that the Agent cannot bypass approval / materialization authority:

Positive chain:
  register (versioned manifest: 6-tool allowlist = 4 read +
  approve_export/materialize_export actions + empty write_permissions +
  approve_export in approval_required_for) → accept run (owner/novel/branch +
  input_hash binding) → deterministic preparation freeze (approved-only
  revisions/assets/citations) → finalize writes the candidate
  ExportPreparationArtifact (review_state=candidate) → stub loop calls the real
  facade action tool approve_export (creates a pending ApprovalRequest bound to
  artifact revision + preparation_hash) → user confirms → deterministic
  materializer (materialize_export) promotes the artifact to approved and
  produces the reproducible bundle (frozen manifest replay).

Adversarial paths (all stable blocked/cancelled/rejected with zero authoritative
writes):
  unknown tool registration, cancellation, wrong owner / skill_version /
  input_hash lineage, schema drift (status non-candidate, review_state
  non-candidate approval bypass, source snapshot drift, evidence mismatch),
  wrong branch, stale preparation hash, wrong artifact revision, forged/stale/
  pending/rejected/cancelled approval, wrong fork scope, wrong approval action
  and Original-authority mutation attempts.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.main import app
from app.models import Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.approvals import confirm, expire_request, reject
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
from app.services.derivative_export.materializer import (
    APPROVE_EXPORT_APPROVAL_ACTION,
    ExportMaterializationError,
    set_materializer_asset_storage,
)
from app.services.derivative_export.preparation import (
    export_preparation_hash,
    prepare_export,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.integration.conftest import reset_public_schema, run_alembic
from tests.integration.test_derivative_export import _seed_chain

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

# Phase 39 编排 allowlist：4 个只读域工具 + 2 个 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_narrative_memory",
    "approve_export",
    "materialize_export",
]
APPROVAL_ACTIONS = ["approve_export"]

BRANCH_VALUE = "deriv-branch"


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(*, novel_id: int, name: str, tools: list[str]) -> SkillVersionRegister:
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
            "derivative_export:write",
            "approval_request",
            "materializer_service",
            "published_assets",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        "approval_required_for": list(APPROVAL_ACTIONS),
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "branch": {"type": ["string", "null"]},
                "fork": {"type": ["string", "null"]},
                "project_id": {"type": "integer"},
                "source_snapshot_hash": {"type": "string"},
                "content_hash": {"type": "string"},
                "evidence_refs": {"type": "array"},
                "requested_action": {"type": "array"},
            },
            "required": [
                "novel_id",
                "project_id",
                "source_snapshot_hash",
                "content_hash",
                "evidence_refs",
                "requested_action",
            ],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "export_preparation"}},
        },
    }
    return SkillVersionRegister.model_validate(base)


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture(scope="module")
def asset_storage() -> DerivativeAssetStorage:
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-skill-") as tmp:
        yield DerivativeAssetStorage(Path(tmp))


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
    """ASGI client bound to the module-migrated PostgreSQL (head incl. 39-05)."""

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


# ────────────────────────── runtime helpers ──────────────────────────


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
    input_data: dict[str, Any],
    branch: str = BRANCH_VALUE,
    cancel_requested: bool = False,
) -> int:
    input_hash = canonical_input_hash(input_data)
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
        usage={"calls": 4, "input_tokens": 600, "output_tokens": 300, "cost_usd": "0.0015"},
        frozen_manifest=frozen_manifest,
    )


async def _count(factory, model, *, run_id: int | None = None, owner_id: int | None = None) -> int:
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
            return int(await session.scalar(select(func.count()).select_from(model)) or 0)
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


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count(factory, Artifact, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _evidence_refs(ids: dict[str, Any]) -> list[str]:
    return [
        f"fork:{ids['fork_key']}:chapter:1",
        f"fork:{ids['fork_key']}:chapter:2",
    ]


def _run_input(ids: dict[str, Any]) -> dict[str, Any]:
    return {
        "novel_id": ids["novel_id"],
        "branch": BRANCH_VALUE,
        "fork": ids["fork_key"],
        "project_id": ids["project_id"],
        "source_snapshot_id": f"novel:{ids['novel_id']}:{ids['fork_key']}",
        "source_snapshot_hash": HEX64,
        "content_hash": ids["snapshot_hash"],
        "evidence_refs": _evidence_refs(ids),
        "requested_action": ["approve_export", "materialize_export"],
    }


def _build_envelope(ctx: dict[str, Any], *, mutate=None) -> dict[str, Any]:
    """Build a canonical ExportPreparationArtifact envelope from the frozen
    preparation payload + run lineage.

    ``mutate`` (optional) is applied **before** the 26-06 normalization trail is
    computed, so an adversarial mutation keeps a replay-consistent trail and the
    failure lands on the intended gate rather than on ``repaired_hash``.
    """
    envelope: dict[str, Any] = {
        "type": "export_preparation",
        "schema_version": "export-preparation.v1",
        "owner_id": ctx["owner_id"],
        "novel_id": ctx["novel_id"],
        "branch": BRANCH_VALUE,
        "producing_skill": "prepare-export",
        "producing_skill_version": "1.0.0",
        "skill_version_id": ctx["skill_version_id"],
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": ctx["preparation_payload"]["source_snapshot"][
                "source_snapshot_hash"
            ],
        },
        "input_hash": ctx["input_hash"],
        "evidence_refs": _evidence_refs(ctx),
        "preparation": dict(ctx["preparation_payload"]),
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "get_chapter", "calls": 1},
            {"tool_name": "approve_export", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    if mutate is not None:
        mutate(envelope)
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


def _approve_params(ctx: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": BRANCH_VALUE,
        "fork": ctx["fork_key"],
        "project_id": ctx["project_id"],
        "artifact_id": ctx["artifact_id"],
        "artifact_revision_id": ctx["artifact_revision_id"],
        "preparation_hash": ctx["preparation_hash"],
        "approval_note": "approve the derivative export",
        "run_id": ctx["run_id"],
        "skill_version_id": ctx["skill_version_id"],
    }
    base.update(overrides)
    return base


def _materialize_params(ctx: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": BRANCH_VALUE,
        "fork": ctx["fork_key"],
        "project_id": ctx["project_id"],
        "artifact_id": ctx["artifact_id"],
        "artifact_revision_id": ctx["artifact_revision_id"],
        "approval_id": ctx.get("approval_id"),
        "preparation_hash": ctx["preparation_hash"],
        "reason": "materialize the approved derivative export",
        "run_id": ctx["run_id"],
        "skill_version_id": ctx["skill_version_id"],
    }
    base.update(overrides)
    return base


async def _set_up(
    runtime_factory,
    sync_url: str,
    asset_storage: DerivativeAssetStorage,
    *,
    suffix: str,
) -> dict[str, Any]:
    """seed owner/novel/fork/project/chapters/assets + skill + run + finalize candidate."""
    ids = _seed_chain(sync_url, asset_storage, suffix=suffix, chapter_count=2)
    ids["fork_key"] = f"ff-dex-{suffix}"

    # 1. register the versioned prepare-export skill.
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"],
            name="prepare-export",
            tools=DEFAULT_TOOLS,
        ),
    )

    # 2. deterministic preparation freeze (approved-only revisions/assets).
    async with runtime_factory() as session:
        frozen = await prepare_export(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
            branch=BRANCH_VALUE,
            fork=ids["fork_key"],
            evidence_refs=_evidence_refs(ids),
            generator_lineage={"provider": "fixture", "model": "stub-model"},
            storage=asset_storage,
        )
        await session.commit()
    ids["snapshot_hash"] = frozen.snapshot.snapshot_hash
    ids["manifest_hash"] = frozen.manifest.manifest_hash
    ids["preparation_payload"] = dict(frozen.preparation_payload)
    ids["preparation_hash"] = frozen.preparation_hash

    # 3. accept the run.
    run_input = _run_input(ids)
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=svid,
        input_data=run_input,
        branch=BRANCH_VALUE,
    )
    ids.update(
        {
            "skill_version_id": svid,
            "run_id": run_id,
            "input_hash": input_hash,
            "run_input": run_input,
        }
    )

    # 4. run 已接受但尚未 finalize——candidate 产物由各测试 finalize（正向用正确
    #    envelope；对抗用 mutated envelope，验证 integrity gate）。
    ids.update(
        {
            "skill_version_id": svid,
            "run_id": run_id,
            "input_hash": input_hash,
            "run_input": run_input,
        }
    )
    return ids


async def _finalize_candidate(
    runtime_factory, ctx: dict[str, Any], *, envelope: dict[str, Any] | None = None
):
    """finalize 写入 candidate ExportPreparationArtifact（status=candidate）。"""
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope or _build_envelope(ctx),
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "completed", outcome.status_reason
    ctx["artifact_id"] = outcome.artifact_id
    ctx["artifact_revision_id"] = outcome.artifact_revision_id
    return outcome


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase39_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 prepare-export manifest 注册成功：6 工具 allowlist（4 只读 +
    2 action）+ 零写权限 + approval_required_for 恰为 approve_export。"""
    ids = _seed_chain(
        migrated_postgres,
        DerivativeAssetStorage(Path(tempfile.mkdtemp(prefix="nm39reg-"))),
        suffix=f"reg_{uuid.uuid4().hex[:6]}",
        with_assets=False,
        with_override=False,
    )
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"], name="prepare-export", tools=DEFAULT_TOOLS
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
    assert version is not None
    assert version.name == "prepare-export"
    assert version.version == "1.0.0"
    assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
    assert "approve_export" in version.allowed_tools
    assert "materialize_export" in version.allowed_tools
    assert version.write_permissions == []
    assert set(version.approval_required_for) == set(APPROVAL_ACTIONS)
    assert "canon" in version.read_permissions
    assert "fanfiction_canon" in version.read_permissions
    assert int(version.budget["max_calls"]) == 40


async def test_phase39_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（materialize_export_directly）→ 注册拒绝，零 active 行。"""
    ids = _seed_chain(
        migrated_postgres,
        DerivativeAssetStorage(Path(tempfile.mkdtemp(prefix="nm39unk-"))),
        suffix=f"unk_{uuid.uuid4().hex[:6]}",
        with_assets=False,
        with_override=False,
    )
    contract = _skill_contract(
        novel_id=ids["novel_id"],
        name="prepare-export",
        tools=list(DEFAULT_TOOLS) + ["materialize_export_directly"],
    )
    with pytest.raises(SkillContractError):
        await _register_skill(
            runtime_factory,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            contract=contract,
        )
    async with runtime_factory() as session:
        registry_count = await session.scalar(
            select(func.count())
            .select_from(SkillRegistry)
            .where(SkillRegistry.owner_id == ids["owner_id"])
        )
    assert int(registry_count or 0) == 0


# ────────────────────────── Task 2：candidate → approval → deterministic materialize ──────────────────────────


async def test_phase39_approval_and_deterministic_materialize(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """正向链：candidate ExportPreparationArtifact → approve_export approval（绑定
    artifact revision + preparation_hash）→ 用户确认 → 确定性 materialize_export
    推进 approved 并产出可复现 bundle。绝不写 Original Canon。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    run_id = ctx["run_id"]

    # stub agent loop：真实调用 approve_export action 工具。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    assert tool_view["candidate_only"] is True
    assert tool_view["approval_action"] == APPROVE_EXPORT_APPROVAL_ACTION
    assert tool_view["approval_status"] == "pending"
    assert tool_view["approval_payload_hash"] == ctx["preparation_hash"]
    assert tool_view["artifact_id"] == ctx["artifact_id"]
    assert tool_view["artifact_revision_id"] == ctx["artifact_revision_id"]
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id

    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
    assert approval is not None and approval.status == "pending"
    assert approval.action == APPROVE_EXPORT_APPROVAL_ACTION
    assert approval.artifact_id == ctx["artifact_id"]
    assert approval.artifact_revision_id == ctx["artifact_revision_id"]
    assert approval.payload_hash == ctx["preparation_hash"]

    # 用户 Web 确认 → 确定性 materialize_export 推进 approved 并产出 bundle。
    async with runtime_factory() as session:
        await confirm(session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once")
        await session.commit()

    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        materialized = await ToolFacade().execute(
            "materialize_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_materialize_params(ctx),
        )
        await session.commit()
    assert materialized["status"] == "approved"
    assert materialized["candidate_only"] is False
    assert materialized["materialized"] is True
    assert materialized["snapshot_hash"] == ctx["snapshot_hash"]
    assert materialized["manifest_hash"] == ctx["manifest_hash"]
    assert materialized["approval_request_id"] == approval_id

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
        revision = await session.get(ArtifactRevision, ctx["artifact_revision_id"])
    assert artifact is not None and artifact.status == "approved"
    assert artifact.current_revision_id == ctx["artifact_revision_id"]
    assert revision is not None
    content = revision.content
    assert content["type"] == "export_preparation"
    assert content["owner_id"] == ctx["owner_id"]
    assert content["input_hash"] == ctx["input_hash"]
    assert content["branch"] == BRANCH_VALUE
    assert content["preparation"]["authority_space"] == "derivative"
    assert content["preparation"]["fork"] == ctx["fork_key"]
    assert content["preparation"]["review_state"] == "candidate"
    assert content["preparation"]["content_hash"] == ctx["snapshot_hash"]

    # Original 空间零变更（Original Visual Bible 行 hash 不变）。
    async with runtime_factory() as session:
        from app.models.visual_bible import VisualBibleVersion

        original = await session.scalar(
            select(VisualBibleVersion).where(VisualBibleVersion.owner_id == ctx["owner_id"])
        )
    assert original is not None
    assert original.source_snapshot_hash == HEX64

    # run 本身不创建任何 ApprovalRequest；只有 action 工具创建。
    async with runtime_factory() as session:
        approvals_for_run = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == run_id)
        )
    assert int(approvals_for_run or 0) == 1


async def test_phase39_http_action_route_wired(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """HTTP 路由连通：POST /api/agent-tools/approve_export 经 require_owned_novel
    注入 owner/novel 后创建 pending approval（candidate-only）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"http_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    headers = {"Authorization": f"Bearer {ctx['token']}"}
    resp = await api_client.post(
        "/api/agent-tools/approve_export",
        params={"novel_id": ctx["novel_id"]},
        headers=headers,
        json=_approve_params(ctx),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_only"] is True
    assert body["approval_action"] == APPROVE_EXPORT_APPROVAL_ACTION
    assert body["approval_status"] == "pending"
    assert body["approval_payload_hash"] == ctx["preparation_hash"]


# ────────────────────────── 对抗路径（fail closed，零权威写入） ──────────────────────────


async def test_phase39_cancellation_no_write(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"cancel_{uuid.uuid4().hex[:6]}",
    )
    run_input = ctx["run_input"]
    run_id = await _create_run(
        runtime_factory,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_data=run_input,
        branch=BRANCH_VALUE,
        cancel_requested=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=_build_envelope(ctx),
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase39_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """envelope owner 血缘与 run 不符 → blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"own_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, mutate=lambda e: e.__setitem__("owner_id", ctx["owner_id"] + 999)
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_stale_input_hash_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"hash_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, mutate=lambda e: e.__setitem__("input_hash", "9" * 64)
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_schema_drift_status_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """schema drift：ExportPreparationArtifact status 非 candidate（直接发布伪造）
    → blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, mutate=lambda e: e.__setitem__("status", "published")
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "status" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_approval_bypass_review_state_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """approval bypass：preparation.review_state 非 candidate（approved 伪造）→
    blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"bypass_{uuid.uuid4().hex[:6]}",
    )

    def _forged(e):
        e["preparation"]["review_state"] = "approved"

    envelope = _build_envelope(ctx, mutate=_forged)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "review_state" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_wrong_branch_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """wrong branch：run 绑定 derivative 分支，envelope 声称别的分支（branch 血缘
    不符）→ blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
    )

    def _wrong_branch(e):
        e["branch"] = "other-branch"
        e["preparation"]["fork"] = "fork-other"

    envelope = _build_envelope(ctx, mutate=_wrong_branch)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_source_snapshot_drift_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """source snapshot drift：preparation source_snapshot_hash 与信封 source_versions
    不一致 → blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ss_{uuid.uuid4().hex[:6]}",
    )

    def _drift(e):
        e["preparation"]["source_snapshot"]["source_snapshot_hash"] = "d" * 64

    envelope = _build_envelope(ctx, mutate=_drift)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "source_snapshot_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_evidence_mismatch_blocks(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """evidence 越界：preparation.evidence_refs 含信封外 key → blocked，零写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ev_{uuid.uuid4().hex[:6]}",
    )

    def _foreign(e):
        e["preparation"]["evidence_refs"] = ["chapter:999"]

    envelope = _build_envelope(ctx, mutate=_foreign)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": _evidence_refs(ctx)},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "evidence" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase39_stale_preparation_hash_action_fails_closed(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """stale preparation_hash（伪造）→ approve_export action fail closed，零 approval。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"phash_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "approve_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_approve_params(ctx, preparation_hash="9" * 64),
            )
    assert "preparation_hash_mismatch" in str(exc.value)
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase39_stale_artifact_revision_action_fails_closed(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """stale artifact revision（非当前修订）→ approve_export action fail closed，
    零 approval。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"rev_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "approve_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_approve_params(
                    ctx, artifact_revision_id=ctx["artifact_revision_id"] + 999_999
                ),
            )
    assert "artifact_revision_stale" in str(exc.value)
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase39_consume_pending_approval_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """确定性 materializer 消费 pending approval（未确认）→ approval_not_approved，
    零权威写入。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"pend_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx),
            )
    assert "approval_not_approved" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_forged_approval_hash_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """伪造 approval：确认后篡改 payload_hash（hash 绑定漂移）→ 确定性 materializer
    fail closed，不产出 bundle。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"forge_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希（伪造批准）
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx),
            )
    assert "approval_hash_mismatch" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_wrong_fork_scope_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """wrong fork scope：确认后篡改 approval.fork_id → 确定性 materializer fail
    closed（fork_scope_mismatch），不产出 bundle。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fork_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.fork_id = approval.fork_id + 999_999  # 篡改 fork scope（wrong fork）
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx),
            )
    assert "fork_scope_mismatch" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_rejected_approval_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """approval 被拒绝（rejected）→ 确定性 materializer fail closed（不产出 bundle）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    async with runtime_factory() as session:
        await reject(session, request_id=approval_id, owner_id=ctx["owner_id"])
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx),
            )
    assert "approval_not_approved" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_cancelled_approval_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """取消（approval expired）→ 确定性 materializer fail closed（不产出 bundle）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"exp_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    async with runtime_factory() as session:
        await expire_request(
            session, request_id=approval_id, owner_id=ctx["owner_id"]
        )
        await session.commit()

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx),
            )
    assert "approval_not_approved" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_wrong_action_approval_fails(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """wrong approval action：把另一 action 的 approved approval 当 approve_export
    消费 → approval_not_found（不产出 bundle）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"act_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    # 伪造一个已批准的其它 action approval（例如 publish_illustration）。
    async with runtime_factory() as session:
        forged = ApprovalRequest(
            owner_id=ctx["owner_id"],
            run_id=ctx["run_id"],
            novel_id=ctx["novel_id"],
            branch_id=None,
            fork_id=None,
            action="publish_illustration",
            payload_summary={},
            payload_hash="d" * 64,
            status="approved",
        )
        session.add(forged)
        await session.commit()
        forged_id = forged.id

    with pytest.raises(InvalidInputError) as exc:
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            await ToolFacade().execute(
                "materialize_export",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params=_materialize_params(ctx, approval_id=forged_id),
            )
    assert "approval_not_found" in str(exc.value)
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ctx["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_phase39_action_is_idempotent(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """approve_export 幂等：重复 artifact + 相同血缘 → 重放既有 approval
    （一个 pending approval）。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        second = await facade.execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx, approval_note="replayed"),
        )
        await session.commit()
    assert first["approval_request_id"] == second["approval_request_id"]
    assert second["replayed"] is True
    async with runtime_factory() as session:
        approvals = (
            await session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.owner_id == ctx["owner_id"],
                    ApprovalRequest.action == APPROVE_EXPORT_APPROVAL_ACTION,
                )
            )
        ).all()
    assert len(approvals) == 1


async def test_phase39_materialize_is_idempotent(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """materialize_export 幂等：approved artifact 再次物化 → 相同 bundle 元数据。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"midem_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "approve_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_approve_params(ctx),
        )
        await session.commit()
    approval_id = int(tool_view["approval_request_id"])
    ctx["approval_id"] = approval_id
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()

    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        first = await ToolFacade().execute(
            "materialize_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_materialize_params(ctx),
        )
        second = await ToolFacade().execute(
            "materialize_export",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_materialize_params(ctx),
        )
        await session.commit()
    assert first["package_hash"] == second["package_hash"]
    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["status"] == "approved"


async def test_phase39_original_authority_untouched(
    runtime_factory, migrated_postgres: str, asset_storage
):
    """Original 权威零变更：run 本身不创建任何 ApprovalRequest（只有 action 工具
    创建）；Original Visual Bible 行不变；无越权 published/bundle 写。"""
    set_materializer_asset_storage(asset_storage)
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
    )
    await _finalize_candidate(runtime_factory, ctx)
    async with runtime_factory() as session:
        approvals_for_run = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == ctx["run_id"])
        )
        from app.models.visual_bible import VisualBibleVersion

        original = await session.scalar(
            select(VisualBibleVersion).where(VisualBibleVersion.owner_id == ctx["owner_id"])
        )
    assert int(approvals_for_run or 0) == 0
    assert original is not None
    assert original.source_snapshot_hash == HEX64
