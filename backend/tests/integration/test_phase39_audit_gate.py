"""Phase 39-04 independent audit gate integration tests (T-39-04-01/02).

Proves the release-gate audit against the real CI PostgreSQL (REQ-FORK-05 /
REQ-SHIP-01 / REQ-SHIP-02 / D-39-03 / D-39-04):

- after the full preparation -> approval -> deterministic materialization ->
  download flow, ``run_derivative_export_lineage_audit`` independently
  recomputes the complete lineage (source snapshot -> manifest -> preparation
  hash -> preparation payload -> artifact -> approve_export approval ->
  materialized bundle -> download/audit event) and every hash link replays;
- the final three-dimension report carries the lineage + REQ-SHIP-01 baseline,
  keeps Phase 22 at 0/3 as an independent risk, and its report hash replays;
- ``qualified_candidate`` only appears when every dimension, lineage check and
  shipment item is verified; any failing item stays BLOCKED in the report —
  never deleted or downgraded;
- a project with an orphaned lineage (no ExportPreparationArtifact/approval/
  bundle) fails closed with explicit blocked checks.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.main import app
from app.services.derivative_export.audit import (
    REQ_SHIP01_REQUIREMENTS,
    DerivativeExportAuditEvidence,
    DerivativeExportAuditStatus,
    DerivativeExportLineageCheckKind,
    DerivativeExportPhase22Evidence,
    DerivativeExportShipmentEvidenceStatus,
    DerivativeExportShipmentItem,
    audit_derivative_export_lineage,
    audit_report_hash,
    build_derivative_export_audit,
    build_derivative_export_shipment_baseline,
    run_derivative_export_lineage_audit,
)
from app.services.derivative_export.materializer import (
    set_materializer_asset_storage,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    FORK_ID,
    FORK_KEY,
    HEX64_E,
    NOVEL_ID,
    OWNER_ID,
    PROJECT_ID,
    build_fixture_snapshot,
    seal_fixture_manifest,
)
from tests.integration.conftest import reset_public_schema, run_alembic
from tests.integration.test_derivative_export_preparation import (
    _approve_via_api,
    _confirm_via_api,
    _evidence_refs,
    _freeze_and_finalize,
    _materialize_via_api,
    _set_up,
)
from app.services.derivative_export.preparation import (
    export_preparation_hash,
    export_preparation_payload,
)
from app.services.derivative_export.manifest import seal_derivative_export_manifest
from app.services.derivative_export.snapshot import ExportSnapshotService

pytestmark = pytest.mark.integration

BRANCH_VALUE = "deriv-branch"
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
    with tempfile.TemporaryDirectory(prefix="novelmind-deriv-audit-") as tmp:
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


def _audit_evidence(kind: str = "package_buildable") -> tuple[DerivativeExportAuditEvidence, ...]:
    return (
        DerivativeExportAuditEvidence(
            kind=kind, location="backend/tests", detail="evidence present"
        ),
    )


def _verified_shipment() -> object:
    return build_derivative_export_shipment_baseline(
        [
            DerivativeExportShipmentItem(
                requirement=req,
                status=DerivativeExportShipmentEvidenceStatus.VERIFIED,
                raw_evidence_link="backend/tests",
                detail="verified",
            )
            for req in REQ_SHIP01_REQUIREMENTS
        ]
    )


def _phase22(green_observed: int = 3) -> DerivativeExportPhase22Evidence:
    return DerivativeExportPhase22Evidence(
        green_observed=green_observed,
        source=".planning/STATE.md",
        source_hash="a" * 64,
    )


def _verified_lineage(*, tamper: dict | None = None) -> object:
    """A fully-verified pure lineage over the fixture snapshot."""
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    payload = export_preparation_payload(
        snapshot,
        manifest,
        branch=BRANCH_VALUE,
        fork=FORK_KEY,
        evidence_refs=["fork:ff-fixture:chapter:1"],
    )
    preparation_hash = export_preparation_hash(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH_VALUE,
        fork=FORK_KEY,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
    )
    args = dict(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
        preparation_hash=preparation_hash,
        snapshot=snapshot,
        preparation_payload=payload,
        artifact_status="approved",
        artifact_revision_id=150,
        artifact_preparation_hash=preparation_hash,
        approval_action="approve_export",
        approval_status="approved",
        approval_artifact_revision_id=150,
        approval_payload_hash=preparation_hash,
        branch=BRANCH_VALUE,
        fork=FORK_KEY,
        package_hash=HEX64_E,
        replayed_package_hash=HEX64_E,
        download_manifest_hash=manifest.manifest_hash,
        epub_validated=True,
    )
    args.update(tamper or {})
    return audit_derivative_export_lineage(**args)


def _build_report(*, phase22_green: int = 3, lineage=None, shipment=None):
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    return build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(phase22_green),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
        lineage=lineage,
        shipment=shipment,
    )


# ────────────────────────── 完整 flow lineage 可复算 ──────────────────────────


async def test_full_flow_lineage_is_independently_recomputable(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """preparation → approval → materialize → download 后，lineage 可复算。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"lin_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)
    view = await _approve_via_api(api_client, ids)
    approval_id = int(view["approval_request_id"])
    await _confirm_via_api(api_client, ids, approval_id)
    materialized = (await _materialize_via_api(api_client, ids, approval_id=approval_id)).json()
    assert materialized["status"] == "approved"

    async with runtime_factory() as session:
        lineage = await run_derivative_export_lineage_audit(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
            storage=asset_storage,
            epub_validated=False,
        )

    # All hash/lineage links replay; only EPUB interoperability is honestly
    # unverified (no validator) and therefore blocked — never green.
    verified = {
        check.kind.value
        for check in lineage.checks
        if check.status == DerivativeExportAuditStatus.VERIFIED
    }
    assert {
        DerivativeExportLineageCheckKind.SOURCE_SNAPSHOT.value,
        DerivativeExportLineageCheckKind.MANIFEST.value,
        DerivativeExportLineageCheckKind.PARITY.value,
        DerivativeExportLineageCheckKind.PREPARATION_HASH.value,
        DerivativeExportLineageCheckKind.PREPARATION_PAYLOAD.value,
        DerivativeExportLineageCheckKind.ARTIFACT_BINDING.value,
        DerivativeExportLineageCheckKind.APPROVAL_BINDING.value,
        DerivativeExportLineageCheckKind.MATERIALIZATION.value,
        DerivativeExportLineageCheckKind.DOWNLOAD_AUDIT.value,
    } <= verified
    blocked = {
        check.kind.value
        for check in lineage.checks
        if check.status == DerivativeExportAuditStatus.BLOCKED
    }
    assert blocked == {DerivativeExportLineageCheckKind.EPUB_VALIDATION.value}

    # Independent recompute of the preparation hash matches the frozen lineage.
    async with runtime_factory() as session:
        frozen = await ExportSnapshotService(
            session, storage=asset_storage
        ).build(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
        )
        manifest = seal_derivative_export_manifest(frozen.snapshot)
        recomputed = export_preparation_hash(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
            fork_id=frozen.snapshot.fork_id,
            branch=BRANCH_VALUE,
            fork=ids["fork_key"],
            snapshot_hash=frozen.snapshot.snapshot_hash,
            manifest_hash=manifest.manifest_hash,
        )
    assert recomputed == ids["preparation_hash"]
    assert recomputed == materialized["preparation_hash"]


