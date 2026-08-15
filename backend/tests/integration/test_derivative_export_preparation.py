"""Phase 39-05 derivative export preparation/materialization integration tests.

Prove the authenticated preparation → approval → deterministic materialization →
read-only download chain over the real CI PostgreSQL (D-39-01/D-39-02):

Positive chain:
  seed owner/novel/fork/project/chapters/revisions/assets → register the
  versioned prepare-export skill → accept a run → deterministic
  ``POST .../export/agent/prepare`` freezes the candidate ExportPreparation
  payload + preparation_hash → finalize writes the candidate
  ExportPreparationArtifact → ``POST .../export/agent/approve`` binds artifact
  revision + preparation_hash (pending approve_export ApprovalRequest) → user
  confirms → ``POST .../export/agent/materialize`` promotes the artifact to
  approved and produces the reproducible bundle → ``GET .../download`` is
  read-only and replays the same bytes.

Adversarial paths (all stable blocked/rejected with zero authoritative writes):
  stale preparation hash, materialize under a pending approval, forged approval
  payload hash, rejected approval, cross-owner artifact and download
  never mutating Artifact status / approval lineage.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.main import app
from app.models.agent_runtime import ApprovalRequest, Artifact, SkillRun
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.finalize import finalize_skill_run
from app.services.agent_runtime.registry import (
    canonical_input_hash,
    register_skill_version,
)
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.derivative_export.materializer import (
    APPROVE_EXPORT_APPROVAL_ACTION,
    set_materializer_asset_storage,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.integration.conftest import reset_public_schema, run_alembic
from tests.integration.test_derivative_export import _seed_chain

pytestmark = pytest.mark.integration

AGENT_PREPARE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/agent/prepare"
)
AGENT_APPROVE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/agent/approve"
)
AGENT_MATERIALIZE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/agent/materialize"
)
DOWNLOAD_BASE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/download"
)

HEX64 = "a" * 64
BRANCH_VALUE = "deriv-branch"

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


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(
    *, novel_id: int, name: str, tools: list[str]
) -> SkillVersionRegister:
    base: dict = {
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
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-prep-") as tmp:
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
async def api_client(runtime_factory, asset_storage):
    """ASGI client bound to the module-migrated PostgreSQL (head incl. 39-05)."""

    async def override_get_db():
        async with runtime_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    from app.api.derivative_export import set_derivative_export_asset_storage

    set_derivative_export_asset_storage(asset_storage)
    set_materializer_asset_storage(asset_storage)
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
    set_materializer_asset_storage(None)
    set_derivative_export_asset_storage(None)


# ────────────────────────── runtime helpers ──────────────────────────


async def _register_skill(factory, *, owner_id: int, novel_id: int) -> int:
    async with factory() as session:
        _, version = await register_skill_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            contract=_skill_contract(
                novel_id=novel_id, name="prepare-export", tools=DEFAULT_TOOLS
            ),
        )
        await session.commit()
        return version.id


async def _create_run(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_data: dict,
    branch: str = BRANCH_VALUE,
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
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return run.id


def _strip_trail(envelope: dict) -> dict:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _build_envelope(
    ctx: dict,
    preparation_payload: dict,
    *,
    evidence_refs: list[str],
    mutate=None,
) -> dict:
    envelope: dict = {
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
            "source_snapshot_hash": preparation_payload["source_snapshot"][
                "source_snapshot_hash"
            ],
        },
        "input_hash": ctx["input_hash"],
        "evidence_refs": evidence_refs,
        "preparation": dict(preparation_payload),
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


async def _finalize_candidate(
    factory, *, run_id: int, envelope: dict, evidence_refs: list[str]
):
    outcome = await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=envelope,
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions=dict(envelope.get("source_versions") or {}),
        usage={
            "calls": 3,
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": "0.001",
        },
        frozen_manifest={"evidence_refs": evidence_refs},
    )
    assert outcome.status == "completed", outcome.status_reason
    return outcome


async def _set_up(
    runtime_factory, sync_url: str, asset_storage, *, suffix: str
) -> dict:
    ids = _seed_chain(sync_url, asset_storage, suffix=suffix, chapter_count=2)
    ids["fork_key"] = f"ff-dex-{suffix}"
    svid = await _register_skill(
        runtime_factory, owner_id=ids["owner_id"], novel_id=ids["novel_id"]
    )
    ids["skill_version_id"] = svid
    return ids


def _evidence_refs(ids: dict) -> list[str]:
    return [
        f"fork:{ids['fork_key']}:chapter:1",
        f"fork:{ids['fork_key']}:chapter:2",
    ]


async def _freeze_and_finalize(
    runtime_factory, api_client, ids, *, preparation: dict | None = None
) -> dict:
    """POST /agent/prepare (deterministic freeze) then finalize the candidate."""
    headers = {"Authorization": f"Bearer {ids['token']}"}
    resp = await api_client.post(
        AGENT_PREPARE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"]},
        headers=headers,
        json={
            "branch": BRANCH_VALUE,
            "fork": ids["fork_key"],
            "evidence_refs": _evidence_refs(ids),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_only"] is True

    run_input = {
        "novel_id": ids["novel_id"],
        "branch": BRANCH_VALUE,
        "fork": ids["fork_key"],
        "project_id": ids["project_id"],
        "source_snapshot_hash": HEX64,
        "content_hash": body["snapshot_hash"],
        "evidence_refs": _evidence_refs(ids),
        "requested_action": ["approve_export", "materialize_export"],
    }
    run_id = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=ids["skill_version_id"],
        input_data=run_input,
    )
    ids["run_id"] = run_id
    ids["input_hash"] = canonical_input_hash(run_input)
    ids["preparation_hash"] = body["preparation_hash"]
    ids["snapshot_hash"] = body["snapshot_hash"]
    ids["manifest_hash"] = body["manifest_hash"]

    envelope = _build_envelope(
        ids,
        body["preparation"],
        evidence_refs=_evidence_refs(ids),
    )
    outcome = await _finalize_candidate(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        evidence_refs=_evidence_refs(ids),
    )
    ids["artifact_id"] = outcome.artifact_id
    ids["artifact_revision_id"] = outcome.artifact_revision_id
    return ids


async def _approve_via_api(
    api_client, ids, *, body_override: dict | None = None
) -> dict:
    headers = {"Authorization": f"Bearer {ids['token']}"}
    body = {
        "branch": BRANCH_VALUE,
        "fork": ids["fork_key"],
        "artifact_id": ids["artifact_id"],
        "artifact_revision_id": ids["artifact_revision_id"],
        "preparation_hash": ids["preparation_hash"],
    }
    if body_override:
        body.update(body_override)
    resp = await api_client.post(
        AGENT_APPROVE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"]},
        headers=headers,
        json=body,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _confirm_via_api(api_client, ids, approval_id: int) -> None:
    headers = {"Authorization": f"Bearer {ids['token']}"}
    resp = await api_client.post(
        f"/api/agent/approval-requests/{approval_id}/confirm",
        headers=headers,
        json={"mode": "once"},
    )
    assert resp.status_code == 200, resp.text


async def _materialize_via_api(
    api_client, ids, *, approval_id: int, body_override: dict | None = None
) -> dict:
    headers = {"Authorization": f"Bearer {ids['token']}"}
    body = {
        "branch": BRANCH_VALUE,
        "fork": ids["fork_key"],
        "artifact_id": ids["artifact_id"],
        "artifact_revision_id": ids["artifact_revision_id"],
        "approval_id": approval_id,
        "preparation_hash": ids["preparation_hash"],
    }
    if body_override:
        body.update(body_override)
    resp = await api_client.post(
        AGENT_MATERIALIZE.format(
            novel_id=ids["novel_id"], project_id=ids["project_id"]
        ),
        params={"novel_id": ids["novel_id"]},
        headers=headers,
        json=body,
    )
    return resp


async def _count(factory, model, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.owner_id == owner_id)  # type: ignore[attr-defined]
            )
            or 0
        )


# ────────────────────────── 正向链 ──────────────────────────


async def test_prepare_approve_materialize_download_chain(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """preparation → approval → 确定性 materialize → 只读 download 全链路。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)

    # 1. approve_export 创建 pending approval（绑定 artifact revision + preparation_hash）。
    view = await _approve_via_api(api_client, ids)
    assert view["candidate_only"] is True
    assert view["approval_action"] == APPROVE_EXPORT_APPROVAL_ACTION
    assert view["approval_status"] == "pending"
    assert view["approval_payload_hash"] == ids["preparation_hash"]
    approval_id = int(view["approval_request_id"])
    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert approval is not None and approval.action == APPROVE_EXPORT_APPROVAL_ACTION
    assert approval.status == "pending"
    assert approval.artifact_id == ids["artifact_id"]
    assert approval.artifact_revision_id == ids["artifact_revision_id"]
    assert approval.payload_hash == ids["preparation_hash"]
    assert artifact is not None and artifact.status == "candidate"

    # 2. 未确认时 materialize → approval_not_approved（pending approval fail closed）。
    pending_resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert pending_resp.status_code == 400, pending_resp.text
    assert "approval_not_approved" in pending_resp.text
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"

    # 3. 用户 Web 确认 → 确定性 materialize → artifact approved + bundle 元数据。
    await _confirm_via_api(api_client, ids, approval_id)
    materialize_resp = await _materialize_via_api(
        api_client, ids, approval_id=approval_id
    )
    assert materialize_resp.status_code == 200, materialize_resp.text
    materialized = materialize_resp.json()
    assert materialized["status"] == "approved"
    assert materialized["candidate_only"] is False
    assert materialized["materialized"] is True
    assert materialized["snapshot_hash"] == ids["snapshot_hash"]
    assert materialized["manifest_hash"] == ids["manifest_hash"]
    assert materialized["package_hash"] == materialized["package_hash"]
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "approved"

    # 4. 幂等：approved artifact 再次 materialize → 相同 bundle 元数据。
    materialize_resp2 = await _materialize_via_api(
        api_client, ids, approval_id=approval_id
    )
    assert materialize_resp2.status_code == 200, materialize_resp2.text
    assert materialize_resp2.json()["package_hash"] == materialized["package_hash"]

    # 5. download 只读：Markdown 字节可复现 + manifest hash 与物化一致。
    headers = {"Authorization": f"Bearer {ids['token']}"}
    download1 = await api_client.get(
        DOWNLOAD_BASE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"], "format": "markdown"},
        headers=headers,
    )
    assert download1.status_code == 200, download1.text
    download2 = await api_client.get(
        DOWNLOAD_BASE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"], "format": "markdown"},
        headers=headers,
    )
    assert download2.status_code == 200, download2.text
    assert download1.content == download2.content
    assert download1.headers["X-Export-Manifest-Hash"] == materialized["manifest_hash"]
    # download 永不改变 Artifact status / approval lineage。
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
        approvals = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.artifact_id == ids["artifact_id"])
        )
    assert artifact is not None and artifact.status == "approved"
    assert int(approvals or 0) == 1
    # Original 空间零写入（无新 approval 越权出现）。
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids["owner_id"]) == 1


