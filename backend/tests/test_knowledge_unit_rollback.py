"""Promotion rollback/restore and final watermark tests."""

import pytest

pytestmark = pytest.mark.unit
from sqlalchemy import select

from app.models.knowledge_unit import NarrativeActivePointer, NarrativeSourceWatermark
from app.services.knowledge_units.promotion import narrative_promotion_service
from app.services.knowledge_units.reconcile import reconcile_build
from app.services.knowledge_units.rollback import (
    RollbackError,
    advance_watermark,
    collection_checkpoint_probe,
    rollback_journal,
    restore_journal,
)
from tests.test_knowledge_unit_indexing import _candidate_build
from tests.test_knowledge_unit_promotion import SECRET, _eval_report, _reconcile
from tests.conftest import TestSessionLocal


async def _promoted(db):
    build = await _candidate_build(db)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(
        db,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )
    pointer = await narrative_promotion_service.commit(
        db,
        journal_id=journal.id,
        candidate_checksum=build.manifest_checksum,
        evidence_secret=SECRET,
    )
    return build, journal, pointer


async def test_rollback_restore_drill_is_reversible(db_session):
    build, journal, pointer = await _promoted(db_session)

    async def probe(checkpoint):
        return checkpoint.get("build_id") == build.id

    assert pointer.build_id == build.id
    rolled = await rollback_journal(
        db_session, journal_id=journal.id, collection_probe=probe
    )
    assert rolled is None
    assert await db_session.scalar(select(NarrativeActivePointer)) is None
    restored = await restore_journal(
        db_session, journal_id=journal.id, collection_probe=probe
    )
    assert restored.build_id == build.id and build.status == "active"
    assert restored.pointer_version == 2


async def test_watermark_advances_only_after_clean_active_reconcile(db_session):
    build, _, _ = await _promoted(db_session)
    units = []
    from app.models.knowledge_unit import NarrativeUnit

    units = list(
        (
            await db_session.scalars(
                select(NarrativeUnit).where(
                    NarrativeUnit.source_snapshot_id == build.source_snapshot_id
                )
            )
        ).all()
    )
    actual = [
        {
            "id": f"unit_{unit.canonical_id}_{unit.id}",
            "metadata": {
                "owner_id": build.owner_id,
                "novel_id": build.novel_id,
                "build_id": build.id,
                "manifest_checksum": build.manifest_checksum,
            },
        }
        for unit in units
    ]
    reconcile = await reconcile_build(
        db_session, build_id=build.id, actual_items=actual
    )
    watermark = await advance_watermark(
        db_session,
        build_id=build.id,
        snapshot_id=build.source_snapshot_id,
        reconcile=reconcile,
    )
    assert watermark.build_id == build.id


async def test_dirty_reconcile_cannot_advance_watermark(db_session):
    build, _, _ = await _promoted(db_session)
    reconcile = await reconcile_build(db_session, build_id=build.id, actual_items=[])
    with pytest.raises(RollbackError, match="clean reconcile"):
        await advance_watermark(
            db_session,
            build_id=build.id,
            snapshot_id=build.source_snapshot_id,
            reconcile=reconcile,
        )
    assert await db_session.scalar(select(NarrativeSourceWatermark)) is None


async def test_committed_new_session_rollback_restore_and_collection_probe(db_session):
    build, journal, _ = await _promoted(db_session)
    await db_session.commit()

    async def probe(checkpoint):
        return checkpoint == journal.details["after"]

    async with TestSessionLocal() as fresh:
        assert (
            await rollback_journal(fresh, journal_id=journal.id, collection_probe=probe)
            is None
        )
        await fresh.commit()
    async with TestSessionLocal() as fresh:
        pointer = await restore_journal(
            fresh, journal_id=journal.id, collection_probe=probe
        )
        await fresh.commit()
        assert pointer.build_id == build.id


async def test_collection_checkpoint_failure_is_recoverable(db_session):
    _, journal, _ = await _promoted(db_session)
    await db_session.commit()

    async def missing_collection(checkpoint):
        return False

    async with TestSessionLocal() as fresh:
        with pytest.raises(RollbackError, match="collection checkpoint"):
            await rollback_journal(
                fresh, journal_id=journal.id, collection_probe=missing_collection
            )
        await fresh.rollback()


async def test_production_gateway_rejects_missing_and_wrong_manifest(db_session):
    from tests.test_knowledge_unit_indexing import FakeStore

    build, journal, _ = await _promoted(db_session)
    store = FakeStore()
    probe = collection_checkpoint_probe(store)
    with pytest.raises(RollbackError, match="collection checkpoint"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=probe
        )

    collection = store.get_named_collection(build.collection_name, create=True)
    collection.ids = ["unit-1"]
    collection.metadatas = [
        {"build_id": build.id, "manifest_checksum": "wrong-manifest"}
    ]
    with pytest.raises(RollbackError, match="collection checkpoint"):
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=probe
        )


async def test_production_gateway_rollback_then_restore(db_session):
    from tests.test_knowledge_unit_indexing import FakeStore

    build, journal, _ = await _promoted(db_session)
    store = FakeStore()
    collection = store.get_named_collection(build.collection_name, create=True)
    collection.ids = ["unit-1"]
    collection.metadatas = [
        {"build_id": build.id, "manifest_checksum": build.manifest_checksum}
    ]
    probe = collection_checkpoint_probe(store)
    assert (
        await rollback_journal(
            db_session, journal_id=journal.id, collection_probe=probe
        )
        is None
    )
    restored = await restore_journal(
        db_session, journal_id=journal.id, collection_probe=probe
    )
    assert restored.build_id == build.id
