"""Phase 39-04 derivative export security audit tests (T-39-04-01/02).

Pure, database-free red-team coverage of the independent Phase 39 release-gate
audit (REQ-FORK-05 / REQ-SHIP-02 / REQ-CRE-02):

- a clean frozen lineage replays every hash link — source snapshot -> frozen
  manifest -> preparation hash -> preparation payload -> ExportPreparationArtifact
  -> approve_export ApprovalRequest -> materialized bundle -> download/audit event
  (T-39-04-01 repudiation guard: every check carries a raw evidence link);
- a contaminated / Original-space snapshot, an orphaned / pending / rejected
  ExportPreparationArtifact, an approval hash mismatch, source revision drift,
  a tampered bundle package manifest, an unverified EPUB and a missing
  download/audit event all fail closed into explicit blocked checks;
- the release gate verdict can only be ``qualified_candidate`` or ``blocked``
  (T-39-04-02: no promotion path), and a non-verified lineage or REQ-SHIP-01
  baseline can never be forced green.

Every assertion names the explicit blocked reason code — never a silent pass.
"""

from __future__ import annotations

import pytest

from app.services.derivative_export.audit import (
    FORBIDDEN_AUDIT_WORDS,
    REQ_SHIP01_REQUIREMENTS,
    DerivativeExportAuditEvidence,
    DerivativeExportAuditReport,
    DerivativeExportAuditStatus,
    DerivativeExportLineageCheckKind,
    DerivativeExportPhase22Evidence,
    DerivativeExportShipmentEvidenceStatus,
    DerivativeExportShipmentItem,
    audit_derivative_export_lineage,
    build_derivative_export_audit,
    build_derivative_export_shipment_baseline,
)
from app.services.derivative_export.preparation import (
    export_preparation_hash,
    export_preparation_payload,
)
from app.services.derivative_export.snapshot import seal_export_snapshot
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

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BRANCH = "deriv-branch"
VERIFIED = DerivativeExportAuditStatus.VERIFIED
BLOCKED = DerivativeExportAuditStatus.BLOCKED


def _phase22(green_observed: int = 3) -> DerivativeExportPhase22Evidence:
    return DerivativeExportPhase22Evidence(
        green_observed=green_observed,
        source=".planning/STATE.md",
        source_hash="a" * 64,
    )


def _audit_evidence(
    kind: str = "package_buildable",
) -> tuple[DerivativeExportAuditEvidence, ...]:
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


def _clean_lineage(*, epub_validated: bool = True, **overrides):
    """A fully-verified lineage audit over the frozen fixture snapshot."""
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    payload = export_preparation_payload(
        snapshot,
        manifest,
        branch=BRANCH,
        fork=FORK_KEY,
        evidence_refs=["fork:ff-fixture:chapter:1"],
    )
    preparation_hash = export_preparation_hash(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK_KEY,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
    )
    base = dict(
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
        branch=BRANCH,
        fork=FORK_KEY,
        package_hash=HEX64_E,
        replayed_package_hash=HEX64_E,
        download_manifest_hash=manifest.manifest_hash,
        epub_validated=epub_validated,
    )
    base.update(overrides)
    return audit_derivative_export_lineage(**base)


def _blocked_reasons(lineage) -> set[str]:
    return {reason for check in lineage.checks for reason in check.blocked_reasons}


def _blocked_kinds(lineage) -> set[str]:
    return {
        check.kind.value
        for check in lineage.checks
        if check.status == DerivativeExportAuditStatus.BLOCKED
    }


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


# ---------------------------------------------------------------------------
# Clean lineage: every hash link replays + raw evidence links (T-39-04-01)
# ---------------------------------------------------------------------------


def test_clean_lineage_all_links_replay_and_verified():
    lineage = _clean_lineage(epub_validated=True)
    verified = {
        check.kind.value
        for check in lineage.checks
        if check.status == DerivativeExportAuditStatus.VERIFIED
    }
    assert verified == {kind.value for kind in DerivativeExportLineageCheckKind}
    assert lineage.status == DerivativeExportAuditStatus.VERIFIED
    assert lineage.blocked_reasons == ()
    # Repudiation guard: every verdict carries a raw evidence link that can be
    # used to recompute the lineage (manifest/snapshot/artifact/approval/bundle).
    for check in lineage.checks:
        assert check.raw_evidence_link
        assert check.raw_evidence_link.startswith("backend/")


def test_clean_lineage_unverified_epub_is_blocked_not_green():
    lineage = _clean_lineage(epub_validated=False)
    assert _blocked_kinds(lineage) == {"epub_validation"}
    assert "epub_interoperability_unverified" in _blocked_reasons(lineage)


# ---------------------------------------------------------------------------
# Contamination / Original mutation (REQ-CRE-02)
# ---------------------------------------------------------------------------


