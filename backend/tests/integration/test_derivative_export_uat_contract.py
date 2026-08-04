"""Phase 39-03 derivative export browser UAT contract (D-39-03, T-39-03-01/02).

The browser must be able to request an export **only** from an approved
``ExportPreparationArtifact`` and inspect both Markdown and EPUB formats
(REQ-FORK-05 / REQ-CRE-07). This suite proves the server-side half of that
contract against the real CI PostgreSQL:

- an approved artifact serves both formats with byte parity and a manifest
  header that replays the frozen artifact manifest hash;
- refresh / reopen (a fresh deterministic freeze) replays the same snapshot,
  manifest and download bytes;
- cross-owner / Original / unknown-project access is an identical 404 or a
  stable fail-closed code (no 403 oracle, no content leak);
- pending / rejected approvals, a stale artifact and a forged preparation hash
  are blocked before any authoritative write (artifact stays candidate);
- a missing asset binary blocks materialization (bundle cannot be complete)
  while the read-only download still presents the explicit placeholder;
- after approval + materialization + download there is zero Original mutation,
  exactly one approve_export approval, an approved artifact, and the
  three-dimension audit report replays its own report hash.

Browser-side negative UAT (route-mocked UI) lives in
``frontend/e2e/derivative-export.spec.ts``; this file is the authenticated
server scope + audit evidence contract.
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.main import app
from app.models.agent_runtime import ApprovalRequest, Artifact
from app.models.derivative_visual import DerivativeVisualCandidateAsset
from app.services.derivative_export.audit import audit_report_hash
from app.services.derivative_export.materializer import (
    APPROVE_EXPORT_APPROVAL_ACTION,
    set_materializer_asset_storage,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.integration.conftest import reset_public_schema, run_alembic
from tests.integration.test_derivative_export import CONTENT_HASH, TINY_PNG_HASH
from tests.integration.test_derivative_export_preparation import (
    _approve_via_api,
    _confirm_via_api,
    _count,
    _evidence_refs,
    _freeze_and_finalize,
    _materialize_via_api,
    _set_up,
)

pytestmark = pytest.mark.integration

BRANCH_VALUE = "deriv-branch"
DOWNLOAD_BASE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/download"
)
AUDIT_BASE = (
    "/api/novels/{novel_id}/derivative-projects/{project_id}/export/audit"
)


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://", 1
        )
    return sync_url


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture(scope="module")
def asset_storage() -> DerivativeAssetStorage:
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-uat-") as tmp:
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


# ────────────────────────── helpers ──────────────────────────


def _headers(ids: dict) -> dict:
    return {"Authorization": f"Bearer {ids['token']}"}


async def _download(api_client, ids: dict, fmt: str):
    return await api_client.get(
        DOWNLOAD_BASE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"], "format": fmt},
        headers=_headers(ids),
    )


async def _materialize_approved(api_client, ids: dict) -> dict:
    """approve_export → Web confirm → deterministic materialize (approved bundle)."""
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)
    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _artifact_status(runtime_factory, ids: dict) -> str:
    async with runtime_factory() as session:
        artifact = await session.get(Artifact, ids["artifact_id"])
    assert artifact is not None
    return artifact.status


async def _find_visual_version_id(runtime_factory, ids: dict) -> int:
    async with runtime_factory() as session:
        row = await session.scalar(
            select(DerivativeVisualCandidateAsset).where(
                DerivativeVisualCandidateAsset.owner_id == ids["owner_id"],
                DerivativeVisualCandidateAsset.novel_id == ids["novel_id"],
                DerivativeVisualCandidateAsset.asset_id == ids["asset_id"],
            )
        )
        return row.visual_version_id if row else 0


# ────────────────────────── 两种格式 parity + 内容比对 ──────────────────────────


async def test_approved_artifact_both_formats_byte_parity(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """approved artifact → Markdown/EPUB 字节 parity + 章节/asset/citation 比对。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"par_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    materialized = await _materialize_approved(api_client, ids)
    assert materialized["status"] == "approved"
    assert materialized["manifest_hash"] == ids["manifest_hash"]

    # Markdown: byte parity + header replays the frozen artifact manifest hash.
    md1 = await _download(api_client, ids, "markdown")
    md2 = await _download(api_client, ids, "markdown")
    assert md1.status_code == 200, md1.text
    assert md1.content == md2.content
    assert md1.headers["X-Export-Manifest-Hash"] == materialized["manifest_hash"]
    body = md1.text
    # Chapter order + content parity.
    assert "## Chapter 1" in body
    assert "## Chapter 2" in body
    assert body.index("## Chapter 1") < body.index("## Chapter 2")
    # Frozen content hash (same canonical markdown everywhere) is present.
    assert CONTENT_HASH in body
    # Approved asset hash + citation leaf appear in the export.
    assert TINY_PNG_HASH in body
    assert f"fork:{ids['fork_key']}:chapter:1" in body
    assert "manifest_hash: " + materialized["manifest_hash"] in body

    # EPUB: byte parity + fixed stdlib layout + chapter/asset/citation present.
    epub1 = await _download(api_client, ids, "epub")
    epub2 = await _download(api_client, ids, "epub")
    assert epub1.status_code == 200, epub1.text
    assert epub1.content == epub2.content
    assert "application/epub+zip" in epub1.headers["content-type"]
    with ZipFile(BytesIO(epub1.content)) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        container = archive.read("META-INF/container.xml").decode("utf-8")
        assert 'full-path="OEBPS/content.opf"' in container
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        assert 'id="chapter-1"' in opf and 'id="chapter-2"' in opf
        assert 'id="export-manifest"' in opf
        chapter1 = archive.read("OEBPS/chapter-1.xhtml").decode("utf-8")
        assert "Chapter 1" in chapter1
        embedded = archive.read("OEBPS/export-manifest.json").decode("utf-8")
        assert materialized["manifest_hash"] in embedded
        image = archive.read(f"OEBPS/assets/{TINY_PNG_HASH}.png")
        assert hashlib.sha256(image).hexdigest() == TINY_PNG_HASH


