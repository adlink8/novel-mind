"""Phase 39-05 preparation pure-gate unit tests (D-39-01/D-39-02, DB-free).

Covers the deterministic preparation contract owned by 39-05:
  - ``export_preparation_hash`` is byte-reproducible and scope-sensitive;
  - ``export_preparation_payload`` freezes the full branch-aware lineage;
  - ``validate_preparation_payload`` passes on a replayed payload and fails
    closed on stale/forged/mismatched lineage, approval bypass, project/fork
    mismatch and missing evidence — no DB required.
"""

from __future__ import annotations

import pytest

from app.services.derivative_export.manifest import canonical_export_hash
from app.services.derivative_export.preparation import (
    EXPORT_PREPARATION_ARTIFACT_KIND,
    EXPORT_PREPARATION_SCHEMA_VERSION,
    export_preparation_hash,
    export_preparation_payload,
    validate_preparation_payload,
)
from tests.fixtures.derivative_export_roundtrip_fixtures import (
    FORK_ID,
    FORK_KEY,
    NOVEL_ID,
    OWNER_ID,
    PROJECT_ID,
    PROJECT_KEY,
    build_fixture_snapshot,
    seal_fixture_manifest,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

BRANCH = "deriv-branch"
FORK = FORK_KEY
EVIDENCE = ["chapter:1", "chapter:2"]


def _snapshot_manifest():
    snapshot = build_fixture_snapshot()
    manifest = seal_fixture_manifest(snapshot)
    return snapshot, manifest


def _payload(snapshot, manifest, **overrides):
    payload = export_preparation_payload(
        snapshot,
        manifest,
        branch=BRANCH,
        fork=FORK,
        evidence_refs=EVIDENCE,
        generator_lineage={"provider": "mock", "provider_model": "mock-1"},
    )
    payload.update(overrides)
    return payload


# ────────────────────────── preparation hash reproducibility ──────────────────────────


def test_export_preparation_hash_is_deterministic():
    kwargs = dict(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK,
        snapshot_hash="a" * 64,
        manifest_hash="b" * 64,
    )
    first = export_preparation_hash(**kwargs)
    second = export_preparation_hash(**kwargs)
    assert first == second
    assert len(first) == 64
    assert all(ch in "0123456789abcdef" for ch in first)


def test_export_preparation_hash_changes_on_frozen_lineage_drift():
    base = dict(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK,
        snapshot_hash="a" * 64,
        manifest_hash="b" * 64,
    )
    hash_a = export_preparation_hash(**base)
    hash_b = export_preparation_hash(**{**base, "snapshot_hash": "c" * 64})
    hash_c = export_preparation_hash(**{**base, "project_id": PROJECT_ID + 1})
    hash_d = export_preparation_hash(**{**base, "branch": None, "fork": "fork-other"})
    assert len({hash_a, hash_b, hash_c, hash_d}) == 4


def test_export_preparation_hash_is_canonical_export_hash_shape():
    """The hash replays a canonical stable-JSON payload (byte-reproducible)."""
    payload = {
        "schema_version": "derivative-export-preparation.v1",
        "owner_id": OWNER_ID,
        "novel_id": NOVEL_ID,
        "project_id": PROJECT_ID,
        "fork_id": FORK_ID,
        "branch": BRANCH,
        "fork": FORK,
        "snapshot_hash": "a" * 64,
        "manifest_hash": "b" * 64,
    }
    assert export_preparation_hash(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        project_id=PROJECT_ID,
        fork_id=FORK_ID,
        branch=BRANCH,
        fork=FORK,
        snapshot_hash="a" * 64,
        manifest_hash="b" * 64,
    ) == canonical_export_hash(payload)


# ────────────────────────── preparation payload freeze ──────────────────────────


def test_export_preparation_payload_freezes_full_branch_aware_lineage():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest)
    assert payload["schema_version"] == EXPORT_PREPARATION_SCHEMA_VERSION
    assert payload["artifact_kind"] == EXPORT_PREPARATION_ARTIFACT_KIND
    assert payload["authority_space"] == "derivative"
    assert payload["fork"] == FORK
    assert payload["project_id"] == PROJECT_ID
    assert payload["project_key"] == PROJECT_KEY
    assert payload["source_snapshot"]["source_snapshot_hash"] == snapshot.source_snapshot
    assert payload["source_snapshot"]["source_manifest_hash"] == snapshot.project_manifest_hash
    assert payload["base_revision"]["project_manifest_hash"] == snapshot.project_manifest_hash
    assert payload["base_revision"]["scope_hash"] == snapshot.scope_hash
    assert payload["base_revision"]["cutoff_snapshot_hash"] == snapshot.cutoff_snapshot_hash
    assert payload["base_revision"]["text_version_hash"] == snapshot.text_version_hash
    # content_hash claims the frozen manifest/snapshot hash (stale -> fail closed).
    assert payload["content_hash"] == snapshot.snapshot_hash
    assert payload["content_hash"] == manifest.manifest_hash
    assert payload["evidence_refs"] == EVIDENCE
    assert payload["generator_lineage"]["provider"] == "mock"
    assert payload["validator_report"]["verdict"] == "candidate"
    assert payload["review_state"] == "candidate"


# ────────────────────────── validate_preparation_payload ──────────────────────────


def test_validate_preparation_payload_passes_on_replayed_lineage():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest)
    assert (
        validate_preparation_payload(
            payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
        )
        == []
    )


def test_validate_rejects_stale_content_hash():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest, content_hash="9" * 64)
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "content_hash_stale" in errors


def test_validate_rejects_approval_bypass_review_state():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest, review_state="approved")
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "review_state_denied" in errors


def test_validate_rejects_wrong_project_and_fork_scope():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest, project_id=PROJECT_ID + 1, fork="fork-other")
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "project_mismatch" in errors
    assert "fork_mismatch" in errors


def test_validate_rejects_source_snapshot_drift():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest)
    payload["source_snapshot"]["source_snapshot_hash"] = "d" * 64
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "source_snapshot_mismatch" in errors


def test_validate_rejects_base_revision_drift():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest)
    # "e"*64 is the scene-spec fixture hash — never the scope hash, so this is a real drift.
    payload["base_revision"]["scope_hash"] = "e" * 64
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "scope_hash_mismatch" in errors


def test_validate_rejects_missing_evidence_and_malformed_hash():
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest, evidence_refs=[])
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "evidence_missing" in errors

    malformed = _payload(snapshot, manifest, content_hash="not-a-hash")
    errors = validate_preparation_payload(
        malformed, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "content_hash_malformed" in errors


def test_validate_rejects_non_dict_payload():
    snapshot, manifest = _snapshot_manifest()
    errors = validate_preparation_payload(
        "not-a-payload", snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert errors == ["preparation_payload_missing"]


def test_validate_returns_stable_codes_never_mutates():
    """The pure gate never mutates the payload and never fabricates a pass."""
    snapshot, manifest = _snapshot_manifest()
    payload = _payload(snapshot, manifest, review_state="approved", content_hash="9" * 64)
    before = dict(payload)
    errors = validate_preparation_payload(
        payload, snapshot=snapshot, manifest=manifest, project_id=PROJECT_ID, fork=FORK
    )
    assert "review_state_denied" in errors
    assert "content_hash_stale" in errors
    assert payload == before
