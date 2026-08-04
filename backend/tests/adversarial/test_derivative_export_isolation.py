"""Phase 39-01 derivative export adversarial isolation tests (D-39-02).

Pure, database-free red-team coverage over the fail-closed export gates:

- no Original / future scope, no cross-owner / cross-project / cross-fork data
  can reach the export (owner/project/fork parity);
- a rejected or unapproved asset is never exportable and a malicious asset id
  (path traversal) is blocked;
- a stale revision (version drift) and a stale citation hash are blocked;
- every revision asset hash must be a member of the published asset set;
- any namespace / mime / hash malformation fails closed.

Every check asserts the explicit blocked reason code — never a silent pass.
"""

from __future__ import annotations

import hashlib

import pytest

from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_NAMESPACE,
    DerivativeVisualAssetState,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshotError,
    validate_asset_membership,
    validate_published_asset,
    validate_published_revision,
    validate_revision_citation_hash,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    FORK_ID,
    HEX64_A,
    HEX64_B,
    HEX64_F,
    NOVEL_ID,
    OWNER_ID,
    PROJECT_ID,
    fixture_asset,
    fixture_revision,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

OTHER_OWNER = OWNER_ID + 999
OTHER_PROJECT = PROJECT_ID + 999
OTHER_FORK = FORK_ID + 999


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
    revision = revision.__class__(
        **{**revision.as_dict(), "owner_id": OTHER_OWNER}
    )
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
    assert "revision_fork_mismatch" in validate_published_revision(
        revision, **_scope()
    )


def test_original_or_promoted_status_is_blocked():
    revision = fixture_revision(version_id=1)
    revision = revision.__class__(**{**revision.as_dict(), "status": "original"})
    assert "revision_status_denied" in validate_published_revision(
        revision, **_scope()
    )


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
    errors = validate_published_revision(
        revision, **_scope(chapter_version_id=2)
    )
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
    assert "asset_hash_not_member" in validate_asset_membership(
        revision, {HEX64_F}
    )


def test_unapproved_asset_hash_referenced_is_blocked():
    # A rejected asset's hash must never satisfy membership.
    revision = fixture_revision(version_id=1, asset_hashes=["5" * 64])
    assert "asset_hash_not_member" in validate_asset_membership(
        revision, {HEX64_F}
    )


# ---------------------------------------------------------------------------
# Snapshot service scope gate (pure helper, no DB)
# ---------------------------------------------------------------------------


def test_snapshot_scope_requires_positive_ids():
    from app.services.derivative_export.snapshot import _require_scope

    for owner, novel, project in [(0, 1, 1), (1, 0, 1), (1, 1, 0), (-1, 1, 1)]:
        with pytest.raises(ExportSnapshotError) as exc:
            _require_scope(owner_id=owner, novel_id=novel, project_id=project)
        assert exc.value.code == "invalid_scope"