def test_contaminated_original_space_snapshot_is_blocked():
    snapshot = build_fixture_snapshot()
    contaminated = seal_export_snapshot(
        snapshot.model_copy(update={"space": "original_canon"})
    )
    manifest = seal_fixture_manifest(contaminated)
    preparation_hash = export_preparation_hash(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK_KEY,
        snapshot_hash=contaminated.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
    )
    lineage = audit_derivative_export_lineage(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        snapshot_hash=contaminated.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
        preparation_hash=preparation_hash,
        snapshot=contaminated,
        preparation_payload=export_preparation_payload(
            contaminated,
            manifest,
            branch=BRANCH,
            fork=FORK_KEY,
            evidence_refs=["fork:ff-fixture:chapter:1"],
        ),
        artifact_status="approved",
        artifact_revision_id=150,
        artifact_preparation_hash=preparation_hash,
        approval_action="approve_export",
        approval_status="approved",
        approval_artifact_revision_id=150,
        approval_payload_hash=preparation_hash,
        branch=BRANCH,
        fork=FORK_KEY,
        package_hash=HEX64_E,
        replayed_package_hash=HEX64_E,
        download_manifest_hash=manifest.manifest_hash,
        epub_validated=True,
    )
    parity = next(
        check
        for check in lineage.checks
        if check.kind == DerivativeExportLineageCheckKind.PARITY
    )
    assert parity.status == DerivativeExportAuditStatus.BLOCKED
    assert "namespace_denied" in parity.blocked_reasons
    assert "lineage_blocked:parity" in lineage.blocked_reasons


def test_contaminated_original_asset_is_blocked():
    snapshot = build_fixture_snapshot()
    asset = snapshot.assets[0].model_copy(update={"namespace": "original_canon"})
    contaminated = seal_export_snapshot(
        snapshot.model_copy(update={"assets": (asset,)})
    )
    manifest = seal_fixture_manifest(contaminated)
    preparation_hash = export_preparation_hash(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK_KEY,
        snapshot_hash=contaminated.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
    )
    lineage = audit_derivative_export_lineage(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        snapshot_hash=contaminated.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
        preparation_hash=preparation_hash,
        snapshot=contaminated,
        preparation_payload=export_preparation_payload(
            contaminated,
            manifest,
            branch=BRANCH,
            fork=FORK_KEY,
            evidence_refs=["fork:ff-fixture:chapter:1"],
        ),
        artifact_status="approved",
        artifact_revision_id=150,
        artifact_preparation_hash=preparation_hash,
        approval_action="approve_export",
        approval_status="approved",
        approval_artifact_revision_id=150,
        approval_payload_hash=preparation_hash,
        branch=BRANCH,
        fork=FORK_KEY,
        package_hash=HEX64_E,
        replayed_package_hash=HEX64_E,
        download_manifest_hash=manifest.manifest_hash,
        epub_validated=True,
    )
    parity = next(
        check
        for check in lineage.checks
        if check.kind == DerivativeExportLineageCheckKind.PARITY
    )
    assert parity.status == DerivativeExportAuditStatus.BLOCKED
    assert "asset_namespace_denied" in parity.blocked_reasons


# ---------------------------------------------------------------------------
# Artifact lineage (orphaned / pending / rejected)
# ---------------------------------------------------------------------------


def test_orphaned_artifact_is_blocked():
    lineage = _clean_lineage(
        epub_validated=True,
        artifact_status=None,
        artifact_revision_id=None,
        artifact_preparation_hash=None,
    )
    assert "artifact_binding" in _blocked_kinds(lineage)
    assert "artifact_evidence_missing" in _blocked_reasons(lineage)


def test_pending_artifact_without_approval_is_blocked():
    lineage = _clean_lineage(
        epub_validated=True,
        artifact_status="candidate",
        approval_status="pending",
        approval_payload_hash=None,
        package_hash=None,
    )
    assert "approval_binding" in _blocked_kinds(lineage)
    assert "materialization" in _blocked_kinds(lineage)
    assert "approval_not_approved" in _blocked_reasons(lineage)
    assert "bundle_evidence_missing" in _blocked_reasons(lineage)


def test_rejected_artifact_is_blocked():
    lineage = _clean_lineage(epub_validated=True, artifact_status="rejected")
    assert "artifact_status_denied" in _blocked_reasons(lineage)
    assert "artifact_binding" in _blocked_kinds(lineage)


def test_divergent_artifact_preparation_hash_is_blocked():
    lineage = _clean_lineage(epub_validated=True, artifact_preparation_hash="9" * 64)
    assert "artifact_preparation_hash_mismatch" in _blocked_reasons(lineage)


# ---------------------------------------------------------------------------
# Approval lineage (hash mismatch / missing / unauthorized)
# ---------------------------------------------------------------------------


