"""Prepare/commit promotion journal tests."""

import pytest
from sqlalchemy import select

from app.models.knowledge_unit import NarrativeActivePointer, NarrativePromotionJournal
from app.services.knowledge_units.promotion import (
    PromotionError,
    narrative_promotion_service,
)
from app.services.knowledge_units.eval import sign_run
from tests.test_knowledge_unit_indexing import _candidate_build


SECRET = "test-release-secret"


def _eval_report(build):
    build.collection_name = build.collection_name or f"candidate_{build.id}"
    report = {
        "run_id": "run-1",
        "passed": True,
        "domain": build.domain_profile,
        "dataset_hash": "e" * 64,
        "build_id": build.id,
        "candidate_checksum": build.manifest_checksum,
        "collection": build.collection_name,
        "owner_id": build.owner_id,
        "novel_id": build.novel_id,
        "outputs": [{"id": "q1"}],
        "faithfulness_failures": 0,
        "canary": {"passed": True},
    }
    report["signature"] = sign_run(report, SECRET)
    return report


def _reconcile(build):
    return {
        **{
            key: []
            for key in (
                "missing",
                "orphan",
                "duplicate",
                "wrong_build",
                "wrong_owner",
                "deleted",
                "deprecated",
            )
        },
        "build_id": build.id,
        "collection": build.collection_name,
    }


async def test_prepare_requires_exact_candidate_and_approval(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    with pytest.raises(PromotionError, match="approval"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=_eval_report(build),
            reconcile_report=_reconcile(build),
            approved_by="",
            evidence_secret=SECRET,
        )
    with pytest.raises(PromotionError, match="checksum"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum="x" * 64,
            eval_report=_eval_report(build),
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_prepare_is_idempotent_and_binds_reports(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    first = await narrative_promotion_service.prepare(
        db_session,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )
    second = await narrative_promotion_service.prepare(
        db_session,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )
    assert first.id == second.id
    assert first.details["eval_runs"][0]["dataset_hash"] == "e" * 64


async def test_commit_activates_exact_candidate(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(
        db_session,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )
    pointer = await narrative_promotion_service.commit(
        db_session, journal_id=journal.id, candidate_checksum=build.manifest_checksum
    )
    assert pointer.build_id == build.id and pointer.pointer_version == 1
    assert build.status == "active" and journal.status == "committed"


async def test_failed_commit_leaves_pointer_absent(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    journal = await narrative_promotion_service.prepare(
        db_session,
        candidate_build_id=build.id,
        candidate_checksum=build.manifest_checksum,
        eval_report=_eval_report(build),
        reconcile_report=_reconcile(build),
        approved_by="owner",
        evidence_secret=SECRET,
    )
    with pytest.raises(PromotionError, match="checksum"):
        await narrative_promotion_service.commit(
            db_session, journal_id=journal.id, candidate_checksum="x" * 64
        )
    assert await db_session.scalar(select(NarrativeActivePointer)) is None
    assert (
        await db_session.get(NarrativePromotionJournal, journal.id)
    ).status == "prepared"


async def test_reconcile_residue_blocks_prepare(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    evaluation = _eval_report(build)
    report = _reconcile(build)
    report["orphan"] = ["unit_bad"]
    with pytest.raises(PromotionError, match="residue"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=evaluation,
            reconcile_report=report,
            approved_by="owner",
            evidence_secret=SECRET,
        )


async def test_forged_or_other_candidate_report_cannot_promote(db_session):
    build = await _candidate_build(db_session)
    build.status = "candidate"
    forged = _eval_report(build)
    forged["build_id"] = 999
    with pytest.raises(PromotionError, match="invalid"):
        await narrative_promotion_service.prepare(
            db_session,
            candidate_build_id=build.id,
            candidate_checksum=build.manifest_checksum,
            eval_report=forged,
            reconcile_report=_reconcile(build),
            approved_by="owner",
            evidence_secret=SECRET,
        )
