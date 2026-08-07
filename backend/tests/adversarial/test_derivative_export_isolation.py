"""Phase 39-01 derivative export adversarial isolation tests (D-39-02).

Pure, database-free red-team coverage over the fail-closed export gates:

- no Original / future scope, no cross-owner / cross-project / cross-fork data
  can reach the export (owner/project/fork parity);
- a rejected or unapproved asset is never exportable and a malicious asset id
  (path traversal) is blocked;
- a stale revision (version drift) and a stale citation hash are blocked;
- every revision asset hash must be a member of the published asset set;
- any namespace / mime / hash malformation fails closed.
- Phase 39-02 (D-39-03): the provenance **package** re-validates owner and the
  derivative namespace, bounds entries/bytes, blocks zip-slip / IDOR /
  Original-space / future-stale citations / rejected or missing assets and the
  three-dimension **audit** cannot be forced green while Phase 22 is blocked.

Every check asserts the explicit blocked reason code — never a silent pass.
"""

from __future__ import annotations

import base64
import hashlib
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from app.schemas.derivative_visual_asset import (
    DerivativeVisualAssetState,
)
from app.services.derivative_export import package as package_module
from app.services.derivative_export.audit import (
    DerivativeExportAuditReport,
    DerivativeExportPhase22Evidence,
    build_derivative_export_audit,
    replay_quality_qualification_blocked_reason,
)
from app.services.derivative_export.manifest import (
    DerivativeExportCitation,
    MissingDerivativeAssetRecord,
)
from app.services.derivative_export.package import (
    build_derivative_export_package,
    derivative_export_package_hash,
    validate_package_inputs,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    seal_export_snapshot,
    validate_asset_membership,
    validate_published_asset,
    validate_published_revision,
    validate_revision_citation_hash,
)
from app.services.derivative_generation.published_revision import (
    canonical_citation_hash,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    FORK_ID,
    HEX64_A,
    HEX64_B,
    HEX64_F,
    OWNER_ID,
    PROJECT_ID,
    build_fixture_snapshot,
    fixture_asset,
    fixture_export_asset,
    fixture_export_revision,
    fixture_revision,
    seal_fixture_manifest,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

OTHER_OWNER = OWNER_ID + 999
OTHER_PROJECT = PROJECT_ID + 999
OTHER_FORK = FORK_ID + 999

TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
TINY_PNG_HASH = hashlib.sha256(TINY_PNG).hexdigest()


class _TinyReader:
    """Serves the tiny PNG only when the content hash replays it."""

    def __call__(self, asset):
        if asset.content_hash != TINY_PNG_HASH:
            return None
        return TINY_PNG


def _readable_snapshot():
    asset = fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    )
    return build_fixture_snapshot(assets=(asset,))


def _phase22(green_observed: int = 0) -> DerivativeExportPhase22Evidence:
    return DerivativeExportPhase22Evidence(
        green_observed=green_observed,
        source=".planning/STATE.md",
        source_hash="a" * 64,
    )


def _scope(**overrides):
    scope = dict(
        owner_id=OWNER_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        source_snapshot=HEX64_A,
        project_manifest_hash=HEX64_B,
        chapter_version_id=1,
    )
    scope.update(overrides)
    return scope


def _asset_scope(**overrides):
    scope = dict(
        owner_id=OWNER_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        source_snapshot_hash=HEX64_A,
    )
    scope.update(overrides)
    return scope


# ---------------------------------------------------------------------------
# Revision parity (owner/project/fork/status/snapshot/version/citation)
# ---------------------------------------------------------------------------


def test_clean_revision_passes_parity():
    revision = fixture_revision(version_id=1)
    assert validate_published_revision(revision, **_scope()) == []


def test_cross_owner_revision_is_blocked():
    revision = fixture_revision(version_id=1)
    revision = revision.__class__(**{**revision.as_dict(), "owner_id": OTHER_OWNER})
    assert "revision_owner_mismatch" in validate_published_revision(
        revision, **_scope()
    )


def test_cross_project_revision_is_blocked():
    revision = fixture_revision(version_id=1)
    revision = revision.__class__(**{**revision.as_dict(), "project_id": OTHER_PROJECT})
    assert "revision_project_mismatch" in validate_published_revision(
        revision, **_scope()
    )


