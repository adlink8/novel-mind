"""Phase 39-04 REQ-SHIP-01 production baseline evidence tests (D-39-04).

Pure, database-free coverage of the honest production-baseline audit:

- every one of the five REQ-SHIP-01 requirements (TLS, secret sourcing /
  rotation, backup/restore drill, monitoring/alert, cost budget) must carry
  evidence — a missing requirement fails closed as ``blocked``;
- blocked and unverified items are honestly reported (never silently green),
  and any non-verified baseline fails the release-gate report verdict closed;
- the baseline only reports the evidence that actually exists — it never
  invents TLS / secret / backup / monitoring / cost-budget evidence.

Repo truth (2026-08-05): ``docs/DEPLOYMENT.md#Production-Blockers`` records no
TLS ingress, no secret manager, no backup/restore drill and no monitoring and
alerting; only provider-key encryption/rotation compatibility and per-skill-run
budget contracts exist — so the honest gate is blocked/unverified, never green.
"""

from __future__ import annotations

import pytest

from app.services.derivative_export.audit import (
    REQ_SHIP01_REQUIREMENTS,
    DerivativeExportAuditEvidence,
    DerivativeExportAuditReport,
    DerivativeExportAuditStatus,
    DerivativeExportPhase22Evidence,
    DerivativeExportShipmentBaseline,
    DerivativeExportShipmentEvidenceStatus,
    DerivativeExportShipmentItem,
    DerivativeExportShipmentRequirement,
    build_derivative_export_audit,
    build_derivative_export_shipment_baseline,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    build_fixture_snapshot,
    seal_fixture_manifest,
)

pytestmark = pytest.mark.unit

VERIFIED = DerivativeExportShipmentEvidenceStatus.VERIFIED
UNVERIFIED = DerivativeExportShipmentEvidenceStatus.UNVERIFIED
BLOCKED = DerivativeExportShipmentEvidenceStatus.BLOCKED

_REQUIREMENTS = tuple(REQ_SHIP01_REQUIREMENTS)


def _item(
    req: DerivativeExportShipmentRequirement,
    status: DerivativeExportShipmentEvidenceStatus = VERIFIED,
) -> DerivativeExportShipmentItem:
    return DerivativeExportShipmentItem(
        requirement=req,
        status=status,
        raw_evidence_link="backend/tests",
        detail=f"evidence:{status.value}",
    )


def _all_verified() -> tuple[DerivativeExportShipmentItem, ...]:
    return tuple(_item(req) for req in _REQUIREMENTS)


def _audit_evidence(kind: str = "package_buildable") -> tuple[DerivativeExportAuditEvidence, ...]:
    return (
        DerivativeExportAuditEvidence(
            kind=kind, location="backend/tests", detail="evidence present"
        ),
    )


def _phase22(green_observed: int = 3) -> DerivativeExportPhase22Evidence:
    return DerivativeExportPhase22Evidence(
        green_observed=green_observed,
        source=".planning/STATE.md",
        source_hash="a" * 64,
    )


# ---------------------------------------------------------------------------
# Missing evidence fails closed
# ---------------------------------------------------------------------------


def test_empty_baseline_blocks_every_requirement():
    baseline = build_derivative_export_shipment_baseline([])
    assert baseline.status == DerivativeExportAuditStatus.BLOCKED
    assert {item.requirement for item in baseline.items} == set(_REQUIREMENTS)
    assert all(
        item.status == DerivativeExportShipmentEvidenceStatus.BLOCKED
        for item in baseline.items
    )
    for req in _REQUIREMENTS:
        assert f"shipment_evidence_missing:{req.value}" in baseline.blocked_reasons


def test_missing_single_requirement_blocks_the_baseline():
    provided = tuple(
        _item(req) for req in _REQUIREMENTS if req != DerivativeExportShipmentRequirement.COST_BUDGET
    )
    baseline = build_derivative_export_shipment_baseline(provided)
    assert baseline.status == DerivativeExportAuditStatus.BLOCKED
    assert "shipment_evidence_missing:cost_budget" in baseline.blocked_reasons


# ---------------------------------------------------------------------------
# Honest status reporting (verified / unverified / blocked)
# ---------------------------------------------------------------------------


def test_all_verified_baseline_is_verified():
    baseline = build_derivative_export_shipment_baseline(_all_verified())
    assert baseline.status == DerivativeExportAuditStatus.VERIFIED
    assert baseline.blocked_reasons == ()


def test_unverified_item_is_honest_partial_and_fails_closed():
    items = list(_all_verified())
    items[0] = _item(_REQUIREMENTS[0], UNVERIFIED)
    baseline = build_derivative_export_shipment_baseline(items)
    assert baseline.status == DerivativeExportAuditStatus.PARTIAL
    assert "shipment_unverified:tls" in baseline.blocked_reasons