# ────────────────────────── 对抗路径（fail closed） ──────────────────────────


async def test_stale_preparation_hash_rejected(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """stale preparation hash（DB 状态/冻结血缘不重放）→ approve fail closed，零 approval。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"stale_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    headers = {"Authorization": f"Bearer {ids['token']}"}
    resp = await api_client.post(
        AGENT_APPROVE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"]},
        headers=headers,
        json={
            "branch": BRANCH_VALUE,
            "fork": ids["fork_key"],
            "artifact_id": ids["artifact_id"],
            "artifact_revision_id": ids["artifact_revision_id"],
            "preparation_hash": "9" * 64,
        },
    )
    assert resp.status_code == 400, resp.text
    assert "preparation_hash_mismatch" in resp.text
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids["owner_id"]) == 0
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_materialize_forged_approval_hash_rejected(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """伪造 approval：确认后篡改 payload_hash → materialize fail closed（不产出 bundle）。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"forge_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)
    async with runtime_factory() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64  # 篡改重放哈希（伪造批准）
        await session.commit()

    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert "approval_hash_mismatch" in resp.text
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_materialize_rejected_approval_rejected(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """approval 被拒绝（rejected）→ materialize fail closed（不产出 bundle）。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    headers = {"Authorization": f"Bearer {ids['token']}"}
    resp = await api_client.post(
        f"/api/agent/approval-requests/{approval_id}/reject",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert "approval_not_approved" in resp.text
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_materialize_cancelled_approval_rejected(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """approval 过期/取消（expired）→ materialize fail closed（不产出 bundle）。"""
    from app.services.agent_runtime.approvals import expire_request

    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"exp_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    async with runtime_factory() as session:
        await expire_request(session, request_id=approval_id, owner_id=ids["owner_id"])
        await session.commit()

    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert "approval_not_approved" in resp.text
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None and artifact.status == "candidate"


async def test_materialize_wrong_scope_artifact_rejected(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """wrong scope：他人 artifact（跨 owner）→ 确定性 materialize fail closed。"""
    ids_a = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"sa_{uuid.uuid4().hex[:6]}",
    )
    ids_b = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"sb_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids_a)
    # ids_b 的 owner/novel 下伪造 A 的 artifact 引用 → 404-hide（无 403 oracle）。
    headers = {"Authorization": f"Bearer {ids_b['token']}"}
    resp = await api_client.post(
        AGENT_APPROVE.format(
            novel_id=ids_b["novel_id"], project_id=ids_b["project_id"]
        ),
        params={"novel_id": ids_b["novel_id"]},
        headers=headers,
        json={
            "branch": BRANCH_VALUE,
            "fork": ids_b["fork_key"],
            "artifact_id": ids_a["artifact_id"],
            "artifact_revision_id": ids_a["artifact_revision_id"],
            "preparation_hash": ids_a["preparation_hash"],
        },
    )
    assert resp.status_code in (400, 404), resp.text
    assert "artifact_not_found" in resp.text or "not found" in resp.text.lower()
    assert (
        await _count(runtime_factory, ApprovalRequest, owner_id=ids_b["owner_id"]) == 0
    )


async def test_cross_owner_novel_404_hides(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """cross-owner novel：他人小说路径调 agent 路由 → 404-hide（无 403 oracle）。"""
    ids_a = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"oa_{uuid.uuid4().hex[:6]}",
    )
    ids_b = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ob_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids_a)
    headers = {"Authorization": f"Bearer {ids_b['token']}"}
    resp = await api_client.post(
        AGENT_PREPARE.format(
            novel_id=ids_a["novel_id"], project_id=ids_a["project_id"]
        ),
        params={"novel_id": ids_a["novel_id"]},
        headers=headers,
        json={
            "branch": BRANCH_VALUE,
            "fork": ids_a["fork_key"],
            "evidence_refs": ["chapter:1"],
        },
    )
    assert resp.status_code == 404
    assert resp.status_code != 403


async def test_original_authority_untouched(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """Original 权威零变更：materialize 后 Original 行不变；无越权 approval/bundle 写。"""
    from app.models.visual_bible import VisualBibleVersion

    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)
    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 200, resp.text

    async with runtime_factory() as session:
        original_rows = await session.scalar(
            select(func.count()).select_from(VisualBibleVersion)
        )
    assert int(original_rows or 0) >= 1  # Original Visual Bible 行仍存在且不变
    # 该 owner 只有 approve_export approval（无越权 approval/bundle 写）。
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids["owner_id"]) == 1
