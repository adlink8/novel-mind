"""Visual Bible evidence materialization (re-verify leaf evidence vs fresh DB)."""

from __future__ import annotations

from hashlib import sha256

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.user import User
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualClaimContract,
    VisualEvidenceRef,
    claim_content_hash,
)
from app.services.visual_bible.evidence import (
    ChapterRecord,
    VisualBibleEvidenceService,
    chapter_content_hash,
    compute_source_snapshot_hash,
)

pytestmark = pytest.mark.unit

TEXT = "阿宁有着一头银色长发，站在临安城头望向远方。"
HEX64 = "a" * 64


def _ref(chapter_id, chapter_number, start, end, *, ss_id="ss-1", ss_hash=HEX64) -> VisualEvidenceRef:
    return VisualEvidenceRef(
        evidence_key=f"ev-{chapter_id}-{start}",
        source_snapshot_id=ss_id,
        source_snapshot_hash=ss_hash,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=sha256(TEXT[start:end].encode("utf-8")).hexdigest(),
        excerpt=TEXT[start:end],
        cutoff_chapter=3,
    )


def _claim(*, evidence_refs, authority=VisualAuthority.CANON_FACT) -> VisualClaimContract:
    base = VisualClaimContract(
        claim_key="claim-1",
        entity_stable_id="char-ayla",
        authority=authority,
        description="银白长发。",
        author="annotator" if authority is not VisualAuthority.CANON_FACT else None,
        rationale="主题呼应" if authority is not VisualAuthority.CANON_FACT else None,
        cutoff_chapter=3,
        claim_hash="0" * 64,
        evidence_refs=evidence_refs,
    )
    return base.model_copy(update={"claim_hash": claim_content_hash(base)})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_chapter_content_hash_is_deterministic():
    assert chapter_content_hash(TEXT) == chapter_content_hash(TEXT)
    assert chapter_content_hash(TEXT) != chapter_content_hash(TEXT + "!")
    assert len(chapter_content_hash(TEXT)) == 64


def test_compute_source_snapshot_hash_binds_scope_and_content():
    chapters = [ChapterRecord(chapter_id=1, chapter_number=1, content=TEXT)]
    h1 = compute_source_snapshot_hash(owner_id=1, novel_id=1, chapters=chapters)
    h2 = compute_source_snapshot_hash(owner_id=1, novel_id=1, chapters=chapters)
    assert h1 == h2
    other_owner = compute_source_snapshot_hash(owner_id=2, novel_id=1, chapters=chapters)
    assert other_owner != h1
    mutated = [
        ChapterRecord(chapter_id=1, chapter_number=1, content=TEXT + "补充")
    ]
    assert compute_source_snapshot_hash(owner_id=1, novel_id=1, chapters=mutated) != h1
    # order-insensitive via sorted chapter_number
    a = compute_source_snapshot_hash(
        owner_id=1,
        novel_id=1,
        chapters=[
            ChapterRecord(1, 2, TEXT),
            ChapterRecord(2, 1, TEXT),
        ],
    )
    b = compute_source_snapshot_hash(
        owner_id=1,
        novel_id=1,
        chapters=[
            ChapterRecord(2, 1, TEXT),
            ChapterRecord(1, 2, TEXT),
        ],
    )
    assert a == b


# ---------------------------------------------------------------------------
# DB-backed service
# ---------------------------------------------------------------------------


async def _seed(db_session: AsyncSession):
    owner = User(username="vb-ev", email="vb-ev@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="证据书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content=TEXT
    )
    db_session.add(chapter)
    await db_session.flush()
    await db_session.commit()
    return owner, novel, chapter


@pytest.mark.asyncio
async def test_verify_novel_scope(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    assert (await service.verify_novel_scope(owner_id=owner.id, novel_id=novel.id)).id == novel.id
    assert await service.verify_novel_scope(owner_id=999, novel_id=novel.id) is None


@pytest.mark.asyncio
async def test_load_source_snapshot_matches_computed(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    snapshot_hash, records = await service.load_source_snapshot(
        owner_id=owner.id, novel_id=novel.id
    )
    assert snapshot_hash == compute_source_snapshot_hash(
        owner_id=owner.id,
        novel_id=novel.id,
        chapters=[ChapterRecord(chapter_id=chapter.id, chapter_number=1, content=TEXT)],
    )
    assert records[0].content == TEXT


@pytest.mark.asyncio
async def test_materialize_resolves_canon_claim(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    snapshot_hash, _ = await service.load_source_snapshot(owner_id=owner.id, novel_id=novel.id)
    claim = _claim(evidence_refs=[_ref(chapter.id, 1, 0, 10, ss_hash=snapshot_hash)])
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.blocked is False
    assert len(outcome.resolved) == 1
    assert outcome.resolved[0].verified_evidence[0].evidence_key == f"ev-{chapter.id}-0"


@pytest.mark.asyncio
async def test_materialize_unowned_novel_reports_scope(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    claim = _claim(evidence_refs=[_ref(chapter.id, 1, 0, 10)])
    outcome = await service.materialize_version_claims(
        owner_id=999,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.blocked is True
    assert outcome.unresolved[0].reason_code == "owner_scope_mismatch"


@pytest.mark.asyncio
async def test_materialize_stale_snapshot_fails_closed(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    claim = _claim(evidence_refs=[_ref(chapter.id, 1, 0, 10, ss_hash=HEX64)])
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,  # stale vs the novel's current set
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.blocked is True
    assert outcome.unresolved[0].reason_code == "stale_snapshot_lineage"


@pytest.mark.asyncio
async def test_materialize_bad_claim_hash_fails(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    snapshot_hash, _ = await service.load_source_snapshot(owner_id=owner.id, novel_id=novel.id)
    claim = _claim(evidence_refs=[_ref(chapter.id, 1, 0, 10, ss_hash=snapshot_hash)])
    bad = claim.model_copy(update={"claim_hash": "1" * 64})
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=3,
        claims=[bad],
    )
    assert outcome.unresolved[0].reason_code == "claim_hash_mismatch"


@pytest.mark.asyncio
async def test_materialize_interpretation_claim_needs_no_evidence(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    claim = _claim(
        evidence_refs=[],
        authority=VisualAuthority.LITERARY_INTERPRETATION,
    )
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=HEX64,
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.blocked is False
    assert outcome.resolved[0].verified_evidence == ()


@pytest.mark.asyncio
async def test_materialize_missing_chapter_fails(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    snapshot_hash, _ = await service.load_source_snapshot(owner_id=owner.id, novel_id=novel.id)
    # ref points to a chapter id that does not exist in this novel
    ghost_ref = _ref(9999, 1, 0, 10, ss_hash=snapshot_hash)
    claim = _claim(evidence_refs=[ghost_ref])
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.unresolved[0].reason_code == "chapter_missing"


@pytest.mark.asyncio
async def test_materialize_content_hash_mismatch_fails(db_session):
    owner, novel, chapter = await _seed(db_session)
    service = VisualBibleEvidenceService(db_session)
    snapshot_hash, _ = await service.load_source_snapshot(owner_id=owner.id, novel_id=novel.id)
    ref = _ref(chapter.id, 1, 0, 10, ss_hash=snapshot_hash)
    poisoned = ref.model_copy(update={"content_hash": "2" * 64})
    claim = _claim(evidence_refs=[poisoned])
    outcome = await service.materialize_version_claims(
        owner_id=owner.id,
        novel_id=novel.id,
        source_snapshot_id="ss-1",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=3,
        claims=[claim],
    )
    assert outcome.unresolved[0].reason_code == "evidence_content_mismatch"