def test_blocked_item_blocks_the_baseline():
    items = list(_all_verified())
    items[2] = _item(_REQUIREMENTS[2], BLOCKED)
    baseline = build_derivative_export_shipment_baseline(items)
    assert baseline.status == DerivativeExportAuditStatus.BLOCKED
    assert "shipment_blocked:backup_restore_drill" in baseline.blocked_reasons


def test_baseline_model_rejects_unknown_fields():
    with pytest.raises(Exception):
        DerivativeExportShipmentBaseline.model_validate(
            {"schema_version": "x", "items": [], "status": "verified", "hacked": True}
        )


# ---------------------------------------------------------------------------
# Release-gate folding (D-39-04)
# ---------------------------------------------------------------------------


def _build_report(shipment, *, phase22_green: int = 3):
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    return build_derivative_export_audit(
        manifest=manifest,
        phase22=_phase22(phase22_green),
        implementation_evidence=_audit_evidence("package_buildable"),
        sample_data_evidence=_audit_evidence("roundtrip_fixtures_present"),
        shipment=shipment,
    )


def test_missing_production_baseline_blocks_the_report():
    report = _build_report(build_derivative_export_shipment_baseline([]))
    assert report.verdict == "blocked"
    assert "shipment_evidence_missing:tls" in report.blocked_reasons
    assert "shipment_evidence_missing:backup_restore_drill" in report.blocked_reasons
    # The honest baseline stays visible in the report — never deleted.
    assert report.shipment is not None
    assert report.shipment.status == DerivativeExportAuditStatus.BLOCKED


def test_blocked_production_baseline_blocks_the_report_even_with_green_phase22():
    items = list(_all_verified())
    items[0] = _item(_REQUIREMENTS[0], BLOCKED)
    report = _build_report(build_derivative_export_shipment_baseline(items))
    assert report.verdict == "blocked"
    assert "shipment_blocked:tls" in report.blocked_reasons


def test_verified_baseline_allows_candidate_verdict():
    report = _build_report(build_derivative_export_shipment_baseline(_all_verified()))
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()


def test_non_verified_baseline_cannot_be_forced_green():
    baseline = build_derivative_export_shipment_baseline([])
    report = _build_report(baseline)
    data = report.model_dump(mode="json")
    data["verdict"] = "qualified_candidate"
    data["blocked_reasons"] = []
    with pytest.raises(ValueError):
        DerivativeExportAuditReport.model_validate(data)


def test_repo_truth_honest_state_blocks_ship():
    """The real repo baseline (no TLS/secret-manager/backup/monitoring; only
    per-run budgets) must honestly report blocked/unverified — never green."""
    items = (
        DerivativeExportShipmentItem(
            requirement=DerivativeExportShipmentRequirement.TLS,
            status=DerivativeExportShipmentEvidenceStatus.BLOCKED,
            raw_evidence_link="docs/DEPLOYMENT.md#Production-Blockers",
            detail="no TLS ingress evidence",
        ),
        DerivativeExportShipmentItem(
            requirement=DerivativeExportShipmentRequirement.SECRET_SOURCING_ROTATION,
            status=DerivativeExportShipmentEvidenceStatus.UNVERIFIED,
            raw_evidence_link="docs/DEPLOYMENT.md",
            detail="provider key encryption/rotation only",
        ),
        DerivativeExportShipmentItem(
            requirement=DerivativeExportShipmentRequirement.BACKUP_RESTORE_DRILL,
            status=DerivativeExportShipmentEvidenceStatus.BLOCKED,
            raw_evidence_link="docs/DEPLOYMENT.md#Production-Blockers",
            detail="no backup/restore drill evidence",
        ),
        DerivativeExportShipmentItem(
            requirement=DerivativeExportShipmentRequirement.MONITORING_ALERT,
            status=DerivativeExportShipmentEvidenceStatus.BLOCKED,
            raw_evidence_link="docs/DEPLOYMENT.md#Production-Blockers",
            detail="no monitoring/alerting evidence",
        ),
        DerivativeExportShipmentItem(
            requirement=DerivativeExportShipmentRequirement.COST_BUDGET,
            status=DerivativeExportShipmentEvidenceStatus.UNVERIFIED,
            raw_evidence_link="backend/app/models/agent_runtime.py:SkillRun",
            detail="only per-skill-run budget contracts",
        ),
    )
    baseline = build_derivative_export_shipment_baseline(items)
    assert baseline.status == DerivativeExportAuditStatus.BLOCKED
    assert "shipment_blocked:tls" in baseline.blocked_reasons
    assert "shipment_unverified:secret_sourcing_rotation" in baseline.blocked_reasons
    report = _build_report(baseline)
    assert report.verdict == "blocked"