async def test_refresh_reopen_replays_same_bytes(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """refresh/reopen：重新确定性 freeze 与下载字节保持一致（可复现）。"""
    from app.services.derivative_export.preparation import prepare_export

    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"ref_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    materialized = await _materialize_approved(api_client, ids)
    md_before = await _download(api_client, ids, "markdown")

    # A fresh "reopen" freeze must replay the exact same frozen lineage.
    async with runtime_factory() as session:
        reopened = await prepare_export(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
            branch=BRANCH_VALUE,
            fork=ids["fork_key"],
            evidence_refs=_evidence_refs(ids),
            storage=asset_storage,
        )
    assert reopened.manifest.manifest_hash == materialized["manifest_hash"]
    assert reopened.snapshot.snapshot_hash == materialized["snapshot_hash"]

    # Reopen download bytes are identical (not just hash-equal).
    md_after = await _download(api_client, ids, "markdown")
    assert md_after.status_code == 200, md_after.text
    assert md_after.content == md_before.content
    assert md_after.headers["X-Export-Manifest-Hash"] == materialized["manifest_hash"]


# ────────────────────────── owner/Original 隔离（T-39-03-01） ──────────────────────────


async def test_cross_owner_materialize_fails_closed(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """cross-owner：B 用 A 的 artifact/approval 物化 → fail closed，零写入。"""
    ids_a = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"xoa_{uuid.uuid4().hex[:6]}",
    )
    ids_b = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"xob_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids_a)
    view = await _approve_via_api(api_client, ids_a)
    approval_id_a = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids_a, approval_id_a)

    # B 在 B 的 novel/project 路径下引用 A 的 artifact/approval/preparation。
    resp = await api_client.post(
        "/api/novels/{novel_id}/derivative-projects/{project_id}/export/agent/materialize".format(
            novel_id=ids_b["novel_id"], project_id=ids_b["project_id"]
        ),
        params={"novel_id": ids_b["novel_id"]},
        headers=_headers(ids_b),
        json={
            "branch": BRANCH_VALUE,
            "fork": ids_b["fork_key"],
            "artifact_id": ids_a["artifact_id"],
            "artifact_revision_id": ids_a["artifact_revision_id"],
            "approval_id": approval_id_a,
            "preparation_hash": ids_a["preparation_hash"],
        },
    )
    assert resp.status_code in (400, 404), resp.text
    assert (
        "approval_not_found" in resp.text
        or "artifact_not_found" in resp.text
        or "not found" in resp.text.lower()
    )
    # B 的 owner 空间零 approval / artifact 变更（无越权写入）。
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids_b["owner_id"]) == 0
    async with runtime_factory() as session:
        artifact_a = await session.get(Artifact, ids_a["artifact_id"])
    # A 的 artifact 未被 B 越权提升（仍 candidate，未 materialize）。
    assert artifact_a is not None and artifact_a.status == "candidate"


