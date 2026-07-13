import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.analysis import AnalysisVersion
from app.models.novel import Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineEvidenceRef,
    TimelineOverride,
    TimelineParticipant,
    TimelinePointerJournal,
)
from app.models.user import User
from app.services.timeline.promotion import (
    ManifestValidationError,
    StalePointerError,
    promote_version,
    rollback_version,
    snapshot_manifest,
)

pytestmark = pytest.mark.integration


async def _candidate(session, owner_id: int, novel_id: int, key: str,
                     logical_id: str, evidence_id: str) -> AnalysisVersion:
    version = AnalysisVersion(
        owner_id=owner_id, novel_id=novel_id, version_key=key, status="candidate",
        source_snapshot_hash="a" * 64, hierarchy_build_id="build", hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64, schema_hash="d" * 64,
        model_lineage={"provider": "fake", "model": "quality", "revision": "r1"},
        decoding_hash="e" * 64, config_hash="f" * 64, price_snapshot={"frozen": True}, manifest={},
    )
    session.add(version)
    await session.flush()
    event = MachineTimelineEvent(
        version_id=version.id, owner_id=owner_id, novel_id=novel_id,
        logical_event_id=logical_id, title=logical_id, description=logical_id,
        event_type="plot", time_precision="unknown", narrative_chapter_number=1,
        narrative_index=0, story_rank=0, story_constraints=[], confidence=.9,
        prompt_hash="c" * 64, schema_hash="d" * 64, model_lineage={"revision": "r1"},
        publication_status="published",
    )
    session.add(event)
    await session.flush()
    session.add(TimelineParticipant(event_id=event.id, mention="阿宁"))
    session.add(TimelineEvidenceRef(event_id=event.id, chapter_id=1, evidence_id=evidence_id,
                                    source_start=0, source_end=2, content_hash="9" * 64))
    await session.flush()
    manifest, checksum = await snapshot_manifest(session, version.id)
    version.manifest, version.manifest_checksum = manifest, checksum
    return version


@pytest.mark.asyncio
async def test_postgres_stale_cas_failed_candidate_and_byte_identical_rollback(pg_async_url, require_postgres):
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    async with factory() as setup:
        user = User(username=f"timeline-{suffix}", email=f"timeline-{suffix}@example.test",
                    hashed_password="x")
        setup.add(user)
        await setup.flush()
        novel = Novel(owner_id=user.id, title=f"timeline-{suffix}", status="ready")
        setup.add(novel)
        await setup.flush()
        v1 = await _candidate(setup, user.id, novel.id, f"v1-{suffix}", "old", "ev-stable")
        v2 = await _candidate(setup, user.id, novel.id, f"v2-{suffix}", "new", "ev-stable")
        bad = await _candidate(setup, user.id, novel.id, f"bad-{suffix}", "bad", "ev-bad")
        bad.manifest_checksum = "0" * 64
        setup.add(TimelineOverride(owner_id=user.id, novel_id=novel.id, logical_event_id="old",
                                   field_name="title", value={"value": "人工标题"}))
        await setup.commit()
        owner_id, novel_id, v1_id, v2_id, bad_id = user.id, novel.id, v1.id, v2.id, bad.id

    async with factory() as session:
        pointer = await promote_version(session, owner_id=owner_id, novel_id=novel_id,
                                        candidate_version_id=v1_id, expected_revision=0)
        assert pointer.revision == 1
        v1_manifest = pointer.manifest_checksum

    async with factory() as winner:
        await promote_version(winner, owner_id=owner_id, novel_id=novel_id,
                              candidate_version_id=v2_id, expected_revision=1)
    async with factory() as stale:
        with pytest.raises(StalePointerError):
            await promote_version(stale, owner_id=owner_id, novel_id=novel_id,
                                  candidate_version_id=v1_id, expected_revision=1)
    async with factory() as invalid:
        with pytest.raises(ManifestValidationError):
            await promote_version(invalid, owner_id=owner_id, novel_id=novel_id,
                                  candidate_version_id=bad_id, expected_revision=2)

    async with factory() as session:
        pointer = await rollback_version(session, owner_id=owner_id, novel_id=novel_id,
                                         target_version_id=v1_id, expected_revision=2)
        assert pointer.version_id == v1_id
        assert pointer.manifest_checksum == v1_manifest
        journal = (await session.scalars(
            select(TimelinePointerJournal).where(TimelinePointerJournal.owner_id == owner_id)
            .order_by(TimelinePointerJournal.id.desc())
        )).first()
        assert journal.action == "rollback"
        version = await session.get(AnalysisVersion, v1_id)
        assert journal.manifest == version.manifest
        override = (await session.scalars(select(TimelineOverride).where(
            TimelineOverride.owner_id == owner_id))).one()
        assert override.logical_event_id == "new" or override.needs_relink is True
        active = (await session.scalars(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == owner_id))).one()
        assert active.version_id == v1_id
    await engine.dispose()
