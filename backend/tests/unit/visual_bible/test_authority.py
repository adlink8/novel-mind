"""Visual Bible candidate authority seam (create_revision / apply_review / reads)."""

from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
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
    CandidateConflictError,
    CandidateNotFoundError,
    GateViolationError,
    ScopeMismatchError,
    VisualBibleAuthorityService,
    list_versions,
    load_version_view,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64

SOURCE_TEXT = "阿宁有着一头银色长发，站在临安城头。"


def _slice_hash(start: int, end: int) -> str:
    return sha256(SOURCE_TEXT[start:end].encode("utf-8")).hexdigest()


def _evidence(*, evidence_key="ev-ayla-hair", chapter_id=1) -> VisualEvidenceRef:
    return VisualEvidenceRef(
        evidence_key=evidence_key,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        chapter_id=chapter_id,
        chapter_number=1,
        source_start=0,
        source_end=10,
        content_hash=_slice_hash(0, 10),
        cutoff_chapter=3,
    )


def _claim(
    *, claim_key="claim-ayla-hair", entity_stable_id="char-ayla", evidence=None
) -> VisualClaimContract:
    base = VisualClaimContract(
        claim_key=claim_key,
        entity_stable_id=entity_stable_id,
        authority=VisualAuthority.CANON_FACT,
        description="阿宁发色为银白。",
        cutoff_chapter=3,
        claim_hash="0" * 64,
        evidence_refs=evidence or [_evidence()],
    )
    return base.model_copy(update={"claim_hash": claim_content_hash(base)})


def _asset(*, asset_key="ref-ayla-sketch") -> VisualReferenceAssetContract:
    return VisualReferenceAssetContract(
        asset_key=asset_key,
        asset_id="obj-1",
        mime_type="image/png",
        bytes_hash=HEX64_B,
        rights_status=VisualRightsStatus.UNREVIEWED,
    )


def _version(
    *,
    owner_id: int,
    novel_id: int,
    version_key: str = "vb-v1",
    claims=None,
    assets=(),
    parent_version_id=None,
    chapter_id=1,
) -> VisualBibleVersionContract:
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
        parent_version_id=parent_version_id,
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
    return version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(version)}
    )


async def _seed_owner_novel(db_session: AsyncSession):
    from app.models.novel import Chapter

    owner = User(username="vb-owner", email="vb@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="视觉圣经书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content=SOURCE_TEXT
    )
    db_session.add(chapter)
    await db_session.flush()
    await db_session.commit()
    return owner, novel, chapter


def _verified_evidence(version: VisualBibleVersionContract) -> dict:
    return {
        claim.claim_key: tuple(claim.evidence_refs)
        for claim in version.claims
        if claim.authority is VisualAuthority.CANON_FACT
    }


# ---------------------------------------------------------------------------
# create_revision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_revision_persists_candidate(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id)
    service = VisualBibleAuthorityService(db_session)
    result = await service.create_revision(
        owner_id=owner.id,
        novel_id=novel.id,
        version=version,
        verified_evidence=_verified_evidence(version),
    )
    assert result.replayed is False
    assert result.entity_ids["char-ayla"] > 0
    assert result.claim_ids["claim-ayla-hair"] > 0
    assert result.version.manifest_hash == version.manifest_hash
    assert result.version.review_state == VisualReviewState.CANDIDATE.value


