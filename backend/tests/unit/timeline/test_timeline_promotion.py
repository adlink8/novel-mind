"""Timeline CAS promotion and rollback (validated, byte-identical manifests)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisVersion
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineOverride,
    TimelineParticipant,
    TimelinePointerJournal,
)
from app.models.user import User
from app.services.timeline.promotion import (
    ManifestValidationError,
    StalePointerError,
    _canonical,
    _checksum,
    promote_version,
    rollback_version,
    snapshot_manifest,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure canonicalization helpers
# ---------------------------------------------------------------------------


def test_canonical_sorts_keys_and_uses_default_str():
    assert _canonical({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert _canonical({"n": None}) == '{"n":null}'
    # non-JSON-serializable values fall back to str()
    assert _canonical({"x": object()}).startswith('{"x":')


def test_checksum_is_deterministic_sha256():
    assert _checksum({"a": 1}) == _checksum({"a": 1})
    assert _checksum({"a": 1}) != _checksum({"a": 2})
    assert len(_checksum({})) == 64


# ---------------------------------------------------------------------------
# snapshot_manifest
# ---------------------------------------------------------------------------


async def _seed_scope(db_session: AsyncSession, *, with_events=True):
    owner = User(
        username="promo-owner", email="promo@example.com", hashed_password="x"
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="时间线书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="正文"
    )
    db_session.add(chapter)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="promo-v1",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    event = None
    if with_events:
        event = MachineTimelineEvent(
            version_id=version.id,
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="1:e1",
            title="开局",
            description="描述",
            event_type="plot",
            time_precision="exact",
            narrative_chapter_number=1,
            narrative_index=0,
            story_rank=1,
            story_constraints=[],
            confidence=0.9,
            prompt_hash="c" * 64,
            schema_hash="d" * 64,
            model_lineage={"stage": "chapter_extract"},
            publication_status="provisional",
        )
        db_session.add(event)
        await db_session.flush()
        db_session.add(TimelineParticipant(event_id=event.id, entity_id=None, mention="阿宁"))
        db_session.add(
            TimelineEvidenceRef(
                event_id=event.id,
                chapter_id=chapter.id,
                evidence_id="ev-1",
                source_start=0,
                source_end=1,
                content_hash="0" * 64,
            )
        )
        db_session.add(
            TimelineCausalEdge(
                version_id=version.id,
                source_event_id=event.id,
                target_event_id=event.id,
                edge_type="same",
                confidence=0.5,
                evidence_refs=[],
            )
        )
    await db_session.commit()
    return owner, novel, chapter, version, event


async def _seal_version(db_session: AsyncSession, version: AnalysisVersion) -> None:
    """Fill the version manifest from its immutable rows (like the worker does)."""
    from app.services.timeline.promotion import snapshot_manifest

    manifest, checksum = await snapshot_manifest(db_session, version.id)
    version.manifest = manifest
    version.manifest_checksum = checksum
    await db_session.commit()


@pytest.mark.asyncio
async def test_snapshot_manifest_builds_all_component_checksums(db_session):
    _, _, _, version, event = await _seed_scope(db_session)
    manifest, checksum = await snapshot_manifest(db_session, version.id)
    assert manifest["schema"] == "timeline-manifest.v1"
    assert set(manifest["components"]) == {"events", "participants", "evidence", "edges"}
    assert manifest["events"][0]["logical_event_id"] == "1:e1"
    assert manifest["participants"][0]["mention"] == "阿宁"
    assert manifest["evidence"][0]["evidence_id"] == "ev-1"
    assert manifest["edges"][0]["edge_type"] == "same"
    assert _checksum(manifest) == checksum


@pytest.mark.asyncio
async def test_snapshot_manifest_empty_version(db_session):
    owner, novel, _, version, _ = await _seed_scope(db_session, with_events=False)
    manifest, checksum = await snapshot_manifest(db_session, version.id)
    assert manifest["events"] == []
    assert manifest["participants"] == []
    assert manifest["evidence"] == []
    assert manifest["edges"] == []
    assert len(checksum) == 64


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promote_version_creates_pointer_journal_and_marks_active(db_session):
    owner, novel, _, version, _ = await _seed_scope(db_session)
    await _seal_version(db_session, version)

    pointer = await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=version.id,
        expected_revision=0,
    )
    assert pointer.version_id == version.id
    assert pointer.revision == 1

    version_id = version.id  # capture before expire_all expires the PK too
    db_session.expire_all()
    row = await db_session.scalar(select(TimelineActivePointer))
    assert row is not None and row.revision == 1
    journal = list((await db_session.scalars(select(TimelinePointerJournal))).all())
    assert len(journal) == 1
    assert journal[0].action == "promotion"
    assert journal[0].from_version_id is None
    assert journal[0].to_version_id == version_id
    version_row = await db_session.get(AnalysisVersion, version_id)
    assert version_row.status == "active"


@pytest.mark.asyncio
async def test_promote_second_version_supersedes_first(db_session):
    owner, novel, chapter, v1, _ = await _seed_scope(db_session)
    await _seal_version(db_session, v1)
    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=v1.id,
        expected_revision=0,
    )
    # second version with an extra event
    v2 = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="promo-v2",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(v2)
    await db_session.flush()
    db_session.add(
        MachineTimelineEvent(
            version_id=v2.id,
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="1:e2",
            title="第二版事件",
            description="d",
            event_type="plot",
            time_precision="unknown",
            narrative_chapter_number=1,
            narrative_index=1,
            confidence=0.8,
            prompt_hash="c" * 64,
            schema_hash="d" * 64,
            model_lineage={},
            publication_status="provisional",
        )
    )
    await db_session.commit()
    await _seal_version(db_session, v2)

    pointer = await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=v2.id,
        expected_revision=1,
    )
    assert pointer.revision == 2
    assert pointer.version_id == v2.id
    v1_id, v2_id = v1.id, v2.id
    db_session.expire_all()
    assert (await db_session.get(AnalysisVersion, v1_id)).status == "superseded"
    assert (await db_session.get(AnalysisVersion, v2_id)).status == "active"


@pytest.mark.asyncio
async def test_promote_stale_pointer_revision_raises_and_rolls_back(db_session):
    owner, novel, _, version, _ = await _seed_scope(db_session)
    await _seal_version(db_session, version)
    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=version.id,
        expected_revision=0,
    )
    with pytest.raises(StalePointerError):
        await promote_version(
            db_session,
            owner_id=owner.id,
            novel_id=novel.id,
            candidate_version_id=version.id,
            expected_revision=0,  # now stale; pointer is at revision 1
        )
    # journal still has only the first promotion
    rows = list((await db_session.scalars(select(TimelinePointerJournal))).all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_promote_version_outside_owner_scope_fails(db_session):
    owner, novel, _, version, _ = await _seed_scope(db_session)
    other = User(username="other-owner", email="other@example.com", hashed_password="x")
    db_session.add(other)
    await db_session.commit()
    with pytest.raises(ManifestValidationError, match="outside the requested"):
        await promote_version(
            db_session,
            owner_id=other.id,
            novel_id=novel.id,
            candidate_version_id=version.id,
            expected_revision=0,
        )


@pytest.mark.asyncio
async def test_promote_version_with_bad_status_fails(db_session):
    owner, novel, _, version, _ = await _seed_scope(db_session)
    version.status = "failed"
    await db_session.commit()
    with pytest.raises(ManifestValidationError, match="cannot be activated"):
        await promote_version(
            db_session,
            owner_id=owner.id,
            novel_id=novel.id,
            candidate_version_id=version.id,
            expected_revision=0,
        )


@pytest.mark.asyncio
async def test_promote_version_with_stale_manifest_fails(db_session):
    owner, novel, _, version, event = await _seed_scope(db_session)
    version.manifest = {"schema": "timeline-manifest.v1", "components": {}, "events": [], "participants": [], "evidence": [], "edges": []}
    version.manifest_checksum = "0" * 64
    await db_session.commit()
    with pytest.raises(ManifestValidationError, match="does not match immutable"):
        await promote_version(
            db_session,
            owner_id=owner.id,
            novel_id=novel.id,
            candidate_version_id=version.id,
            expected_revision=0,
        )


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_version_moves_pointer_back_and_journals(db_session):
    owner, novel, _, v1, _ = await _seed_scope(db_session)
    await _seal_version(db_session, v1)
    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=v1.id,
        expected_revision=0,
    )
    pointer = await rollback_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        target_version_id=v1.id,
        expected_revision=1,
    )
    assert pointer.revision == 2
    db_session.expire_all()
    journals = list(
        (await db_session.scalars(select(TimelinePointerJournal).order_by(TimelinePointerJournal.id))).all()
    )
    assert [j.action for j in journals] == ["promotion", "rollback"]


# ---------------------------------------------------------------------------
# Override relinking on promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promotion_relinks_override_when_identity_matches(db_session):
    owner, novel, _, v1, _ = await _seed_scope(db_session)
    await _seal_version(db_session, v1)
    # an active override bound to the old logical event id
    db_session.add(
        TimelineOverride(
            owner_id=owner.id,
            novel_id=novel.id,
            logical_event_id="1:e1",
            field_name="title",
            value={"value": "改标题"},
            status="active",
            needs_relink=False,
        )
    )
    await db_session.commit()
    await promote_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        candidate_version_id=v1.id,
        expected_revision=0,
    )
    db_session.expire_all()
    override = (await db_session.scalars(select(TimelineOverride))).one()
    # same identity mapping (v1 is its own old version) => stays mapped, no relink flag
    assert override.needs_relink is False
