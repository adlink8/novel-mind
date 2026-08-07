"""Phase 34-03 anchor repair unit tests (REQ-VIS-05, D-34-03).

Covers the explicit repair candidate flow within this plan's scope:
- ``classify_anchor_repair`` classifies text/version changes as ``valid`` /
  ``needs_repair`` / ``invalid`` with the frozen evidence diff; inserted/deleted
  ranges, version switches and wrong hashes are classified correctly and a stale
  anchor is never relocated to a nearby paragraph (D-34-01/03);
- ``repair_proposal_key`` is deterministic and span/asset-scoped so re-proposing
  the same repair replays (append-only) and a different span creates a distinct
  candidate;
- the repair candidate gate accepts only an exact new span against the current
  chapter (candidate-only, no auto-relocation);
- ``AnchorRepairService.revalidate`` persists the status projection (the single
  mutable column) so a stale anchor is presented explicitly.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import (
    IllustrationAnchor,
    IllustrationAnchorProposal,
)
from app.models.illustration_job import IllustrationJob
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.illustration import FrozenAssetRevisionView
from app.schemas.illustration_anchor import (
    AnchorCopy,
    AnchorRange as AnchorRangeContract,
    AnchorStatus,
    IllustrationAnchorProposalContract,
    build_anchor_proposal_idempotency_key,
    source_span_hash,
    validate_anchor_proposal_contract,
)
from app.services.illustration_anchors.repair import (
    AnchorRepairService,
    classify_anchor_repair,
    repair_proposal_key,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
SNAPSHOT_HASH = "4" * 64
SCENE_SPEC_HASH = "1" * 64
PROMPT_HASH = "2" * 64
VB_HASH = "3" * 64
CONFIG_HASH = "5" * 64
ASSET_BYTES_HASH = "6" * 64

# Deterministic mock chapter text (code-point offsets are stable in Python).
CHAPTER_TEXT = (
    "Arin crossed the rain-soaked courtyard. The lanterns flickered in the "
    "wind, casting long shadows over the cobblestones."
)
_EXCERPT_START = CHAPTER_TEXT.index("The lanterns")
_EXCERPT_END = len(CHAPTER_TEXT)
EXCERPT = CHAPTER_TEXT[_EXCERPT_START:_EXCERPT_END]
CHAPTER_CONTENT_HASH = sha256(CHAPTER_TEXT.encode("utf-8")).hexdigest()
ANCHOR_HASH = source_span_hash(EXCERPT)

# Edited chapter text: a paragraph is inserted before the anchored span, so the
# frozen span no longer replays the excerpt and the content version changes.
EDITED_TEXT = "A guard shouted.\n\n" + CHAPTER_TEXT
EDITED_CONTENT_HASH = sha256(EDITED_TEXT.encode("utf-8")).hexdigest()
EDITED_EXCERPT_START = EDITED_TEXT.index(EXCERPT)
EDITED_EXCERPT_END = len(EDITED_TEXT)


def _classify(**overrides):
    payload = {
        "anchor_hash": ANCHOR_HASH,
        "chapter_content_hash": CHAPTER_CONTENT_HASH,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "excerpt": EXCERPT,
        "source_start": _EXCERPT_START,
        "source_end": _EXCERPT_END,
        "paragraph_start": 2,
        "paragraph_end": 2,
        "current_content": CHAPTER_TEXT,
    }
    payload.update(overrides)
    return classify_anchor_repair(**payload)


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


def _repair_proposal(*, content: str, source_start: int, source_end: int, **overrides):
    """Frozen repair candidate contract for an exact new span (D-34-01)."""
    overrides = dict(overrides)
    asset = overrides.pop("proposal_asset", _frozen_asset())
    overrides.setdefault("excerpt", EXCERPT)
    overrides.setdefault("anchor_hash", source_span_hash(overrides["excerpt"]))
    overrides.setdefault(
        "chapter_content_hash", sha256(content.encode("utf-8")).hexdigest()
    )
    payload = {
        "schema_version": "illustration-anchor-proposal.v1",
        "artifact_kind": "illustration_anchor_proposal",
        "owner_id": 11,
        "novel_id": 22,
        "chapter_id": 44,
        "chapter_number": 4,
        "proposal_key": f"repair:1:{source_start}:{source_end}",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "range": AnchorRangeContract(
            source_start=source_start,
            source_end=source_end,
            paragraph_start=2,
            paragraph_end=2,
        ),
        "excerpt": overrides.pop("excerpt"),
        "anchor_hash": overrides.pop("anchor_hash"),
        "chapter_content_hash": overrides.pop("chapter_content_hash"),
        "proposal_asset": asset,
        "presentation": AnchorCopy(
            caption="The lantern-lit courtyard",
            alt_text="Arin crosses a rain-soaked courtyard lit by flickering lanterns",
            citation="Chapter 4: The Lantern Courtyard",
        ),
        "status": "proposed",
        "approval_request_id": None,
        "idempotency_key": "0" * 64,
    }
    payload.update(overrides)
    proposal = IllustrationAnchorProposalContract.model_validate(payload)
    if "idempotency_key" not in overrides:
        proposal = proposal.model_copy(
            update={"idempotency_key": build_anchor_proposal_idempotency_key(proposal)}
        )
    return proposal


# ---------------------------------------------------------------------------
# Pure classification (D-34-03)
# ---------------------------------------------------------------------------


def test_classification_is_frozen_and_holds_evidence():
    c = _classify()
    assert c.status is AnchorStatus.VALID
    assert c.reason_code is None
    assert c.previous_content_hash == CHAPTER_CONTENT_HASH
    assert c.current_content_hash == CHAPTER_CONTENT_HASH
    assert c.source_start == _EXCERPT_START
    assert c.source_end == _EXCERPT_END
    with pytest.raises(FrozenInstanceError):
        c.status = AnchorStatus.NEEDS_REPAIR  # frozen dataclass


def test_unchanged_chapter_is_valid():
    c = _classify()
    assert c.status is AnchorStatus.VALID
    assert c.detail is None


def test_inserted_text_before_range_is_needs_repair():
    c = _classify(current_content=EDITED_TEXT)
    assert c.status is AnchorStatus.NEEDS_REPAIR
    assert c.reason_code == "text_version_drift"
    assert c.previous_content_hash == CHAPTER_CONTENT_HASH
    assert c.current_content_hash == EDITED_CONTENT_HASH
    assert c.current_content_hash != c.previous_content_hash


def test_deleted_text_inside_range_is_needs_repair():
    deleted = CHAPTER_TEXT.replace("wind", "rain")
    c = _classify(current_content=deleted)
    assert c.status is AnchorStatus.NEEDS_REPAIR
    assert c.reason_code == "text_version_drift"
    assert c.current_content_hash == sha256(deleted.encode("utf-8")).hexdigest()


def test_version_switch_by_snapshot_is_needs_repair():
    c = _classify(current_content=CHAPTER_TEXT, current_snapshot_id="ss-2")
    assert c.status is AnchorStatus.NEEDS_REPAIR
    assert c.reason_code == "source_snapshot_drift"
    assert c.previous_snapshot_id == "ss-1"
    assert c.current_snapshot_id == "ss-2"

    c = _classify(current_content=CHAPTER_TEXT, current_snapshot_hash=HEX64_B)
    assert c.status is AnchorStatus.NEEDS_REPAIR
    assert c.reason_code == "source_snapshot_drift"


def test_content_hash_change_alone_is_needs_repair():
    # A caller that only has the current hash (no content) still detects drift.
    c = _classify(current_content=None, current_content_hash=EDITED_CONTENT_HASH)
    assert c.status is AnchorStatus.NEEDS_REPAIR
    assert c.reason_code == "text_version_drift"


def test_excerpt_exists_elsewhere_is_needs_repair_not_relocated():
    # The excerpt still exists in the edited chapter, but at a shifted offset:
    # the anchor is stale and must NOT move to the new location.
    assert EDITED_TEXT.count(EXCERPT) == 1
    c = _classify(current_content=EDITED_TEXT)
    assert c.status is AnchorStatus.NEEDS_REPAIR
    # The frozen span is preserved — no new coordinates are ever generated.
    assert c.source_start == _EXCERPT_START
    assert c.source_end == _EXCERPT_END
    assert EDITED_TEXT[c.source_start : c.source_end] != EXCERPT


def test_malformed_anchor_hash_is_invalid():
    c = _classify(anchor_hash="not-a-hex-hash")
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "malformed_anchor_hash"

    c = _classify(chapter_content_hash="zz")
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "malformed_anchor_hash"


def test_source_snapshot_incomplete_is_invalid():
    c = _classify(source_snapshot_id="")
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "source_snapshot_incomplete"

    c = _classify(source_snapshot_hash="ff")
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "source_snapshot_incomplete"


def test_wrong_anchor_hash_is_invalid():
    c = _classify(anchor_hash="b" * 64)
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "anchor_hash_mismatch"


def test_out_of_bounds_span_on_unchanged_chapter_is_invalid():
    c = _classify(current_content=CHAPTER_TEXT, source_end=len(CHAPTER_TEXT) + 10)
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "source_range_out_of_bounds"


def test_span_mismatch_on_unchanged_chapter_is_invalid():
    # Same content hash (chapter unchanged) but the span points at wrong text:
    # the anchor is internally inconsistent, not merely stale.
    c = _classify(current_content=CHAPTER_TEXT, source_start=0, source_end=10)
    assert c.status is AnchorStatus.INVALID
    assert c.reason_code == "source_range_mismatch"


def test_range_only_classification_without_content_stays_valid():
    # Without current content the validator cannot detect span drift; the exact
    # internal hashes still replay so the anchor is valid (caller responsibility
    # to pass the current text — the service always does).
    c = _classify(current_content=None, current_content_hash=None)
    assert c.status is AnchorStatus.VALID


# ---------------------------------------------------------------------------
# Deterministic repair candidate key (append-only, span-scoped)
# ---------------------------------------------------------------------------


def test_repair_proposal_key_is_deterministic_and_span_scoped():
    a = repair_proposal_key(
        anchor_id=11,
        source_start=10,
        source_end=20,
        excerpt="abc",
        asset_revision_id=3,
    )
    b = repair_proposal_key(
        anchor_id=11,
        source_start=10,
        source_end=20,
        excerpt="abc",
        asset_revision_id=3,
    )
    assert a == b
    assert a.startswith("repair:11:")
    assert len(a) <= 160
    assert (
        repair_proposal_key(
            anchor_id=12,
            source_start=10,
            source_end=20,
            excerpt="abc",
            asset_revision_id=3,
        )
        != a
    )
    assert (
        repair_proposal_key(
            anchor_id=11,
            source_start=11,
            source_end=20,
            excerpt="abc",
            asset_revision_id=3,
        )
        != a
    )
    assert (
        repair_proposal_key(
            anchor_id=11,
            source_start=10,
            source_end=21,
            excerpt="abc",
            asset_revision_id=3,
        )
        != a
    )
    assert (
        repair_proposal_key(
            anchor_id=11,
            source_start=10,
            source_end=20,
            excerpt="abd",
            asset_revision_id=3,
        )
        != a
    )
    assert (
        repair_proposal_key(
            anchor_id=11,
            source_start=10,
            source_end=20,
            excerpt="abc",
            asset_revision_id=4,
        )
        != a
    )


# ---------------------------------------------------------------------------
# Repair candidate gate (candidate-only, exact new span, no auto-relocation)
# ---------------------------------------------------------------------------


def test_repair_candidate_gate_accepts_exact_new_span():
    proposal = _repair_proposal(
        content=EDITED_TEXT,
        source_start=EDITED_EXCERPT_START,
        source_end=EDITED_EXCERPT_END,
    )
    result = validate_anchor_proposal_contract(proposal, chapter_content=EDITED_TEXT)
    assert result.ok is True
    assert result.status is AnchorStatus.PROPOSED


def test_repair_candidate_gate_rejects_stale_offsets():
    # The frozen (original) offsets against the edited content are stale and
    # must fail closed — never a nearest-match relocation.
    proposal = _repair_proposal(
        content=EDITED_TEXT,
        source_start=_EXCERPT_START,
        source_end=_EXCERPT_END,
    )
    result = validate_anchor_proposal_contract(proposal, chapter_content=EDITED_TEXT)
    assert result.ok is False
    assert result.status is AnchorStatus.INVALID
    assert result.reason_code == "source_range_mismatch"


def test_repair_candidate_gate_rejects_wrong_content_hash():
    proposal = _repair_proposal(
        content=EDITED_TEXT,
        source_start=EDITED_EXCERPT_START,
        source_end=EDITED_EXCERPT_END,
        chapter_content_hash=CHAPTER_CONTENT_HASH,
    )
    result = validate_anchor_proposal_contract(proposal, chapter_content=EDITED_TEXT)
    assert result.ok is False
    assert result.status is AnchorStatus.INVALID
    assert result.reason_code == "chapter_content_hash_mismatch"


def test_repair_candidate_gate_rejects_wrong_anchor_hash():
    proposal = _repair_proposal(
        content=EDITED_TEXT,
        source_start=EDITED_EXCERPT_START,
        source_end=EDITED_EXCERPT_END,
        anchor_hash="c" * 64,
    )
    result = validate_anchor_proposal_contract(proposal, chapter_content=EDITED_TEXT)
    assert result.ok is False
    assert result.reason_code == "anchor_hash_mismatch"


def test_repair_candidate_contract_is_strict_and_frozen():
    proposal = _repair_proposal(
        content=EDITED_TEXT,
        source_start=EDITED_EXCERPT_START,
        source_end=EDITED_EXCERPT_END,
    )
    assert proposal.status is AnchorStatus.PROPOSED
    with pytest.raises(ValidationError):
        _repair_proposal(
            content=EDITED_TEXT,
            source_start=EDITED_EXCERPT_START,
            source_end=EDITED_EXCERPT_END,
            cover_url="http://example.com/x.png",
        )
    assert "cover_url" not in set(IllustrationAnchorProposalContract.model_fields)
    assert "dom_index" not in set(IllustrationAnchorProposalContract.model_fields)


# ---------------------------------------------------------------------------
# Service revalidation (explicit status projection, no silent mutation)
# ---------------------------------------------------------------------------


async def _user_and_novel(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Repair Novel {username}", owner_id=user.id)
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


async def _persist_asset(db_session: AsyncSession, username: str):
    user, novel, chapter = await _user_and_novel(db_session, username)
    job = IllustrationJob(
        owner_id=user.id,
        novel_id=novel.id,
        job_key="job-repair",
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


def _published_anchor(
    owner: User,
    novel: Novel,
    asset: AssetRevision,
    chapter: Chapter,
    *,
    proposal: IllustrationAnchorProposal,
    approval: ApprovalRequest,
    anchor_key: str = "anchor-lantern-courtyard",
) -> IllustrationAnchor:
    return IllustrationAnchor(
        owner_id=owner.id,
        novel_id=novel.id,
        chapter_id=chapter.id,
        chapter_number=4,
        anchor_key=anchor_key,
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
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="illustration-anchor.v1",
    )


async def _approved_anchor(
    db_session: AsyncSession, username: str
) -> tuple[IllustrationAnchor, dict]:
    """Create an approved proposal + published valid anchor (D-34-01 shape)."""
    asset, _, owner, novel, chapter = await _persist_asset(db_session, username)
    approval = ApprovalRequest(
        owner_id=owner.id,
        action="publish_illustration",
        payload_summary={},
        payload_hash=None,
        status="approved",
    )
    db_session.add(approval)
    await db_session.flush()
    proposal = IllustrationAnchorProposal(
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
        approval_request_id=approval.id,
        published_asset_revision_id=asset.id,
        publish_manifest_hash=HEX64,
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
        schema_version="illustration-anchor-proposal.v1",
    )
    db_session.add(proposal)
    await db_session.flush()
    anchor = _published_anchor(
        owner, novel, asset, chapter, proposal=proposal, approval=approval
    )
    db_session.add(anchor)
    await db_session.flush()
    return anchor, {
        "owner_id": owner.id,
        "novel_id": novel.id,
        "chapter_id": chapter.id,
    }


async def test_revalidate_valid_anchor_keeps_valid(db_session: AsyncSession):
    anchor, ids = await _approved_anchor(db_session, "repair_valid")
    result = await AnchorRepairService(db_session).revalidate(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], anchor_id=anchor.id
    )
    assert result.status is AnchorStatus.VALID
    assert anchor.status == "valid"


async def test_revalidate_after_text_edit_persists_needs_repair(
    db_session: AsyncSession,
):
    anchor, ids = await _approved_anchor(db_session, "repair_stale")
    chapter = await db_session.get(Chapter, ids["chapter_id"])
    # Text authority changed (the anchor is now stale).
    chapter.content = EDITED_TEXT
    await db_session.flush()
    result = await AnchorRepairService(db_session).revalidate(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], anchor_id=anchor.id
    )
    assert result.status is AnchorStatus.NEEDS_REPAIR
    assert result.reason_code == "text_version_drift"
    # The status projection is persisted: stale anchors are presented explicitly.
    assert anchor.status == "needs_repair"
    # Content is never mutated and the span is never relocated.
    assert anchor.excerpt == EXCERPT
    assert anchor.source_start == _EXCERPT_START
    assert anchor.source_end == _EXCERPT_END


async def test_revalidate_restores_valid_when_text_reverts(db_session: AsyncSession):
    anchor, ids = await _approved_anchor(db_session, "repair_revert")
    chapter = await db_session.get(Chapter, ids["chapter_id"])
    chapter.content = EDITED_TEXT
    await db_session.flush()
    stale = await AnchorRepairService(db_session).revalidate(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], anchor_id=anchor.id
    )
    assert stale.status is AnchorStatus.NEEDS_REPAIR
    chapter.content = CHAPTER_TEXT
    await db_session.flush()
    valid = await AnchorRepairService(db_session).revalidate(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], anchor_id=anchor.id
    )
    assert valid.status is AnchorStatus.VALID
    assert anchor.status == "valid"


async def test_revalidate_out_of_scope_fails_closed(db_session: AsyncSession):
    anchor, ids = await _approved_anchor(db_session, "repair_scope")
    service = AnchorRepairService(db_session)
    with pytest.raises(ValueError):
        await service.revalidate(
            owner_id=ids["owner_id"] + 999,
            novel_id=ids["novel_id"],
            anchor_id=anchor.id,
        )
    with pytest.raises(ValueError):
        await service.revalidate(
            owner_id=0, novel_id=ids["novel_id"], anchor_id=anchor.id
        )