@pytest.mark.asyncio
async def test_create_revision_replays_identical_version(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    service = VisualBibleAuthorityService(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id)
    first = await service.create_revision(
        owner_id=owner.id,
        novel_id=novel.id,
        version=version,
        verified_evidence=_verified_evidence(version),
    )
    second = await service.create_revision(
        owner_id=owner.id,
        novel_id=novel.id,
        version=version,
        verified_evidence=_verified_evidence(version),
    )
    assert second.replayed is True
    assert second.version.id == first.version.id


@pytest.mark.asyncio
async def test_create_revision_conflicting_retry_raises(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    service = VisualBibleAuthorityService(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id)
    await service.create_revision(
        owner_id=owner.id,
        novel_id=novel.id,
        version=version,
        verified_evidence=_verified_evidence(version),
    )
    conflicted = _version(
        owner_id=owner.id, novel_id=novel.id, version_key="vb-v1", assets=[_asset()]
    )
    with pytest.raises(CandidateConflictError):
        await service.create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=conflicted,
            verified_evidence=_verified_evidence(conflicted),
        )


@pytest.mark.asyncio
async def test_create_revision_scope_mismatch_on_version(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    version = _version(owner_id=owner.id + 100, novel_id=novel.id)
    with pytest.raises(ScopeMismatchError, match="scope"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=version,
            verified_evidence={},
        )


@pytest.mark.asyncio
async def test_create_revision_unowned_novel_fails(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    other = User(username="vb-other", email="vb2@example.com", hashed_password="x")
    db_session.add(other)
    await db_session.commit()
    version = _version(owner_id=other.id, novel_id=novel.id)
    with pytest.raises(ScopeMismatchError, match="does not own"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=other.id,
            novel_id=novel.id,
            version=version,
            verified_evidence={},
        )


@pytest.mark.asyncio
async def test_create_revision_requires_candidate_state(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id).model_copy(
        update={"review_state": VisualReviewState.APPROVED}
    )
    with pytest.raises(GateViolationError, match="only candidate"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=version,
            verified_evidence={},
        )


@pytest.mark.asyncio
async def test_create_revision_missing_parent_scope_fails(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id, parent_version_id=999)
    with pytest.raises(ScopeMismatchError, match="parent version"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=version,
            verified_evidence=_verified_evidence(version),
        )


@pytest.mark.asyncio
async def test_create_revision_duplicate_stable_id_gate(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    entity = VisualEntityContract(
        stable_id="char-ayla",
        entity_key="e-ayla",
        entity_type=VisualEntityType.CHARACTER,
        description="x",
        authority=VisualAuthority.CANON_FACT,
        disclosure_cutoff=1,
    )
    dup = entity.model_copy(update={"entity_key": "e-ayla-2"})
    version = _version(owner_id=owner.id, novel_id=novel.id).model_copy(
        update={"entities": [entity, dup]}
    )
    with pytest.raises(GateViolationError, match="duplicate entity stable_id"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=version,
            verified_evidence=_verified_evidence(version),
        )


@pytest.mark.asyncio
async def test_create_revision_missing_verified_evidence_fails(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    version = _version(owner_id=owner.id, novel_id=novel.id)
    with pytest.raises(GateViolationError, match="no verified evidence"):
        await VisualBibleAuthorityService(db_session).create_revision(
            owner_id=owner.id,
            novel_id=novel.id,
            version=version,
            verified_evidence={},
        )


@pytest.mark.asyncio
async def test_create_revision_rejects_scope_ids(db_session):
    with pytest.raises(ScopeMismatchError, match="positive integers"):
        VisualBibleAuthorityService._require_scope(owner_id=0, novel_id=1)
    with pytest.raises(ScopeMismatchError, match="positive integers"):
        VisualBibleAuthorityService._require_scope(owner_id=1, novel_id=-5)


# ---------------------------------------------------------------------------
# apply_review
# ---------------------------------------------------------------------------


async def _created_version(db_session: AsyncSession, owner_id, novel_id):
    version = _version(owner_id=owner_id, novel_id=novel_id)
    await VisualBibleAuthorityService(db_session).create_revision(
        owner_id=owner_id,
        novel_id=novel_id,
        version=version,
        verified_evidence=_verified_evidence(version),
    )
    row = (await db_session.scalars(select(VisualBibleVersion))).one()
    return row


def _review_event(owner_id, novel_id, version_id, *, action=VisualReviewAction.REJECT):
    return VisualReviewEventInput(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        action=action,
        actor_source=VisualActorSource.HUMAN,
        actor="reader",
        reason="与原文不符",
        event_key=f"ev-{action.value}-1",
        from_review_state=VisualReviewState.CANDIDATE,
    )


@pytest.mark.asyncio
async def test_apply_review_reject_moves_state(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    service = VisualBibleAuthorityService(db_session)
    updated = await service.apply_review(
        owner_id=owner.id,
        novel_id=novel.id,
        event=_review_event(owner.id, novel.id, row.id),
    )
    assert updated.review_state == VisualReviewState.REJECTED.value


@pytest.mark.asyncio
async def test_apply_review_is_idempotent_on_event_key(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    service = VisualBibleAuthorityService(db_session)
    event = _review_event(owner.id, novel.id, row.id)
    await service.apply_review(owner_id=owner.id, novel_id=novel.id, event=event)
    # repeat with same event_key but now stale from_state: still replays, no error
    await service.apply_review(owner_id=owner.id, novel_id=novel.id, event=event)
    row2 = (await db_session.scalars(select(VisualBibleVersion))).one()
    assert row2.review_state == VisualReviewState.REJECTED.value


@pytest.mark.asyncio
async def test_apply_review_from_state_mismatch_raises(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    event = _review_event(owner.id, novel.id, row.id)
    # first reject moves state to rejected
    await VisualBibleAuthorityService(db_session).apply_review(
        owner_id=owner.id, novel_id=novel.id, event=event
    )
    # second review with a fresh key but stale from_state
    stale = _review_event(
        owner.id, novel.id, row.id, action=VisualReviewAction.SUPERSEDE
    )
    stale = stale.model_copy(
        update={
            "event_key": "ev-supersede-2",
            "from_review_state": VisualReviewState.CANDIDATE,
        }
    )
    with pytest.raises(GateViolationError, match="from_review_state"):
        await VisualBibleAuthorityService(db_session).apply_review(
            owner_id=owner.id, novel_id=novel.id, event=stale
        )


@pytest.mark.asyncio
async def test_apply_review_illegal_transition_raises(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    row = await _created_version(db_session, owner.id, novel.id)
    event = _review_event(owner.id, novel.id, row.id)
    await VisualBibleAuthorityService(db_session).apply_review(
        owner_id=owner.id, novel_id=novel.id, event=event
    )
    # from rejected, supersede is legal; but approve is not (uses from candidate)
    illegal = VisualReviewEventInput(
        owner_id=owner.id,
        novel_id=novel.id,
        version_id=row.id,
        action=VisualReviewAction.APPROVE,
        actor_source=VisualActorSource.HUMAN,
        actor="reader",
        reason="x",
        event_key="ev-approve-3",
        from_review_state=VisualReviewState.REJECTED,
    )
    with pytest.raises(GateViolationError, match="illegal review action"):
        await VisualBibleAuthorityService(db_session).apply_review(
            owner_id=owner.id, novel_id=novel.id, event=illegal
        )


@pytest.mark.asyncio
async def test_apply_review_version_not_found_raises(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    with pytest.raises(CandidateNotFoundError):
        await VisualBibleAuthorityService(db_session).apply_review(
            owner_id=owner.id,
            novel_id=novel.id,
            event=_review_event(owner.id, novel.id, 999),
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_version_view_and_list_versions(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    await _created_version(db_session, owner.id, novel.id)
    await db_session.commit()

    view = await load_version_view(
        db_session, owner_id=owner.id, novel_id=novel.id, version_id=1
    )
    assert view.version_key == "vb-v1"
    assert view.entities[0].stable_id == "char-ayla"
    claim = view.entities[0].claims[0]
    assert claim.claim_key == "claim-ayla-hair"
    assert claim.evidence_refs[0].evidence_key == "ev-ayla-hair"

    versions = await list_versions(db_session, owner_id=owner.id, novel_id=novel.id)
    assert [v.version_key for v in versions] == ["vb-v1"]


@pytest.mark.asyncio
async def test_load_version_view_missing_raises(db_session):
    owner, novel, _ = await _seed_owner_novel(db_session)
    with pytest.raises(CandidateNotFoundError):
        await load_version_view(
            db_session, owner_id=owner.id, novel_id=novel.id, version_id=1
        )