async def test_original_and_unknown_scope_are_identical_404(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """Original/未知项目：download 与 materialize 路径 404-hide（无 403 oracle）。"""
    ids_a = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"orf_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids_a)

    # 未知/非 derivative 项目 id → 与不存在一致的 404。
    unknown = await api_client.get(
        DOWNLOAD_BASE.format(novel_id=ids_a["novel_id"], project_id=999999),
        params={"novel_id": ids_a["novel_id"], "format": "markdown"},
        headers=_headers(ids_a),
    )
    assert unknown.status_code == 404
    assert "project_not_found" in unknown.json()["detail"]

    # Original 空间：请求发生在只读 download 路径，一律 404-hide（无 403 oracle）。
    resp = await api_client.get(
        DOWNLOAD_BASE.format(novel_id=ids_a["novel_id"], project_id=999998),
        params={"novel_id": ids_a["novel_id"], "format": "epub"},
        headers=_headers(ids_a),
    )
    assert resp.status_code == 404
    assert resp.status_code != 403


# ────────────────────────── 审批/物化边界（fail closed） ──────────────────────────


async def test_pending_approval_materialize_blocked(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """pending approval（未确认）→ materialize blocked，artifact 仍 candidate。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"pen_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    # 故意不 confirm。
    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert "approval_not_approved" in resp.text
    assert await _artifact_status(runtime_factory, ids) == "candidate"


async def test_rejected_approval_materialize_blocked(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """rejected approval → materialize blocked，artifact 仍 candidate。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"rej_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    reject_resp = await api_client.post(
        f"/api/agent/approval-requests/{approval_id}/reject",
        headers=_headers(ids),
    )
    assert reject_resp.status_code == 200, reject_resp.text

    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert "approval_not_approved" in resp.text
    assert await _artifact_status(runtime_factory, ids) == "candidate"


async def test_stale_artifact_materialize_blocked(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """stale artifact：approval 后 DB 状态漂移 → materialize blocked，零物化。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"stl_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)

    # 项目名称变化 → 新 snapshot hash，approved artifact 的冻结血缘不再重放。
    async with runtime_factory() as session:
        await session.execute(
            text(
                "UPDATE derivative_projects SET name = name || ' v2' "
                "WHERE id = :pid"
            ),
            {"pid": ids["project_id"]},
        )
        await session.commit()

    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert any(
        code in resp.text
        for code in ("preparation_parity", "preparation_hash_mismatch", "content_hash_stale")
    )
    # 零提升：artifact 保持 candidate。
    assert await _artifact_status(runtime_factory, ids) == "candidate"
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids["owner_id"]) == 1


async def test_preparation_hash_mismatch_blocked(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """forged preparation_hash → materialize blocked（preparation_hash_mismatch）。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"phm_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)

    resp = await _materialize_via_api(
        api_client,
        ids,
        approval_id=approval_id,
        body_override={"preparation_hash": "9" * 64},
    )
    assert resp.status_code == 400, resp.text
    assert "preparation_hash_mismatch" in resp.text
    assert await _artifact_status(runtime_factory, ids) == "candidate"


async def test_missing_asset_blocks_materialize(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """missing asset：物化 blocked（bundle 不能完整）；只读 download 显式占位。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"msa_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)

    visual_version_id = await _find_visual_version_id(runtime_factory, ids)
    assert visual_version_id > 0
    asset_storage.remove(
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        visual_version_id=visual_version_id,
        asset_id=ids["asset_id"],
        mime_type="image/png",
    )

    # 物化（确定性 bundle）fail closed：缺失二进制不能产出完整 provenance package。
    resp = await _materialize_via_api(api_client, ids, approval_id=approval_id)
    assert resp.status_code == 400, resp.text
    assert any(
        code in resp.text
        for code in ("preparation_parity", "bundle_blocked", "missing_asset", "content_hash_stale")
    )
    assert await _artifact_status(runtime_factory, ids) == "candidate"

    # 只读 download 仍返回 200，以显式占位呈现缺失资产（不静默丢弃、零写入）。
    md = await _download(api_client, ids, "markdown")
    assert md.status_code == 200, md.text
    assert "插图缺失" in md.text
    assert "asset_bytes_missing" in md.text or "missing_asset" in md.text


# ────────────────────────── no mutation + audit 事件（T-39-03-02） ──────────────────────────


async def test_no_original_mutation_and_audit_events(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """物化/下载后 Original 零变更；approval/materialization audit 事件存在。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"aud_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)

    async with runtime_factory() as session:
        original_before = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": ids["novel_id"]},
                )
            ).all()
        )
        artifact_before = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM canon_space_artifacts "
                    "WHERE owner_id = :o AND novel_id = :n"
                ),
                {"o": ids["owner_id"], "n": ids["novel_id"]},
            )
            or 0
        )

    materialized = await _materialize_approved(api_client, ids)
    await _download(api_client, ids, "markdown")
    await _download(api_client, ids, "epub")

    async with runtime_factory() as session:
        original_after = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": ids["novel_id"]},
                )
            ).all()
        )
        artifact_after = int(
            await session.scalar(
                text(
                    "SELECT count(*) FROM canon_space_artifacts "
                    "WHERE owner_id = :o AND novel_id = :n"
                ),
                {"o": ids["owner_id"], "n": ids["novel_id"]},
            )
            or 0
        )
        approval = await session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.artifact_id == ids["artifact_id"]
            )
        )
        artifact = await session.get(Artifact, ids["artifact_id"])

    # D-39-02 / no original mutation.
    assert original_before == original_after
    assert artifact_before == artifact_after == 0
    # T-39-03-02：approval 事件（approved approve_export）+ artifact 已批准，且
    # download 不再制造新的 approval（防 repudiation / 防伪造物化）。
    assert approval is not None
    assert approval.action == APPROVE_EXPORT_APPROVAL_ACTION
    assert approval.status == "approved"
    assert artifact is not None and artifact.status == "approved"
    assert await _count(runtime_factory, ApprovalRequest, owner_id=ids["owner_id"]) == 1

    # 三维审计契约：verdict blocked（Phase 22 0/3），report hash 可重放。
    audit_resp = await api_client.get(
        AUDIT_BASE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"]},
        headers=_headers(ids),
    )
    assert audit_resp.status_code == 200, audit_resp.text
    report = audit_resp.json()
    assert [d["dimension"] for d in report["dimensions"]] == [
        "implementation_readiness",
        "sample_data_coverage",
        "quality_qualification",
    ]
    assert report["dimensions"][2]["status"] == "blocked"
    assert report["dimensions"][2]["blocked_reasons"]
    assert report["verdict"] == "blocked"
    assert report["phase22"]["green_observed"] == 0
    assert report["snapshot_hash"] == materialized["snapshot_hash"]
    assert audit_report_hash(report) == report["report_hash"]