def test_cross_fork_revision_is_blocked():
    revision = fixture_revision(version_id=1)
    revision = revision.__class__(**{**revision.as_dict(), "fork_id": OTHER_FORK})
    assert "revision_fork_mismatch" in validate_published_revision(revision, **_scope())


def test_original_or_promoted_status_is_blocked():
    revision = fixture_revision(version_id=1)
    revision = revision.__class__(**{**revision.as_dict(), "status": "original"})
    assert "revision_status_denied" in validate_published_revision(revision, **_scope())


def test_source_snapshot_drift_is_blocked():
    revision = fixture_revision(version_id=1, source_snapshot="2" * 64)
    assert "revision_source_snapshot_mismatch" in validate_published_revision(
        revision, **_scope()
    )


def test_manifest_hash_drift_is_blocked():
    revision = fixture_revision(version_id=1, manifest_hash="3" * 64)
    assert "revision_manifest_hash_mismatch" in validate_published_revision(
        revision, **_scope()
    )


def test_stale_revision_version_is_blocked():
    # The chapter version token has moved ahead of the published revision.
    revision = fixture_revision(version_id=1)
    errors = validate_published_revision(revision, **_scope(chapter_version_id=2))
    assert "revision_version_stale" in errors


def test_stale_citation_hash_is_blocked():
    revision = fixture_revision(version_id=1)
    review = dict(revision.review)
    review["evidence_snapshot"] = dict(review["evidence_snapshot"])
    # The citation keys freeze but the hash no longer replays them.
    revision = revision.__class__(
        **{**revision.as_dict(), "citation_hash": "9" * 64, "review": review}
    )
    assert "revision_citation_hash_mismatch" in validate_revision_citation_hash(
        revision
    )
    assert "revision_citation_hash_mismatch" in validate_published_revision(
        revision, **_scope()
    )


# ---------------------------------------------------------------------------
# Asset parity (owner/project/fork/namespace/snapshot/hash/mime/path)
# ---------------------------------------------------------------------------


def test_clean_asset_passes_parity():
    asset = fixture_asset()
    assert validate_published_asset(asset, **_asset_scope()) == []


def test_cross_owner_asset_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(update={"owner_id": OTHER_OWNER})
    assert "asset_owner_mismatch" in validate_published_asset(asset, **_asset_scope())


def test_cross_project_asset_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(update={"project_id": OTHER_PROJECT})
    assert "asset_project_mismatch" in validate_published_asset(asset, **_asset_scope())


def test_cross_fork_asset_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(update={"fork_id": OTHER_FORK})
    assert "asset_fork_mismatch" in validate_published_asset(asset, **_asset_scope())


def test_original_namespace_asset_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(update={"namespace": "original_canon"})
    assert "asset_namespace_denied" in validate_published_asset(asset, **_asset_scope())


def test_rejected_or_unapproved_asset_is_blocked():
    for state in ("rejected", "candidate", "superseded", "blocked"):
        asset = fixture_asset(review_state=state)
        asset = asset.model_copy(
            update={
                "approval": DerivativeVisualAssetState(state),
            }
        )
        assert "asset_not_approved" in validate_published_asset(asset, **_asset_scope())


def test_future_source_snapshot_drift_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(
        update={
            "source_snapshot": asset.source_snapshot.model_copy(
                update={"source_snapshot_hash": "8" * 64}
            )
        }
    )
    assert "asset_source_snapshot_mismatch" in validate_published_asset(
        asset, **_asset_scope()
    )


def test_malformed_content_hash_is_blocked():
    asset = fixture_asset(content_hash="not-a-hash")
    assert "asset_hash_malformed" in validate_published_asset(asset, **_asset_scope())


def test_unallowed_mime_type_is_blocked():
    asset = fixture_asset()
    asset = asset.model_copy(update={"mime_type": "application/x-msdownload"})
    assert "asset_mime_denied" in validate_published_asset(asset, **_asset_scope())


@pytest.mark.parametrize(
    "malicious",
    [
        "../escape",
        "..%2fescape",
        "a/b",
        "a\\b",
        "a..b",
        "a\x00b",
    ],
)
def test_path_traversal_asset_id_is_blocked(malicious):
    # T-39-01-02 / zip-slip: a malicious asset id must never reach the archive.
    asset = fixture_asset(asset_id=malicious)
    assert "asset_path_denied" in validate_published_asset(asset, **_asset_scope())


