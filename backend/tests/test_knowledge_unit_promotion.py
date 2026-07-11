"""Prepare/commit promotion journal tests."""

import pytest
from sqlalchemy import select

from app.models.knowledge_unit import NarrativeActivePointer, NarrativePromotionJournal
from app.services.knowledge_units.promotion import PromotionError, narrative_promotion_service
from tests.test_knowledge_unit_indexing import _candidate_build


def _eval_report():
    return {"passed": True, "dataset_hash": "e" * 64, "canary": {"passed": True}}


def _reconcile():
    return {key: [] for key in ("missing", "orphan", "duplicate", "wrong_owner", "deleted", "deprecated")}


async def test_prepare_requires_exact_candidate_and_approval(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    with pytest.raises(PromotionError, match="approval"):
        await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="")
    with pytest.raises(PromotionError, match="checksum"):
        await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum="x" * 64, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="owner")


async def test_prepare_is_idempotent_and_binds_reports(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    first = await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="owner")
    second = await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="owner")
    assert first.id == second.id
    assert first.details["dataset_hash"] == "e" * 64


async def test_commit_activates_exact_candidate(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="owner")
    pointer = await narrative_promotion_service.commit(db_session, journal_id=journal.id, candidate_checksum=build.manifest_checksum)
    assert pointer.build_id == build.id and pointer.pointer_version == 1
    assert build.status == "active" and journal.status == "committed"


async def test_failed_commit_leaves_pointer_absent(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=_reconcile(), approved_by="owner")
    with pytest.raises(PromotionError, match="checksum"):
        await narrative_promotion_service.commit(db_session, journal_id=journal.id, candidate_checksum="x" * 64)
    assert await db_session.scalar(select(NarrativeActivePointer)) is None
    assert (await db_session.get(NarrativePromotionJournal, journal.id)).status == "prepared"


async def test_reconcile_residue_blocks_prepare(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    report = _reconcile()
    report["orphan"] = ["unit_bad"]
    with pytest.raises(PromotionError, match="residue"):
        await narrative_promotion_service.prepare(db_session, candidate_build_id=build.id, candidate_checksum=build.manifest_checksum, eval_report=_eval_report(), reconcile_report=report, approved_by="owner")