def test_approval_hash_mismatch_is_blocked():
    lineage = _clean_lineage(epub_validated=True, approval_payload_hash="9" * 64)
    assert "approval_hash_mismatch" in _blocked_reasons(lineage)


def test_missing_approval_is_blocked():
    lineage = _clean_lineage(
        epub_validated=True,
        approval_action=None,
        approval_status=None,
        approval_artifact_revision_id=None,
        approval_payload_hash=None,
        package_hash=None,
    )
    assert "approval_evidence_missing" in _blocked_reasons(lineage)
    assert "approval_binding" in _blocked_kinds(lineage)


def test_wrong_approval_action_is_blocked():
    lineage = _clean_lineage(epub_validated=True, approval_action="materialize_export")
    assert "approval_action_denied" in _blocked_reasons(lineage)


def test_approval_revision_mismatch_is_blocked():
    lineage = _clean_lineage(epub_validated=True, approval_artifact_revision_id=999)
    assert "approval_revision_mismatch" in _blocked_reasons(lineage)


# ---------------------------------------------------------------------------
# Source drift / bundle tampering / missing audit event
# ---------------------------------------------------------------------------


def test_source_revision_drift_is_blocked():
    lineage = _clean_lineage(epub_validated=True, snapshot_hash="2" * 64)
    assert "source_snapshot_hash_mismatch" in _blocked_reasons(lineage)


def test_manifest_hash_drift_is_blocked():
    lineage = _clean_lineage(epub_validated=True, manifest_hash="3" * 64)
    assert "manifest_hash_mismatch" in _blocked_reasons(lineage)


def test_bundle_manifest_tampering_is_blocked():
    lineage = _clean_lineage(
        epub_validated=True,
        package_hash=HEX64_E,
        replayed_package_hash="1" * 64,
    )
    assert "package_hash_mismatch" in _blocked_reasons(lineage)
    malformed = _clean_lineage(epub_validated=True, package_hash="not-a-hash")
    assert "package_hash_malformed" in _blocked_reasons(malformed)


def test_missing_bundle_is_blocked():
    lineage = _clean_lineage(epub_validated=True, package_hash=None)
    assert "bundle_evidence_missing" in _blocked_reasons(lineage)
    assert "materialization" in _blocked_kinds(lineage)


def test_missing_download_audit_event_is_blocked():
    lineage = _clean_lineage(epub_validated=True, download_manifest_hash=None)
    assert "download_evidence_missing" in _blocked_reasons(lineage)
    drifted = _clean_lineage(epub_validated=True, download_manifest_hash="5" * 64)
    assert "download_manifest_hash_mismatch" in _blocked_reasons(drifted)


# ---------------------------------------------------------------------------
# Release gate verdict (T-39-04-02: only qualified_candidate / blocked)
# ---------------------------------------------------------------------------


def test_report_green_only_when_all_evidence_satisfied():
    report = _build_report(
        phase22_green=3,
        lineage=_clean_lineage(epub_validated=True),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()


def test_any_lineage_failure_blocks_the_report():
    report = _build_report(
        phase22_green=3,
        lineage=_clean_lineage(epub_validated=True, artifact_status="rejected"),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "blocked"
    assert "lineage_blocked:artifact_binding" in report.blocked_reasons
    # The blocked check stays visible in the report — never deleted/downgraded.
    check = next(
        check
        for check in report.lineage.checks
        if check.kind.value == "artifact_binding"
    )
    assert check.status == DerivativeExportAuditStatus.BLOCKED
    assert "artifact_status_denied" in check.blocked_reasons


def test_unverified_epub_blocks_the_report():
    report = _build_report(
        phase22_green=3,
        lineage=_clean_lineage(epub_validated=False),
        shipment=_verified_shipment(),
    )
    assert report.verdict == "blocked"
    assert "lineage_blocked:epub_validation" in report.blocked_reasons


def test_non_verified_lineage_cannot_be_forced_green():
    report = _build_report(
        phase22_green=3,
        lineage=_clean_lineage(epub_validated=True, artifact_status="rejected"),
        shipment=_verified_shipment(),
    )
    data = report.model_dump(mode="json")
    data["verdict"] = "qualified_candidate"
    data["blocked_reasons"] = []
    with pytest.raises(ValueError):
        DerivativeExportAuditReport.model_validate(data)


def test_blocked_reasons_never_carry_promotion_vocabulary():
    report = _build_report(
        phase22_green=3,
        lineage=_clean_lineage(epub_validated=False, artifact_status="rejected"),
        shipment=build_derivative_export_shipment_baseline([]),
    )
    assert report.verdict == "blocked"
    assert not FORBIDDEN_AUDIT_WORDS.intersection(report.blocked_reasons)
    for reason in report.blocked_reasons:
        assert "promote" not in reason and "production_ready" not in reason