# ---------------------------------------------------------------------------
# Asset hash membership (a revision can only reference published assets)
# ---------------------------------------------------------------------------


def test_asset_hashes_membership_passes_for_published_hashes():
    revision = fixture_revision(version_id=1, asset_hashes=[HEX64_F])
    assert validate_asset_membership(revision, {HEX64_F}) == []


def test_asset_hash_not_member_is_blocked():
    revision = fixture_revision(version_id=1, asset_hashes=["7" * 64])
    assert "asset_hash_not_member" in validate_asset_membership(revision, {HEX64_F})


def test_unapproved_asset_hash_referenced_is_blocked():
    # A rejected asset's hash must never satisfy membership.
    revision = fixture_revision(version_id=1, asset_hashes=["5" * 64])
    assert "asset_hash_not_member" in validate_asset_membership(revision, {HEX64_F})


# ---------------------------------------------------------------------------
# Snapshot service scope gate (pure helper, no DB)
# ---------------------------------------------------------------------------


def test_snapshot_scope_requires_positive_ids():
    from app.services.derivative_export.snapshot import _require_scope

    for owner, novel, project in [(0, 1, 1), (1, 0, 1), (1, 1, 0), (-1, 1, 1)]:
        with pytest.raises(ExportSnapshotError) as exc:
            _require_scope(owner_id=owner, novel_id=novel, project_id=project)
        assert exc.value.code == "invalid_scope"


# ---------------------------------------------------------------------------
# Phase 39-02: provenance package (D-39-03) fail-closed gates
# ---------------------------------------------------------------------------


def test_clean_snapshot_builds_package_with_replayable_hash():
    snapshot = _readable_snapshot()
    payload, pkg = build_derivative_export_package(snapshot, _TinyReader())
    with ZipFile(BytesIO(payload)) as archive:
        assert "package-manifest.json" in archive.namelist()
        index = json.loads(archive.read("package-manifest.json"))
    assert derivative_export_package_hash(index) == index["package_hash"]
    assert pkg.package_hash == index["package_hash"]
    assert index["snapshot_hash"] == snapshot.snapshot_hash


def test_package_cross_owner_revision_is_blocked():
    revision = fixture_export_revision().model_copy(update={"owner_id": OTHER_OWNER})
    snapshot = build_fixture_snapshot(revisions=(revision,), assets=(), citations=())
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "revision_owner_mismatch"


def test_package_cross_owner_manifest_is_blocked():
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot).model_copy(
        update={"owner_id": OTHER_OWNER}
    )
    errors = validate_package_inputs(snapshot, manifest)
    assert "manifest_owner_mismatch" in errors


def test_package_original_space_is_blocked():
    snapshot = build_fixture_snapshot()
    snapshot = seal_export_snapshot(
        snapshot.model_copy(update={"space": "original_canon"})
    )
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "namespace_denied"


def test_package_original_namespace_asset_is_blocked():
    asset = fixture_export_asset(
        fixture_asset(content_hash=TINY_PNG_HASH, size_bytes=len(TINY_PNG))
    ).model_copy(update={"namespace": "original_canon"})
    snapshot = build_fixture_snapshot(assets=(asset,))
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "asset_namespace_denied"


def test_package_future_citation_chapter_is_blocked():
    citations = (
        DerivativeExportCitation(
            citation_key="fork:ff-fixture:chapter:99",
            citation_hash=canonical_citation_hash(["fork:ff-fixture:chapter:99"]),
            source_snapshot=HEX64_A,
            revision_id=501,
            chapter_number=99,
        ),
    )
    snapshot = build_fixture_snapshot(
        revisions=(fixture_export_revision(),), assets=(), citations=citations
    )
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "citation_chapter_unknown"


def test_package_future_citation_revision_is_blocked():
    citations = (
        DerivativeExportCitation(
            citation_key="fork:ff-fixture:chapter:1",
            citation_hash=canonical_citation_hash(["fork:ff-fixture:chapter:1"]),
            source_snapshot=HEX64_A,
            revision_id=999999,
            chapter_number=1,
        ),
    )
    snapshot = build_fixture_snapshot(
        revisions=(fixture_export_revision(),), assets=(), citations=citations
    )
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "citation_revision_unknown"


