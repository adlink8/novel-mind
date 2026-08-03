"""Phase 30-04 review/versioning gate and envelope contract tests (REQ-VIS-01).

Covers the 30-VALIDATION.md review matrix at the unit level:
- the fail-closed approval gate: every canon_fact claim needs persisted
  evidence and every reference asset must be rights-cleared before an approval
  can be appended; reason codes are stable and replayable;
- the immutable revision ref and review envelope strict contracts (no extra
  fields, stable lineage, parent linkage);
- approval never silently promotes: the gate vocabulary is closed and only
  ``cleared`` resolves rights.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.visual_bible import VisualBibleVersion
from app.schemas.visual_bible import (
    VisualBibleVersionView,
    VisualReferenceAssetView,
    VisualReviewAction,
    VisualReviewEventView,
    VisualReviewState,
    VisualRightsStatus,
)
from app.services.visual_bible.review import (
    RIGHTS_BLOCKED_FOR_APPROVAL,
    VisualApprovalGateView,
    VisualBibleReviewEnvelope,
    VisualRevisionRef,
    build_revision_ref,
    evaluate_approval_gate,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64


# ---------------------------------------------------------------------------
# Approval gate (pure, replayable, fail closed)
# ---------------------------------------------------------------------------


def test_approval_gate_passes_when_evidence_and_rights_are_clean():
    result = evaluate_approval_gate(
        canon_claim_evidence_counts={"char-ayla-hair": 1, "place-hall": 2},
        asset_rights={"ref-ayla-sketch": VisualRightsStatus.CLEARED},
    )
    assert result.ok is True
    assert result.reason_code is None
    assert result.unresolved_claims == ()
    assert result.unresolved_assets == ()


def test_approval_gate_blocks_canon_claim_without_evidence():
    result = evaluate_approval_gate(
        canon_claim_evidence_counts={"char-ayla-hair": 0},
        asset_rights={},
    )
    assert result.ok is False
    assert result.reason_code == "evidence_unresolved"
    assert result.unresolved_claims == ("char-ayla-hair",)
    assert result.detail  # human-readable detail is always present


def test_approval_gate_blocks_every_rights_status_except_cleared():
    # The only rights status that resolves approval is ``cleared`` (D-30-01).
    assert set(RIGHTS_BLOCKED_FOR_APPROVAL) == {
        VisualRightsStatus.UNREVIEWED,
        VisualRightsStatus.PENDING,
        VisualRightsStatus.DENIED,
    }
    assert VisualRightsStatus.CLEARED not in RIGHTS_BLOCKED_FOR_APPROVAL

    for status in (VisualRightsStatus.UNREVIEWED, VisualRightsStatus.PENDING):
        result = evaluate_approval_gate(
            canon_claim_evidence_counts={},
            asset_rights={"ref-1": status},
        )
        assert result.ok is False
        assert result.reason_code == "rights_unresolved"
        assert result.unresolved_assets == ("ref-1",)

    denied = evaluate_approval_gate(
        canon_claim_evidence_counts={},
        asset_rights={"ref-1": "denied"},
    )
    assert denied.ok is False
    assert denied.reason_code == "rights_unresolved"


def test_approval_gate_clean_when_no_claims_or_assets():
    result = evaluate_approval_gate(canon_claim_evidence_counts={}, asset_rights={})
    assert result.ok is True


def test_approval_gate_reports_all_unresolved_items_together():
    result = evaluate_approval_gate(
        canon_claim_evidence_counts={"cl-a": 0, "cl-b": 0},
        asset_rights={"ref-a": "unreviewed", "ref-b": "pending"},
    )
    # Evidence is checked first and reported before rights.
    assert result.reason_code == "evidence_unresolved"
    assert result.unresolved_claims == ("cl-a", "cl-b")

    rights_only = evaluate_approval_gate(
        canon_claim_evidence_counts={"cl-c": 1},
        asset_rights={"ref-a": "unreviewed", "ref-b": "denied"},
    )
    assert rights_only.reason_code == "rights_unresolved"
    assert rights_only.unresolved_assets == ("ref-a", "ref-b")


def test_approval_gate_accepts_string_rights_statuses():
    # DB rows come back as plain strings; the gate must coerce them.
    result = evaluate_approval_gate(
        canon_claim_evidence_counts={},
        asset_rights={"ref-a": "cleared"},
    )
    assert result.ok is True


# ---------------------------------------------------------------------------
# Immutable revision ref (Phase 31/32 Scene Candidate consumer contract)
# ---------------------------------------------------------------------------


def _version_view(**overrides) -> VisualBibleVersionView:
    payload = {
        "id": 7,
        "owner_id": 11,
        "novel_id": 22,
        "version_key": "vb-main",
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": HEX64,
        "cutoff_chapter": 8,
        "schema_version": "visual-bible.v1",
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "manifest_hash": "c" * 64,
        "review_state": "candidate",
        "style_profile": None,
        "constraints": None,
        "entities": [],
        "reference_assets": [],
        "review_events": [],
    }
    payload.update(overrides)
    return VisualBibleVersionView.model_validate(payload)


def test_revision_ref_is_immutable_identity_and_lineage():
    ref = build_revision_ref(_version_view())
    assert ref.kind == "visual_bible"
    assert ref.version_id == 7
    assert ref.version_key == "vb-main"
    assert ref.revision_number == 1
    assert ref.parent_version_id is None
    assert ref.manifest_hash == "c" * 64
    assert ref.source_snapshot_hash == HEX64
    assert ref.cutoff_chapter == 8

    with pytest.raises(ValidationError):
        VisualRevisionRef.model_validate(ref.model_dump() | {"canon": True})


def test_revision_ref_is_stable_across_rebuilds():
    view = _version_view()
    assert build_revision_ref(view) == build_revision_ref(view)


def test_revision_ref_binds_parent_lineage():
    parent = build_revision_ref(_version_view(id=3, version_key="vb-main-v1"))
    child = build_revision_ref(
        _version_view(id=7, version_key="vb-main-v2", revision_number=2, parent_version_id=3)
    )
    assert child.parent_version_id == parent.version_id
    # The parent ref itself never changes when a child is built.
    assert build_revision_ref(_version_view(id=3, version_key="vb-main-v1")) == parent


def test_revision_ref_builds_from_orm_row():
    """build_revision_ref also accepts the ORM row shape (authority service)."""
    row = VisualBibleVersion(
        id=9,
        owner_id=11,
        novel_id=22,
        version_key="vb-orm",
        revision_number=1,
        parent_version_id=None,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=8,
        schema_version="visual-bible.v1",
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        manifest_hash="d" * 64,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64,
        projection_hash=HEX64,
    )
    ref = build_revision_ref(row)
    assert ref.version_id == 9
    assert ref.version_key == "vb-orm"
    assert ref.manifest_hash == "d" * 64


# ---------------------------------------------------------------------------
# Review envelope strict contract
# ---------------------------------------------------------------------------


def _review_event_view(**overrides):
    payload = {
        "action": "approve",
        "actor_source": "human",
        "actor": "reader",
        "reason": "matches the text",
        "event_key": "ev-approve-1",
        "from_review_state": "candidate",
        "to_review_state": "approved",
    }
    payload.update(overrides)
    return VisualReviewEventView.model_validate(payload)


def _envelope(**overrides):
    payload = {
        "version_id": 7,
        "owner_id": 11,
        "novel_id": 22,
        "version_key": "vb-main",
        "revision_number": 1,
        "parent_version_id": None,
        "review_state": "candidate",
        "revision_ref": build_revision_ref(_version_view()).model_dump(),
        "parent_revision_ref": None,
        "review_events": [_review_event_view().model_dump()],
        "approval_gate": {
            "ok": True,
            "reason_code": None,
            "detail": None,
            "unresolved_claims": [],
            "unresolved_assets": [],
        },
    }
    payload.update(overrides)
    return VisualBibleReviewEnvelope.model_validate(payload)


def test_review_envelope_carries_history_reason_code_and_revision_ref():
    env = _envelope()
    assert env.review_state is VisualReviewState.CANDIDATE
    assert env.revision_ref.version_id == 7
    assert len(env.review_events) == 1
    assert env.review_events[0].action is VisualReviewAction.APPROVE
    assert env.approval_gate is not None
    assert env.approval_gate.ok is True
    assert env.approval_gate.reason_code is None


def test_review_envelope_surfaces_gate_reason_codes():
    env = _envelope(
        approval_gate={
            "ok": False,
            "reason_code": "rights_unresolved",
            "detail": "1 reference asset(s) are not rights-cleared",
            "unresolved_claims": [],
            "unresolved_assets": ["ref-ayla-sketch"],
        }
    )
    assert env.approval_gate is not None
    assert env.approval_gate.ok is False
    assert env.approval_gate.reason_code == "rights_unresolved"
    assert env.approval_gate.unresolved_assets == ["ref-ayla-sketch"]


def test_review_envelope_is_strict_and_requires_revision_ref():
    with pytest.raises(ValidationError):
        VisualBibleReviewEnvelope.model_validate(
            _envelope().model_dump(exclude={"revision_ref"})
        )
    with pytest.raises(ValidationError):
        VisualBibleReviewEnvelope.model_validate(
            _envelope().model_dump() | {"active_pointer": 1}
        )


def test_approval_gate_view_is_strict():
    with pytest.raises(ValidationError):
        VisualApprovalGateView.model_validate(
            {"ok": True, "reason_code": None, "detail": None, "canon": True}
        )


# ---------------------------------------------------------------------------
# No silent canon promotion at the gate layer
# ---------------------------------------------------------------------------


def test_approval_gate_only_gates_approval_not_asset_approval():
    """A passing gate is orthogonal to asset approval flags (D-30-01).

    The gate resolves review readiness; a generated/reference asset still stays
    ``approved=False`` and the gate never flips it. Approval is a version
    review-state projection, not a canon mutation.
    """
    result = evaluate_approval_gate(
        canon_claim_evidence_counts={"char-ayla-hair": 1},
        asset_rights={"ref-1": VisualRightsStatus.CLEARED},
    )
    assert result.ok is True
    assert result.reason_code is None

    # An approved revision envelope may omit the gate (it is no longer
    # 'waiting'), and the underlying version view still carries approved=False.
    env = _envelope(
        review_state="approved",
        approval_gate=None,
    )
    view = _version_view(
        review_state="approved",
        reference_assets=[
            VisualReferenceAssetView.model_validate(
                {
                    "asset_key": "ref-1",
                    "asset_id": "obj-1",
                    "mime_type": "image/png",
                    "bytes_hash": HEX64_B,
                    "rights_status": "cleared",
                    "approved": False,
                }
            )
        ],
    )
    assert env.review_state is VisualReviewState.APPROVED
    assert env.approval_gate is None
    assert view.reference_assets[0].approved is False