# ────────────────────────── 最终报告：Phase 22 独立风险 + lineage/shipment ─────


async def test_final_report_carries_lineage_shipment_and_phase22_risk(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """最终 audit 报告：Phase 22 0/3 独立、lineage/shipment 在场、hash 可重放。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"fin_{uuid.uuid4().hex[:6]}",
    )
    await _freeze_and_finalize(runtime_factory, api_client, ids)

    resp = await api_client.get(
        AUDIT_BASE.format(novel_id=ids["novel_id"], project_id=ids["project_id"]),
        params={"novel_id": ids["novel_id"]},
        headers={"Authorization": f"Bearer {ids['token']}"},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    # Phase 22 stays an independent 0/3 risk in the final report.
    assert report["phase22"]["green_observed"] == 0
    assert report["dimensions"][2]["status"] == "blocked"
    assert report["verdict"] == "blocked"
    # The independent lineage + REQ-SHIP-01 baseline are present and honest.
    assert report["lineage"]["checks"]
    assert report["shipment"]["items"]
    assert any(
        item["requirement"] == "tls"
        for item in report["shipment"]["items"]
    )
    # No active pointer / promotion vocabulary anywhere in the report.
    for reason in report["blocked_reasons"]:
        assert "promote" not in reason and "production_ready" not in reason
    assert audit_report_hash(report) == report["report_hash"]


# ────────────────────────── qualified_candidate 边界 ─────────────────────────


def test_qualified_candidate_only_when_all_evidence_satisfied():
    report = _build_report(
        phase22_green=3,
        lineage=_verified_lineage(),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()
    assert report.dimensions[0].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[1].status == DerivativeExportAuditStatus.VERIFIED
    assert report.dimensions[2].status == DerivativeExportAuditStatus.VERIFIED
    assert audit_report_hash(report) == report.report_hash


def test_failing_item_stays_blocked_not_deleted_or_downgraded():
    """任一失败项保持 BLOCKED：不删除、不降级、不绕过。"""
    report = _build_report(
        phase22_green=3,
        lineage=_verified_lineage(
            tamper={
                "approval_status": "rejected",
                "package_hash": None,
            }
        ),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "blocked"
    assert "lineage_blocked:approval_binding" in report.blocked_reasons
    assert "lineage_blocked:materialization" in report.blocked_reasons
    # The failing checks remain visible in the report with their reasons.
    kinds = {check.kind.value for check in report.lineage.checks}
    assert "approval_binding" in kinds
    assert "materialization" in kinds
    approval_check = next(
        check
        for check in report.lineage.checks
        if check.kind.value == "approval_binding"
    )
    assert approval_check.status == DerivativeExportAuditStatus.BLOCKED
    assert "approval_not_approved" in approval_check.blocked_reasons
    materialization_check = next(
        check
        for check in report.lineage.checks
        if check.kind.value == "materialization"
    )
    assert materialization_check.status == DerivativeExportAuditStatus.BLOCKED


def test_phase22_block_alone_blocks_the_report():
    """即使 lineage + shipment 全绿，Phase 22 0/3 仍独立阻断（D-39-04）。"""
    report = _build_report(
        phase22_green=0,
        lineage=_verified_lineage(),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "blocked"
    assert report.dimensions[2].status == DerivativeExportAuditStatus.BLOCKED
    assert report.blocked_reasons
    assert report.phase22.green_observed == 0


# ────────────────────────── 孤立 lineage（无 artifact/approval/bundle） ───────


async def test_orphaned_lineage_fails_closed_in_db_gate(
    runtime_factory, migrated_postgres: str, api_client, asset_storage
):
    """无 ExportPreparationArtifact / approval / bundle 的项目 → 显式 blocked。"""
    ids = await _set_up(
        runtime_factory,
        migrated_postgres,
        asset_storage,
        suffix=f"orp_{uuid.uuid4().hex[:6]}",
    )
    # 未 finalize → 无候选 artifact、无 approval、无 bundle。

    async with runtime_factory() as session:
        lineage = await run_derivative_export_lineage_audit(
            session,
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            project_id=ids["project_id"],
            storage=asset_storage,
            epub_validated=True,
        )
    blocked = {
        check.kind.value
        for check in lineage.checks
        if check.status == DerivativeExportAuditStatus.BLOCKED
    }
    assert "artifact_binding" in blocked
    assert "approval_binding" in blocked
    assert "materialization" in blocked
    reasons = {
        reason
        for check in lineage.checks
        for reason in check.blocked_reasons
    }
    assert "artifact_evidence_missing" in reasons
    assert "approval_evidence_missing" in reasons
    assert "bundle_evidence_missing" in reasons
