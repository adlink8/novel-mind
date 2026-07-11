"""Promotion rollback/restore and final watermark tests."""

import pytest
from sqlalchemy import select

from app.models.knowledge_unit import NarrativeActivePointer, NarrativeSourceWatermark
from app.services.knowledge_units.promotion import narrative_promotion_service
from app.services.knowledge_units.reconcile import reconcile_build
from app.services.knowledge_units.rollback import RollbackError, advance_watermark, rollback_journal, restore_journal
from tests.test_knowledge_unit_indexing import _candidate_build


def _eval():
    return {"passed": True, "dataset_hash": "e" * 64, "canary": {"passed": True}}


def _clean():
    return {key: [] for key in ("missing", "orphan", "duplicate", "wrong_owner", "deleted", "deprecated")}


async def _promoted(db):
    build = await _candidate_build(db)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(db, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval(), reconcile_report=_clean(), approved_by="owner")
    pointer = await narrative_promotion_service.commit(db, journal_id=journal.id, candidate_checksum=build.manifest_checksum)
    return build, journal, pointer


async def test_rollback_restore_drill_is_reversible(db_session):
    build, journal, pointer = await _promoted(db_session)
    assert pointer.build_id == build.id
    rolled = await rollback_journal(db_session, journal_id=journal.id)
    assert rolled is None
    assert await db_session.scalar(select(NarrativeActivePointer)) is None
    restored = await restore_journal(db_session, journal_id=journal.id)
    assert restored.build_id == build.id and build.status == "active"
    assert restored.pointer_version == 2


async def test_watermark_advances_only_after_clean_active_reconcile(db_session):
    build, _, _ = await _promoted(db_session)
    units = []
    from app.models.knowledge_unit import NarrativeUnit
    units = list((await db_session.scalars(select(NarrativeUnit).where(NarrativeUnit.source_snapshot_id == build.source_snapshot_id))).all())
    actual = [{"id": f"unit_{unit.canonical_id}_{unit.id}", "metadata": {"owner_id": build.owner_id, "novel_id": build.novel_id}} for unit in units]
    reconcile = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    watermark = await advance_watermark(db_session, build_id=build.id, snapshot_id=build.source_snapshot_id, reconcile=reconcile)
    assert watermark.build_id == build.id


async def test_dirty_reconcile_cannot_advance_watermark(db_session):
    build, _, _ = await _promoted(db_session)
    reconcile = await reconcile_build(db_session, build_id=build.id, actual_items=[])
    with pytest.raises(RollbackError, match="clean reconcile"):
        await advance_watermark(db_session, build_id=build.id, snapshot_id=build.source_snapshot_id, reconcile=reconcile)
    assert await db_session.scalar(select(NarrativeSourceWatermark)) is None