def test_package_stale_citation_hash_is_blocked():
    citations = (
        DerivativeExportCitation(
            citation_key="fork:ff-fixture:chapter:1",
            citation_hash="9" * 64,
            source_snapshot=HEX64_A,
            revision_id=501,
            chapter_number=1,
        ),
    )
    snapshot = build_fixture_snapshot(
        revisions=(fixture_export_revision(),), assets=(), citations=citations
    )
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "citation_hash_mismatch"


def test_package_citation_source_snapshot_drift_is_blocked():
    citations = (
        DerivativeExportCitation(
            citation_key="fork:ff-fixture:chapter:1",
            citation_hash=canonical_citation_hash(["fork:ff-fixture:chapter:1"]),
            source_snapshot="2" * 64,
            revision_id=501,
            chapter_number=1,
        ),
    )
    snapshot = build_fixture_snapshot(
        revisions=(fixture_export_revision(),), assets=(), citations=citations
    )
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "citation_source_snapshot_mismatch"


def test_package_rejected_asset_is_blocked():
    asset = fixture_export_asset(fixture_asset(review_state="rejected"))
    snapshot = build_fixture_snapshot(assets=(asset,))
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "asset_not_approved"


def test_package_missing_asset_record_is_blocked():
    record = MissingDerivativeAssetRecord(
        asset_id="dv-missing",
        content_hash=HEX64_F,
        mime_type="image/png",
        chapter_number=1,
        reason_code="asset_bytes_missing",
        detail="bytes missing in scope",
    )
    snapshot = build_fixture_snapshot(assets=(), missing_assets=(record,))
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "missing_asset_blocks_package"


def test_package_missing_bytes_are_blocked():
    # The snapshot lists the asset but the reader cannot replay its bytes.
    snapshot = _readable_snapshot()
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, lambda asset: None)
    assert exc.value.code == "asset_bytes_missing"


@pytest.mark.parametrize(
    "malicious",
    [
        "../escape",
        "..%2fescape",
        "a/b",
        "a\\b",
        "a..b",
        "a\x00b",
    ],
)
def test_package_path_traversal_asset_id_is_blocked(malicious):
    # T-39-02-02 / zip-slip: a malicious asset id must never reach the archive.
    asset = fixture_export_asset(fixture_asset(asset_id=malicious))
    snapshot = build_fixture_snapshot(assets=(asset,))
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(snapshot, _TinyReader())
    assert exc.value.code == "asset_path_denied"


def test_package_total_size_bound_fails_closed(monkeypatch):
    monkeypatch.setattr(package_module, "MAX_PACKAGE_TOTAL_BYTES", 10)
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(_readable_snapshot(), _TinyReader())
    assert exc.value.code == "package_too_large"


def test_package_entry_count_bound_fails_closed(monkeypatch):
    monkeypatch.setattr(package_module, "MAX_PACKAGE_ENTRIES", 2)
    with pytest.raises(ExportSnapshotError) as exc:
        build_derivative_export_package(_readable_snapshot(), _TinyReader())
    assert exc.value.code == "package_too_many_entries"


# ---------------------------------------------------------------------------
# Phase 39-02: three-dimension audit contract (D-39-03, D-39-04)
# ---------------------------------------------------------------------------


def test_falsely_green_audit_is_blocked():
    """Phase 22 0/3 -> quality blocked + verdict blocked; green is impossible."""
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(manifest=manifest, phase22=_phase22(0))
    assert report.dimensions[2].status.value == "blocked"
    assert report.verdict == "blocked"
    assert report.dimensions[2].blocked_reasons
    # A green claim cannot be constructed while the evidence says blocked.
    data = report.model_dump(mode="json")
    data["dimensions"][2]["status"] = "verified"
    data["dimensions"][2]["blocked_reasons"] = []
    data["verdict"] = "qualified_candidate"
    data["blocked_reasons"] = []
    with pytest.raises(ValueError):
        DerivativeExportAuditReport.model_validate(data)


def test_audit_blocked_reason_replays_from_manifest():
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    report = build_derivative_export_audit(manifest=manifest, phase22=_phase22(0))
    reason = report.dimensions[2].blocked_reasons[0]
    assert reason == replay_quality_qualification_blocked_reason(
        snapshot_hash=manifest.manifest_hash, green_observed=0
    )
    # The blocked reason is bound to the manifest: a different manifest hash
    # can never replay the same reason.
    assert reason != replay_quality_qualification_blocked_reason(
        snapshot_hash="2" * 64, green_observed=0
    )
