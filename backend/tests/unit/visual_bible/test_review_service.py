"""Visual Bible review service (append_event / approval gate / envelope builder)."""

from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.user import User
from app.models.visual_bible import VisualBibleVersion
from app.schemas.visual_bible import (
    VisualActorSource,
    VisualAuthority,
    VisualBibleVersionContract,
    VisualClaimContract,
    VisualEntityContract,
    VisualEntityType,
    VisualEvidenceRef,
    VisualReferenceAssetContract,
    VisualReviewAction,
    VisualReviewEventInput,
    VisualReviewState,
    VisualRightsStatus,
    claim_content_hash,
    recompute_manifest_hash,
)
from app.services.visual_bible.authority import (
    GateViolationError,
    ScopeMismatchError,
    VisualBibleAuthorityService,
)
from app.services.visual_bible.review import (
    VisualBibleReviewService,
    build_review_envelope,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
TEXT = "阿宁有着一头银色长发，站在临安城头。"


def _evidence(ss_hash=HEX64) -> VisualEvidenceRef:
    return VisualEvidenceRef(
        evidence_key="ev-ayla-hair",
        source_snapshot_id="ss-1",
        source_snapshot_hash=ss_hash,
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=10,
        content_hash=sha256(TEXT[0:10].encode("utf-8")).hexdigest(),
        cutoff_chapter=3,
    )


def _claim(*, evidence=None) -> VisualClaimContract:
    base = VisualClaimContract(
        claim_key="claim-ayla-hair",
        entity_stable_id="char-ayla",
        authority=VisualAuthority.CANON_FACT,
        description="阿宁发色为银白。",
        cutoff_chapter=3,
        claim_hash="0" * 64,
        evidence_refs=evidence or [_evidence()],
    )
    return base.model_copy(update={"claim_hash": claim_content_hash(base)})


def _asset(*, rights=VisualRightsStatus.CLEARED) -> VisualReferenceAssetContract:
    return VisualReferenceAssetContract(
        asset_key="ref-ayla-sketch",
        asset_id="obj-1",
        mime_type="image/png",
        bytes_hash=HEX64_B,
        rights_status=rights,
    )


def _version(*, owner_id, novel_id, claims=None, assets=(), version_key="vb-review-v1"):
    entity = VisualEntityContract(
        stable_id="char-ayla",
        entity_key="e-ayla",
        entity_type=VisualEntityType.CHARACTER,
        description="临安城少女。",
        authority=VisualAuthority.CANON_FACT,
        disclosure_cutoff=1,
    )
    version = VisualBibleVersionContract(
        owner_id=owner_id,
        novel_id=novel_id,
        version_key=version_key,
        revision_number=1,
        parent_version_id=None,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=3,
        schema_hash=HEX64,
        policy_hash=HEX64_B,
        prompt_hash="c" * 64,
        model_hash="d" * 64,
        config_hash="e" * 64,
        manifest_hash="0" * 64,
        entities=[entity],
        claims=claims or [_claim()],
        reference_assets=list(assets),
        review_state=VisualReviewState.CANDIDATE,
    )
    return version.model_copy(update={"manifest_hash": recompute_manifest_hash(version)})


def _verified(version: VisualBibleVersionContract) -> dict:
    return {
        c.claim_key: tuple(c.evidence_refs)
        for c in version.claims
        if c.authority is VisualAuthority.CANON_FACT
    }


async def _seed(db_session: AsyncSession):
    owner = User(username="vb-rv", email="vb-rv@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="评审书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    db_session.add(
        Chapter(novel_id=novel.id, chapter_number=1, title="第一章", content=TEXT)
    )
    await db_session.flush()
    await db_session.commit()
    return owner, novel


async def _created_version(db_session, owner_id, novel_id, **version_kwargs) -> VisualBibleVersion:
    version = _version(owner_id=owner_id, novel_id=novel_id, **version_kwargs)
    await VisualBibleAuthorityService(db_session).create_revision(
        owner_id=owner_id,
        novel_id=novel_id,
        version=version,
        verified_evidence=_verified(version),
    )
    return (await db_session.scalars(select(VisualBibleVersion))).one()


def _event(owner_id, novel_id, version_id, *, action=VisualReviewAction.APPROVE):
    return VisualReviewEventInput(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        action=action,
        actor_source=VisualActorSource.HUMAN,
        actor="editor",
        reason="符合设定",
        event_key=f"ev-{action.value}-1",
        from_review_state=VisualReviewState.CANDIDATE,
    )


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_event_approve_passes_gate(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(db_session, owner.id, novel.id, assets=[_asset()])
    service = VisualBibleReviewService(db_session)
    updated = await service.append_event(
        owner_id=owner.id, novel_id=novel.id, event=_event(owner.id, novel.id, row.id)
    )
    assert updated.review_state == VisualReviewState.APPROVED.value
    # approval is recorded as an append-only event with audit details
    from app.models.visual_bible import VisualBibleReviewEvent

    event_rows = list(
        (await db_session.scalars(select(VisualBibleReviewEvent))).all()
    )
    assert len(event_rows) == 1
    assert event_rows[0].details["budget"]["status"] == "not_applicable"
    assert event_rows[0].details["approval_gate"]["ok"] is True


@pytest.mark.asyncio
async def test_append_event_approval_blocked_by_unresolved_evidence(db_session):
    owner, novel = await _seed(db_session)
    # create a canon_fact candidate with persisted evidence, then drop the rows
    row = await _created_version(db_session, owner.id, novel.id)
    from app.models.visual_bible import VisualEvidenceRef as VbEvidenceRow

    await db_session.execute(VbEvidenceRow.__table__.delete())
    await db_session.commit()

    with pytest.raises(GateViolationError, match="approval blocked by evidence_unresolved"):
        await VisualBibleReviewService(db_session).append_event(
            owner_id=owner.id,
            novel_id=novel.id,
            event=_event(owner.id, novel.id, row.id),
        )


@pytest.mark.asyncio
async def test_append_event_approval_blocked_by_rights(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(
        db_session, owner.id, novel.id, assets=[_asset(rights=VisualRightsStatus.UNREVIEWED)]
    )
    with pytest.raises(GateViolationError, match="approval blocked by rights_unresolved"):
        await VisualBibleReviewService(db_session).append_event(
            owner_id=owner.id,
            novel_id=novel.id,
            event=_event(owner.id, novel.id, row.id),
        )


@pytest.mark.asyncio
async def test_append_event_reject_and_replay_idempotent(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    service = VisualBibleReviewService(db_session)
    event = _event(owner.id, novel.id, row.id, action=VisualReviewAction.REJECT)
    updated = await service.append_event(owner_id=owner.id, novel_id=novel.id, event=event)
    assert updated.review_state == VisualReviewState.REJECTED.value
    # same event_key replays without error even though the state moved on
    await service.append_event(owner_id=owner.id, novel_id=novel.id, event=event)
    from app.models.visual_bible import VisualBibleReviewEvent

    rows = list((await db_session.scalars(select(VisualBibleReviewEvent))).all())
    assert len(rows) == 1  # no second event appended


@pytest.mark.asyncio
async def test_append_event_scope_mismatch_raises(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    event = _event(owner.id, novel.id, row.id)
    with pytest.raises(ScopeMismatchError):
        await VisualBibleReviewService(db_session).append_event(
            owner_id=owner.id + 100, novel_id=novel.id, event=event
        )


# ---------------------------------------------------------------------------
# build_review_envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_review_envelope_candidate_carries_gate(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(db_session, owner.id, novel.id, assets=[_asset()])
    envelope = await build_review_envelope(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=row.id
    )
    assert envelope.revision_ref.version_id == row.id
    assert envelope.review_state is VisualReviewState.CANDIDATE
    assert envelope.approval_gate is not None
    assert envelope.approval_gate.ok is True
    assert envelope.parent_revision_ref is None


@pytest.mark.asyncio
async def test_build_review_envelope_approved_omits_gate(db_session):
    owner, novel = await _seed(db_session)
    row = await _created_version(db_session, owner.id, novel.id, assets=[_asset()])
    await VisualBibleReviewService(db_session).append_event(
        owner_id=owner.id,
        novel_id=novel.id,
        event=_event(owner.id, novel.id, row.id),
    )
    envelope = await build_review_envelope(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=row.id
    )
    assert envelope.review_state is VisualReviewState.APPROVED
    assert envelope.approval_gate is None
    assert len(envelope.review_events) == 1


@pytest.mark.asyncio
async def test_build_review_envelope_with_parent_ref(db_session):
    owner, novel = await _seed(db_session)
    parent = await _created_version(db_session, owner.id, novel.id)
    child_version = _version(owner_id=owner.id, novel_id=novel.id, version_key="vb-review-v2")
    child_version = child_version.model_copy(
        update={
            "revision_number": 2,
            "parent_version_id": parent.id,
            "manifest_hash": "0" * 64,
        }
    )
    child_version = child_version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(child_version)}
    )
    await VisualBibleAuthorityService(db_session).create_revision(
        owner_id=owner.id,
        novel_id=novel.id,
        version=child_version,
        verified_evidence=_verified(child_version),
    )
    child_row = (
        await db_session.scalars(
            select(VisualBibleVersion).where(VisualBibleVersion.version_key == "vb-review-v2")
        )
    ).one()
    envelope = await build_review_envelope(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=child_row.id
    )
    assert envelope.parent_revision_ref is not None
    assert envelope.parent_revision_ref.version_id == parent.id
