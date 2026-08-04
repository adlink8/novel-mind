"""Phase 34-01 illustration anchor contract tests (REQ-VIS-05).

Covers D-34-01..D-34-04 within this plan's scope:
- closed, pinned anchor status vocabulary (proposed / pending_approval / valid /
  needs_repair / invalid) with only valid/needs_repair/invalid on published
  anchors;
- exact source span contract: anchor hash replays from the excerpt, chapter
  content hash replays from the current text, offsets are exact and a mismatch
  is ``invalid`` — never a nearest-match relocation;
- proposal gate: a proposal only accepts a proposal-ready AssetRevision with
  cleared rights and an exact source hash/range/version; the idempotency key
  replays from the span/asset;
- published anchor gate: a valid anchor must bind an approved action, the
  published AssetRevision and a frozen publish manifest hash;
- ORM metadata, append-only content rows (status is the only mutable
  projection) and the migration chain (20260801_illustration_anchors on top of
  20260801_illustration_jobs).
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.user import User
from app.models.agent_runtime import ApprovalRequest
from app.models.illustration_job import IllustrationJob
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import (
    AnchorRange,
    IllustrationAnchor,
    IllustrationAnchorProposal,
)
from app.schemas.illustration import FrozenAssetRevisionView
from app.schemas.illustration_anchor import (
    ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES,
    ILLUSTRATION_ANCHOR_STATUSES,
    AnchorCopy,
    AnchorGateError,
    AnchorPublishManifest,
    AnchorRange as AnchorRangeContract,
    AnchorStatus,
    IllustrationAnchorContract,
    IllustrationAnchorProposalContract,
    PublishedAssetRef,
    anchor_publish_manifest_hash,
    build_anchor_proposal_idempotency_key,
    canonical_anchor_hash,
    source_span_hash,
    validate_anchor_proposal_contract,
    validate_exact_source,
    validate_published_anchor,
)
from app.services.illustration_anchors.validation import (
    AnchorValidationService,
    validate_exact_proposal,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64

SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
SNAPSHOT_HASH = "4" * 64
CONFIG_HASH = "5" * 64
ASSET_BYTES_HASH = "6" * 64

# Deterministic mock chapter text (code-point offsets are stable in Python).
CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
# The anchor span is the second sentence.
_EXCERPT_START = CHAPTER_TEXT.index("The lanterns")
_EXCERPT_END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[_EXCERPT_START:_EXCERPT_END]
CHAPTER_CONTENT_HASH = sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = source_span_hash(EXCERPT)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

ANCHOR_TABLES = {
    "illustration_anchor_proposals",
    "illustration_anchors",
}

# Pinned canonical hashes of the closed vocabularies so a future rename cannot
# pass silently (stable hash pins the closed contract).
STATUSES_HASH = canonical_anchor_hash(
    {"anchor_statuses": list(ILLUSTRATION_ANCHOR_STATUSES)}
)
PUBLISHED_STATUSES_HASH = canonical_anchor_hash(
    {"published_statuses": list(ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES)}
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _frozen_asset(**overrides):
    payload = {
        "id": 1,
        "owner_id": 11,
        "novel_id": 22,
        "job_id": 1,
        "revision_key": "rev-1",
        "revision_number": 1,
        "asset_id": "asset-1",
        "storage_key": f"assets/11/22/{ASSET_BYTES_HASH}.png",
        "mime_type": "image/png",
        "width": 1024,
        "height": 1024,
        "size_bytes": 42,
        "bytes_hash": ASSET_BYTES_HASH,
        "scene_spec_hash": SCENE_SPEC_HASH,
        "prompt_revision_hash": PROMPT_HASH,
        "visual_bible_revision_hash": VB_HASH,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "cutoff_chapter": 8,
        "provider": "mock",
        "provider_model": "mock-img-v1",
        "provider_request_id": "req-1",
        "rights_status": "cleared",
        "approval_state": "proposal_ready",
        "approved_by": "editor",
    }
    payload.update(overrides)
    return FrozenAssetRevisionView.model_validate(payload)


def _copy(**overrides):
    payload = {
        "caption": "The lantern-lit courtyard",
        "alt_text": "Arin crosses a rain-soaked courtyard lit by flickering lanterns",
        "citation": "Chapter 4: The Lantern Courtyard",
    }
    payload.update(overrides)
    return AnchorCopy.model_validate(payload)


def _range(**overrides):
    payload = {
        "source_start": _EXCERPT_START,
        "source_end": _EXCERPT_END,
        "paragraph_start": 2,
        "paragraph_end": 2,
    }
    payload.update(overrides)
    return AnchorRangeContract.model_validate(payload)


def _proposal(**overrides):
    overrides = dict(overrides)
    asset = overrides.pop("proposal_asset", _frozen_asset())
    rng = overrides.pop("range", _range())
    presentation = overrides.pop("presentation", _copy())
    overrides.setdefault("excerpt", EXCERPT)
    overrides.setdefault("anchor_hash", ANCHOR_HASH)
    overrides.setdefault("chapter_content_hash", CHAPTER_CONTENT_HASH)
    payload = {
        "schema_version": "illustration-anchor-proposal.v1",
        "artifact_kind": "illustration_anchor_proposal",
        "owner_id": 11,
        "novel_id": 22,
        "chapter_id": 44,
        "chapter_number": 4,
        "proposal_key": "proposal-lantern-courtyard",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "range": rng,
        "excerpt": overrides.pop("excerpt"),
        "anchor_hash": overrides.pop("anchor_hash"),
        "chapter_content_hash": overrides.pop("chapter_content_hash"),
        "proposal_asset": asset,
        "presentation": presentation,
        "status": "proposed",
        "approval_request_id": None,
        "idempotency_key": "0" * 64,
    }
    payload.update(overrides)
    proposal = IllustrationAnchorProposalContract.model_validate(payload)
    if "idempotency_key" not in overrides:
        proposal = proposal.model_copy(
            update={
                "idempotency_key": build_anchor_proposal_idempotency_key(proposal)
            }
        )
    return proposal


def _manifest(**overrides):
    overrides = dict(overrides)
    rng = overrides.pop("range", _range())
    presentation = overrides.pop("presentation", _copy())
    asset = overrides.pop("asset", None)
    if asset is None:
        asset = PublishedAssetRef.model_validate(
            {
                "asset_revision_id": 1,
                "asset_id": "asset-1",
                "bytes_hash": ASSET_BYTES_HASH,
                "mime_type": "image/png",
            }
        )
    payload = {
        "schema_version": "illustration-anchor-manifest.v1",
        "artifact_kind": "illustration_anchor_manifest",
        "owner_id": 11,
        "novel_id": 22,
        "chapter_id": 44,
        "chapter_number": 4,
        "anchor_key": "anchor-lantern-courtyard",
        "text_version_hash": CHAPTER_CONTENT_HASH,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "range": rng,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "presentation": presentation,
        "asset": asset,
        "published_at": datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc),
    }
    payload.update(overrides)
    return AnchorPublishManifest.model_validate(payload)


def _anchor(**overrides):
    overrides = dict(overrides)
    manifest = overrides.pop("manifest", _manifest())
    rng = overrides.pop("range", _range())
    presentation = overrides.pop("presentation", _copy())
    payload = {
        "schema_version": "illustration-anchor.v1",
        "artifact_kind": "illustration_anchor",
        "owner_id": 11,
        "novel_id": 22,
        "chapter_id": 44,
        "chapter_number": 4,
        "anchor_key": "anchor-lantern-courtyard",
        "proposal_id": 1,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "range": rng,
        "excerpt": EXCERPT,
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "published_asset_revision_id": 1,
        "approval_request_id": 77,
        "publish_manifest_hash": anchor_publish_manifest_hash(manifest),
        "presentation": presentation,
        "status": "valid",
        "approved_by": "editor",
        "approved_at": datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc),
        "idempotency_key": HEX64_B,
    }
    payload.update(overrides)
    return IllustrationAnchorContract.model_validate(payload), manifest


# ---------------------------------------------------------------------------
# Vocabulary (closed and pinned)
# ---------------------------------------------------------------------------


def test_anchor_vocabulary_is_closed_and_pinned():
    assert [s.value for s in AnchorStatus] == list(ILLUSTRATION_ANCHOR_STATUSES)
    assert set(ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES) <= set(
        ILLUSTRATION_ANCHOR_STATUSES
    )
    assert STATUSES_HASH == "4a2faf4f17d71414638695ddc11cc8f7478886250b88d542848cc8c58a15f50e"
    assert PUBLISHED_STATUSES_HASH == "c2b7ea392a01410bfee652b4b86e97105fc6149f5b31ddf4a08170b2094d6b26"


def test_published_anchor_statuses_exclude_candidate_states():
    assert AnchorStatus.PROPOSED not in set(
        AnchorStatus(s) for s in ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES
    )
    assert AnchorStatus.PENDING_APPROVAL not in set(
        AnchorStatus(s) for s in ILLUSTRATION_ANCHOR_PUBLISHED_STATUSES
    )


# ---------------------------------------------------------------------------
# Exact source span value objects (D-34-01)
# ---------------------------------------------------------------------------


def test_anchor_range_requires_exact_span():
    assert _range().source_end > _range().source_start
    with pytest.raises(ValidationError):
        _range(source_start=10, source_end=10)  # empty span
    with pytest.raises(ValidationError):
        _range(source_start=20, source_end=10)  # inverted span
    with pytest.raises(ValidationError):
        _range(paragraph_start=2, paragraph_end=None)  # must be a pair
    with pytest.raises(ValidationError):
        _range(paragraph_start=3, paragraph_end=2)  # inverted paragraph


def test_anchor_copy_requires_accessible_caption_and_alt():
    copy = _copy()
    assert copy.caption and copy.alt_text and copy.citation
    with pytest.raises(ValidationError):
        _copy(caption="")
    with pytest.raises(ValidationError):
        _copy(alt_text="")
    with pytest.raises(ValidationError):
        _copy(citation="")


# ---------------------------------------------------------------------------
# Anchor hash and exact source validation (anchor-valid / anchor-edited)
# ---------------------------------------------------------------------------


def test_anchor_hash_replays_from_excerpt():
    assert source_span_hash(EXCERPT) == ANCHOR_HASH
    assert source_span_hash(EXCERPT + " ") != ANCHOR_HASH


def test_exact_source_valid_replays_to_proposed():
    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=CHAPTER_TEXT,
    )
    assert result.ok is True
    assert result.status is AnchorStatus.PROPOSED


def test_edited_text_before_range_is_invalid_not_relocated():
    # A paragraph was inserted before the anchored sentence; the stored span
    # now points at different text, so the anchor is stale (never relocated).
    edited = "A guard shouted.\n\n" + CHAPTER_TEXT
    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=edited,
    )
    assert result.ok is False
    assert result.status is AnchorStatus.INVALID
    assert result.reason_code == "chapter_content_hash_mismatch"


def test_edited_text_inside_range_is_invalid_not_relocated():
    # Words inside the anchored sentence changed (same length so offsets stay
    # in bounds); the excerpt no longer replays at the stored span and must
    # not move to a nearby paragraph.
    edited = CHAPTER_TEXT.replace("wind", "rain")
    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=sha256(edited.encode("utf-8")).hexdigest(),
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=edited,
    )
    assert result.ok is False
    assert result.status is AnchorStatus.INVALID
    assert result.reason_code == "source_range_mismatch"
    # The excerpt no longer appears in the edited text.
    assert edited.count(EXCERPT) == 0


def test_excerpt_exists_elsewhere_is_not_auto_relocated():
    # A prelude is inserted before the anchored span: the excerpt still exists
    # in the chapter, but at a shifted offset. The stored range no longer
    # replays it, and the validator must never relocate (no nearest match).
    edited = "A guard shouted. " + CHAPTER_TEXT
    assert edited.count(EXCERPT) == 1  # the excerpt exists elsewhere
    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=sha256(edited.encode("utf-8")).hexdigest(),
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=edited,
    )
    assert result.ok is False
    assert result.status is AnchorStatus.INVALID
    assert result.reason_code == "source_range_mismatch"


def test_out_of_bounds_and_malformed_hashes_fail_closed():
    result = validate_exact_source(
        source_range=_range(source_end=len(CHAPTER_TEXT) + 10),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=CHAPTER_TEXT,
    )
    assert result.ok is False
    assert result.reason_code == "source_range_out_of_bounds"

    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash="not-a-hex-hash",
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=CHAPTER_TEXT,
    )
    assert result.ok is False
    assert result.reason_code == "malformed_anchor_hash"

    result = validate_exact_source(
        source_range=_range(),
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        source_snapshot_id="",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_content=CHAPTER_TEXT,
    )
    assert result.ok is False
    assert result.reason_code == "source_snapshot_incomplete"


# ---------------------------------------------------------------------------
# Proposal contract and gate (candidate-only, proposal-ready asset only)
# ---------------------------------------------------------------------------


def test_proposal_contract_is_strict_and_frozen():
    proposal = _proposal()
    assert proposal.status is AnchorStatus.PROPOSED
    assert proposal.anchor_hash == ANCHOR_HASH
    with pytest.raises(ValidationError):
        _proposal(cover_url="http://example.com/cover.jpg")
    with pytest.raises(ValidationError):
        _proposal(dangerous_html="<img>")
    fields = set(IllustrationAnchorProposalContract.model_fields)
    assert "cover_url" not in fields
    assert "dom_index" not in fields
    assert "active_pointer" not in fields


def test_proposal_requires_proposal_ready_asset_with_cleared_rights():
    with pytest.raises(ValidationError):
        _proposal(proposal_asset=_frozen_asset(approval_state="candidate"))
    with pytest.raises(ValidationError):
        _proposal(proposal_asset=_frozen_asset(approval_state="rejected"))
    with pytest.raises(ValidationError):
        _proposal(proposal_asset=_frozen_asset(rights_status="unreviewed"))
    with pytest.raises(ValidationError):
        _proposal(proposal_asset=_frozen_asset(rights_status="denied"))


def test_proposal_scope_must_match_its_asset():
    with pytest.raises(ValidationError):
        _proposal(owner_id=12)
    with pytest.raises(ValidationError):
        _proposal(novel_id=23)


def test_proposal_must_enter_as_proposed():
    # The contract validator raises AnchorGateError; pydantic surfaces it as a
    # ValidationError with the gate message.
    with pytest.raises(ValidationError, match="created as proposed"):
        _proposal(status="valid")
    with pytest.raises(ValidationError, match="created as proposed"):
        _proposal(status="pending_approval")


def test_proposal_idempotency_key_replays_from_span_and_asset():
    a = _proposal()
    b = _proposal()
    assert a.idempotency_key == b.idempotency_key
    assert a.idempotency_key == build_anchor_proposal_idempotency_key(a)
    assert len(a.idempotency_key) == 64
    assert _proposal(proposal_key="other").idempotency_key != a.idempotency_key
    assert _proposal(chapter_id=45).idempotency_key != a.idempotency_key
    assert _proposal(source_snapshot_id="ss-2").idempotency_key != a.idempotency_key
    assert _proposal(excerpt=EXCERPT + "!").idempotency_key != a.idempotency_key
    assert (
        _proposal(proposal_asset=_frozen_asset(id=2)).idempotency_key
        != a.idempotency_key
    )


def test_proposal_gate_accepts_exact_span_only():
    result = validate_anchor_proposal_contract(
        _proposal(), chapter_content=CHAPTER_TEXT
    )
    assert result.ok is True
    assert result.status is AnchorStatus.PROPOSED

    wrong_hash = _proposal(anchor_hash=HEX64_C)
    result = validate_anchor_proposal_contract(
        wrong_hash, chapter_content=CHAPTER_TEXT
    )
    assert result.ok is False
    assert result.reason_code == "anchor_hash_mismatch"

    bad_key = _proposal().model_copy(update={"idempotency_key": "9" * 64})
    result = validate_anchor_proposal_contract(bad_key, chapter_content=CHAPTER_TEXT)
    assert result.ok is False
    assert result.reason_code == "proposal_idempotency_mismatch"

    stale = _proposal(chapter_content_hash=HEX64_C)
    result = validate_anchor_proposal_contract(stale, chapter_content=CHAPTER_TEXT)
    assert result.ok is False
    assert result.reason_code == "chapter_content_hash_mismatch"


# ---------------------------------------------------------------------------
# Service gate (server-side owner/novel scope; proposed or invalid only)
# ---------------------------------------------------------------------------


def test_validate_exact_proposal_checks_asset_state_and_span():
    proposal = _proposal()
    result = validate_exact_proposal(
        proposal,
        chapter_content=CHAPTER_TEXT,
        asset={
            "approval_state": "proposal_ready",
            "rights_status": "cleared",
            "bytes_hash": ASSET_BYTES_HASH,
        },
    )
    assert result.ok is True
    assert result.status is AnchorStatus.PROPOSED

    result = validate_exact_proposal(
        proposal,
        chapter_content=CHAPTER_TEXT,
        asset={
            "approval_state": "candidate",
            "rights_status": "cleared",
            "bytes_hash": ASSET_BYTES_HASH,
        },
    )
    assert result.ok is False
    assert result.reason_code == "asset_not_proposal_ready"

    result = validate_exact_proposal(
        proposal,
        chapter_content=CHAPTER_TEXT,
        asset={
            "approval_state": "proposal_ready",
            "rights_status": "unreviewed",
            "bytes_hash": ASSET_BYTES_HASH,
        },
    )
    assert result.ok is False
    assert result.reason_code == "asset_rights_unresolved"

    result = validate_exact_proposal(
        proposal,
        chapter_content=CHAPTER_TEXT,
        asset={
            "approval_state": "proposal_ready",
            "rights_status": "cleared",
            "bytes_hash": HEX64_D,
        },
    )
    assert result.ok is False
    assert result.reason_code == "asset_hash_drift"


async def test_validation_service_scope_and_persisted_asset(
    db_session: AsyncSession,
):
    service = AnchorValidationService(db_session)
    with pytest.raises(ValueError):
        await service.validate_exact(
            owner_id=0, novel_id=22, proposal=_proposal()
        )
    with pytest.raises(ValueError):
        await service.validate_exact(
            owner_id=11, novel_id=-1, proposal=_proposal()
        )
    with pytest.raises(ValueError):
        await service.validate_exact(
            owner_id=12, novel_id=22, proposal=_proposal()
        )
    # No persisted asset in this scope -> gate error (not silently accepted).
    with pytest.raises(ValueError):
        await service.validate_exact(
            owner_id=11, novel_id=22, proposal=_proposal()
        )


# ---------------------------------------------------------------------------
# Published anchor gate (approved action + published asset + manifest)
# ---------------------------------------------------------------------------


def test_published_anchor_contract_binds_approved_action_asset_manifest():
    anchor, manifest = _anchor()
    assert anchor.status is AnchorStatus.VALID
    assert anchor.publish_manifest_hash == anchor_publish_manifest_hash(manifest)
    validate_published_anchor(anchor, manifest)


def test_published_anchor_must_enter_valid():
    with pytest.raises(ValidationError, match="created valid only"):
        _anchor(status="needs_repair")
    with pytest.raises(ValidationError, match="created valid only"):
        _anchor(status="invalid")


def test_anchor_hash_must_replay_in_published_contract():
    with pytest.raises(ValidationError, match="does not replay from the excerpt"):
        _anchor(anchor_hash=HEX64_C)


def test_publish_manifest_hash_must_replay():
    anchor, manifest = _anchor()
    tampered = anchor.model_copy(update={"publish_manifest_hash": HEX64_C})
    with pytest.raises(AnchorGateError):
        validate_published_anchor(tampered, manifest)


def test_published_asset_must_be_the_manifest_asset():
    anchor, manifest = _anchor()
    wrong_asset = anchor.model_copy(update={"published_asset_revision_id": 99})
    with pytest.raises(AnchorGateError):
        validate_published_anchor(wrong_asset, manifest)


def test_published_anchor_manifest_drift_fails_closed():
    anchor, manifest = _anchor()

    drifted_text = manifest.model_copy(update={"text_version_hash": HEX64_D})
    with pytest.raises(AnchorGateError):
        validate_published_anchor(anchor, drifted_text)

    drifted_hash = manifest.model_copy(update={"anchor_hash": HEX64_D})
    with pytest.raises(AnchorGateError):
        validate_published_anchor(anchor, drifted_hash)

    drifted_range = manifest.model_copy(
        update={
            "range": AnchorRangeContract.model_validate(
                {
                    "source_start": _EXCERPT_START + 1,
                    "source_end": _EXCERPT_END,
                    "paragraph_start": 2,
                    "paragraph_end": 2,
                }
            )
        }
    )
    with pytest.raises(AnchorGateError):
        validate_published_anchor(anchor, drifted_range)

    drifted_snapshot = manifest.model_copy(update={"source_snapshot_id": "ss-9"})
    with pytest.raises(AnchorGateError):
        validate_published_anchor(anchor, drifted_snapshot)


def test_publish_manifest_is_frozen_and_stable():
    m1 = _manifest()
    m2 = _manifest()
    assert anchor_publish_manifest_hash(m1) == anchor_publish_manifest_hash(m2)
    assert len(anchor_publish_manifest_hash(m1)) == 64
    assert anchor_publish_manifest_hash(
        m1.model_copy(update={"anchor_key": "other"})
    ) != anchor_publish_manifest_hash(m1)


# ---------------------------------------------------------------------------
# ORM metadata, append-only rows and migration chain
# ---------------------------------------------------------------------------


def test_anchor_tables_are_registered_on_metadata():
    tables = set(IllustrationAnchorProposal.metadata.tables)
    assert ANCHOR_TABLES <= tables


def test_orm_exports_anchor_entities():
    from app.models import (
        IllustrationAnchor as ExportedAnchor,
        IllustrationAnchorProposal as ExportedProposal,
    )

    assert ExportedProposal.__tablename__ == "illustration_anchor_proposals"
    assert ExportedAnchor.__tablename__ == "illustration_anchors"


def test_proposal_orm_carries_contract_columns_and_constraints():
    cols = set(inspect(IllustrationAnchorProposal).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "chapter_id",
        "chapter_number",
        "proposal_key",
        "source_snapshot_id",
        "source_snapshot_hash",
        "paragraph_start",
        "paragraph_end",
        "source_start",
        "source_end",
        "excerpt",
        "anchor_hash",
        "chapter_content_hash",
        "proposal_asset_revision_id",
        "approval_request_id",
        "published_asset_revision_id",
        "publish_manifest_hash",
        "status",
        "caption",
        "alt_text",
        "citation",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationAnchorProposal.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "proposal_key") in unique
    assert ("idempotency_key",) in unique

    check_names = {
        c.name
        for c in IllustrationAnchorProposal.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_illustration_anchor_proposals_status" in check_names
    assert "ck_illustration_anchor_proposals_publish_shape" in check_names
    assert "ck_illustration_anchor_proposals_offsets" in check_names
    assert "ck_illustration_anchor_proposals_paragraph" in check_names


def test_anchor_orm_carries_published_shape_constraint():
    check_names = {
        c.name
        for c in IllustrationAnchor.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_illustration_anchors_status" in check_names
    assert "ck_illustration_anchors_publish_shape" in check_names
    assert "ck_illustration_anchors_offsets" in check_names
    unique = {
        tuple(c.name for c in u.columns)
        for u in IllustrationAnchor.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "anchor_key") in unique


def test_migration_chain_is_serial_on_top_of_illustration_jobs_head():
    migration = _load_migration("20260801_illustration_anchors.py")
    assert migration.revision == "20260801_illustration_anchors"
    assert migration.down_revision == "20260801_illustration_jobs"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
    assert "illustration_anchor_proposals" in migration.__doc__
    assert "illustration_anchors" in migration.__doc__
    assert "'pending_approval'" in migration._ANCHOR_STATUSES
    assert "'needs_repair'" in migration._ANCHOR_STATUSES


def test_migration_matches_orm_table_set():
    migration = _load_migration("20260801_illustration_anchors.py")
    for table in ANCHOR_TABLES:
        assert table in migration.__doc__


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Persistence and append-only projections
# ---------------------------------------------------------------------------


async def _user_and_novel(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Anchor Novel {username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=4,
        title="The Lantern Courtyard",
        content=CHAPTER_TEXT,
    )
    db_session.add(chapter)
    await db_session.flush()
    return user, novel, chapter


async def _persist_asset(
    db_session: AsyncSession, username: str
) -> tuple[AssetRevision, IllustrationJob, User, Novel]:
    user, novel, chapter = await _user_and_novel(db_session, username)
    job = IllustrationJob(
        owner_id=user.id,
        novel_id=novel.id,
        job_key="job-anchor",
        idempotency_key=HEX64,
        status="succeeded",
        status_reason=None,
        error_code=None,
        lease_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested=False,
        retry_count=0,
        scene_spec_hash=SCENE_SPEC_HASH,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_id=None,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        model_lineage={},
        config_hash=CONFIG_HASH,
        price_snapshot={},
        response_hash=None,
        schema_version="illustration.v1",
    )
    db_session.add(job)
    await db_session.flush()
    asset = AssetRevision(
        owner_id=user.id,
        novel_id=novel.id,
        job_id=job.id,
        revision_key="rev-1",
        revision_number=1,
        asset_id="asset-1",
        storage_key=f"assets/{user.id}/{novel.id}/{ASSET_BYTES_HASH}.png",
        mime_type="image/png",
        width=1024,
        height=1024,
        size_bytes=42,
        bytes_hash=ASSET_BYTES_HASH,
        scene_spec_hash=SCENE_SPEC_HASH,
        prompt_revision_id=101,
        prompt_revision_hash=PROMPT_HASH,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        model_lineage={},
        config_hash=CONFIG_HASH,
        provider="mock",
        provider_model="mock-img-v1",
        provider_request_id="req-1",
        provider_response={},
        provenance={},
        rights_status="cleared",
        approval_state="proposal_ready",
        approved_by="editor",
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="illustration-asset.v1",
    )
    db_session.add(asset)
    await db_session.flush()
    return asset, job, user, novel, chapter


def _proposal_row(
    owner: User,
    novel: Novel,
    asset: AssetRevision,
    chapter: Chapter,
    *,
    status: str = "proposed",
    approval_request_id: int | None = None,
    published_asset_revision_id: int | None = None,
    publish_manifest_hash: str | None = None,
) -> IllustrationAnchorProposal:
    return IllustrationAnchorProposal(
        owner_id=owner.id,
        novel_id=novel.id,
        chapter_id=chapter.id,
        chapter_number=4,
        proposal_key="proposal-lantern-courtyard",
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        paragraph_start=2,
        paragraph_end=2,
        source_start=_EXCERPT_START,
        source_end=_EXCERPT_END,
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        proposal_asset_revision_id=asset.id,
        approval_request_id=approval_request_id,
        published_asset_revision_id=published_asset_revision_id,
        publish_manifest_hash=publish_manifest_hash,
        status=status,
        caption="The lantern-lit courtyard",
        alt_text="Arin crosses a rain-soaked courtyard lit by flickering lanterns",
        citation="Chapter 4: The Lantern Courtyard",
        approved_by=None,
        approved_at=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_C,
        projection_hash=HEX64,
        schema_version="illustration-anchor-proposal.v1",
    )


async def test_proposal_projection_moves_approval_and_publish_only(
    db_session: AsyncSession,
):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_proj")
    row = _proposal_row(owner, novel, asset, chapter)
    db_session.add(row)
    await db_session.flush()
    assert row.status == "proposed"
    assert row.published_asset_revision_id is None

    # Approval request attaches: proposed -> pending_approval (projection).
    approval = ApprovalRequest(
        owner_id=owner.id,
        action="illustration_anchor_publish",
        payload_summary={},
        payload_hash=None,
        status="pending",
    )
    db_session.add(approval)
    await db_session.flush()
    row.approval_request_id = approval.id
    row.status = "pending_approval"
    await db_session.flush()
    assert row.status == "pending_approval"

    # Deterministic publish projection: pending_approval -> valid with the
    # published asset + publish manifest (34-05 owns this in production).
    row.published_asset_revision_id = asset.id
    row.publish_manifest_hash = HEX64
    row.approved_by = "editor"
    row.status = "valid"
    await db_session.flush()
    assert row.status == "valid"

    # Immutable content still fails closed after the projection moved.
    row.source_start = 0
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_proposal_row_is_append_only(db_session: AsyncSession):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_append")
    row = _proposal_row(owner, novel, asset, chapter)
    db_session.add(row)
    await db_session.flush()
    row.excerpt = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_valid_anchor_row_requires_published_binding(
    db_session: AsyncSession,
):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_shape")
    approval = ApprovalRequest(
        owner_id=owner.id,
        action="illustration_anchor_publish",
        payload_summary={},
        payload_hash=None,
        status="approved",
    )
    db_session.add(approval)
    await db_session.flush()
    proposal = _proposal_row(
        owner,
        novel,
        asset,
        chapter,
        status="valid",
        approval_request_id=approval.id,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=HEX64,
    )
    proposal.approved_by = "editor"
    db_session.add(proposal)
    await db_session.flush()

    # A published anchor created from the approved proposal row is valid.
    anchor = IllustrationAnchor(
        owner_id=owner.id,
        novel_id=novel.id,
        chapter_id=chapter.id,
        chapter_number=4,
        anchor_key="anchor-lantern-courtyard",
        proposal_id=proposal.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        paragraph_start=2,
        paragraph_end=2,
        source_start=_EXCERPT_START,
        source_end=_EXCERPT_END,
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=HEX64,
        approval_request_id=approval.id,
        status="valid",
        caption="The lantern-lit courtyard",
        alt_text="Arin crosses a rain-soaked courtyard lit by flickering lanterns",
        citation="Chapter 4: The Lantern Courtyard",
        approved_by="editor",
        approved_at=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_C,
        projection_hash=HEX64,
        schema_version="illustration-anchor.v1",
    )
    db_session.add(anchor)
    await db_session.flush()
    assert anchor.status == "valid"

    # A published anchor whose content would move fails closed.
    anchor.excerpt = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_anchor_status_is_the_only_mutable_projection(
    db_session: AsyncSession,
):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_status")
    approval = ApprovalRequest(
        owner_id=owner.id,
        action="illustration_anchor_publish",
        payload_summary={},
        payload_hash=None,
        status="approved",
    )
    db_session.add(approval)
    await db_session.flush()
    proposal = _proposal_row(
        owner,
        novel,
        asset,
        chapter,
        status="valid",
        approval_request_id=approval.id,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=HEX64,
    )
    proposal.approved_by = "editor"
    db_session.add(proposal)
    await db_session.flush()
    anchor = IllustrationAnchor(
        owner_id=owner.id,
        novel_id=novel.id,
        chapter_id=chapter.id,
        chapter_number=4,
        anchor_key="anchor-status",
        proposal_id=proposal.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        paragraph_start=None,
        paragraph_end=None,
        source_start=_EXCERPT_START,
        source_end=_EXCERPT_END,
        excerpt=EXCERPT,
        anchor_hash=ANCHOR_HASH,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=HEX64,
        approval_request_id=approval.id,
        status="valid",
        caption="The lantern-lit courtyard",
        alt_text="Arin crosses a rain-soaked courtyard lit by flickering lanterns",
        citation="Chapter 4: The Lantern Courtyard",
        approved_by="editor",
        approved_at=None,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="illustration-anchor.v1",
    )
    db_session.add(anchor)
    await db_session.flush()
    # Stale source drift is surfaced explicitly via the status projection.
    anchor.status = "needs_repair"
    await db_session.flush()
    assert anchor.status == "needs_repair"
    # Immutable content still fails closed.
    anchor.published_asset_revision_id = 999
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_anchor_delete_fails_closed(db_session: AsyncSession):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_del")
    row = _proposal_row(owner, novel, asset, chapter)
    db_session.add(row)
    await db_session.flush()
    await db_session.delete(row)
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_publish_shape_check_rejects_unbound_valid(db_session: AsyncSession):
    asset, _, owner, novel, chapter = await _persist_asset(db_session, "anchor_unbound")
    # Proposing a valid status without the published binding violates the
    # fail-closed publish shape (D-34-01) at the DB level.
    row = _proposal_row(owner, novel, asset, chapter, status="valid")
    db_session.add(row)
    with pytest.raises(IntegrityError):
        await db_session.flush()
