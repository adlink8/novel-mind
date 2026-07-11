"""Affected-subject delta and zero-write refresh tests."""

from sqlalchemy import func, select

from app.models.knowledge import KnowledgeRelationJudgment
from app.models.knowledge_unit import NarrativeIndexBuild, NarrativeRefreshRun, NarrativeSourceWatermark, NarrativeUnit
from app.services.knowledge_units.canonicalize import narrative_canonicalizer
from app.services.knowledge_units.incremental import execute_refresh, prepare_delta, rebuild_affected_candidate
from app.services.knowledge_units.materialize import narrative_unit_materializer
from app.services.knowledge_units.source_snapshot import source_snapshot_service
from tests.test_knowledge_unit_materialize import _accepted_source


async def _watermark(db, snapshot):
    build = NarrativeIndexBuild(owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, source_snapshot_id=snapshot.id, domain_profile=snapshot.domain_profile, build_key=f"wm-{snapshot.id}", status="active", manifest_checksum="m" * 64, config_checksum="c" * 64, unit_count=0)
    db.add(build)
    await db.flush()
    row = NarrativeSourceWatermark(owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile=snapshot.domain_profile, snapshot_id=snapshot.id, build_id=build.id, source_watermark=snapshot.source_watermark, manifest_checksum=build.manifest_checksum)
    db.add(row)
    await db.flush()
    return build


async def test_initial_delta_marks_all_accepted_judgments_added(db_session):
    snapshot = await _accepted_source(db_session)
    plan = await prepare_delta(db_session, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction", after_snapshot_id=snapshot.id)
    assert len(plan.added) == 1 and not plan.no_change
    assert plan.affected_subjects == ("entity_candidate:1",)


async def test_same_snapshot_is_true_zero_write(db_session):
    snapshot = await _accepted_source(db_session)
    await _watermark(db_session, snapshot)
    before = await db_session.scalar(select(func.count()).select_from(NarrativeRefreshRun))
    plan = await prepare_delta(db_session, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction", after_snapshot_id=snapshot.id)
    report = await execute_refresh(db_session, plan=plan, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction")
    after = await db_session.scalar(select(func.count()).select_from(NarrativeRefreshRun))
    assert plan.no_change and report.run_id is None and report.status == "no_change"
    assert all(value == 0 for value in report.writes.values())
    assert before == after == 0


async def test_changed_judgment_creates_resumable_refresh_run(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    await narrative_canonicalizer.canonicalize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    await _watermark(db_session, snapshot)
    judgment = await db_session.scalar(select(KnowledgeRelationJudgment))
    judgment.structured_output = {"revision": 2}
    await db_session.flush()
    changed = await source_snapshot_service.create_snapshot(db_session, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction")
    plan = await prepare_delta(db_session, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction", after_snapshot_id=changed.id)
    first = await execute_refresh(db_session, plan=plan, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction")
    second = await execute_refresh(db_session, plan=plan, owner_id=snapshot.owner_id, novel_id=snapshot.novel_id, domain_profile="fiction")
    assert len(plan.changed) == 1
    assert first.run_id == second.run_id and first.status == "prepared"


async def test_changed_subject_rebuilds_fresh_candidate_only(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    await narrative_canonicalizer.canonicalize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    await _watermark(db_session, snapshot)
    judgment = await db_session.scalar(select(KnowledgeRelationJudgment))
    judgment.confidence = 0.91
    await db_session.flush()
    changed = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
    )
    plan = await prepare_delta(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        after_snapshot_id=changed.id,
    )
    report = await rebuild_affected_candidate(
        db_session,
        plan=plan,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
    )
    rows = list((await db_session.scalars(select(NarrativeUnit).order_by(NarrativeUnit.id))).all())
    assert report.status == "candidate" and report.writes["llm"] == 0
    assert [(row.source_snapshot_id, row.status) for row in rows] == [
        (snapshot.id, "deprecated"),
        (changed.id, "candidate"),
    ]
