"""Affected-subject delta and zero-write refresh tests."""

from sqlalchemy import func, select
import pytest

from app.models.knowledge import KnowledgeRelationJudgment
from app.models.knowledge_unit import (
    NarrativeIndexBuild,
    NarrativeActivePointer,
    NarrativePromotionJournal,
    NarrativeRefreshRun,
    NarrativeSourceWatermark,
    NarrativeUnit,
)
from app.services.knowledge_units.canonicalize import narrative_canonicalizer
from app.services.knowledge_units.incremental import (
    complete_refresh,
    execute_refresh,
    prepare_delta,
    rebuild_affected_candidate,
)
from app.services.knowledge_units.indexing import NarrativeIndexingService
from app.services.knowledge_units.materialize import stable_hash
from app.services.knowledge_units.materialize import narrative_unit_materializer
from app.services.knowledge_units.source_snapshot import source_snapshot_service
from tests.test_knowledge_unit_materialize import _accepted_source
from tests.test_knowledge_unit_indexing import FakeStore
from tests.conftest import TestSessionLocal


async def _watermark(db, snapshot):
    build = NarrativeIndexBuild(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        source_snapshot_id=snapshot.id,
        domain_profile=snapshot.domain_profile,
        build_key=f"wm-{snapshot.id}",
        status="active",
        manifest_checksum="m" * 64,
        config_checksum="c" * 64,
        unit_count=0,
    )
    db.add(build)
    await db.flush()
    row = NarrativeSourceWatermark(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile=snapshot.domain_profile,
        snapshot_id=snapshot.id,
        build_id=build.id,
        source_watermark=snapshot.source_watermark,
        manifest_checksum=build.manifest_checksum,
    )
    db.add(row)
    await db.flush()
    return build


async def test_initial_delta_marks_all_accepted_judgments_added(db_session):
    snapshot = await _accepted_source(db_session)
    plan = await prepare_delta(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        after_snapshot_id=snapshot.id,
    )
    assert len(plan.added) == 1 and not plan.no_change
    assert plan.affected_subjects == ("entity_candidate:1",)


async def test_same_snapshot_is_true_zero_write(db_session):
    snapshot = await _accepted_source(db_session)
    await _watermark(db_session, snapshot)
    before = await db_session.scalar(
        select(func.count()).select_from(NarrativeRefreshRun)
    )
    plan = await prepare_delta(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        after_snapshot_id=snapshot.id,
    )
    report = await execute_refresh(
        db_session,
        plan=plan,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
    )
    after = await db_session.scalar(
        select(func.count()).select_from(NarrativeRefreshRun)
    )
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
    first = await execute_refresh(
        db_session,
        plan=plan,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
    )
    second = await execute_refresh(
        db_session,
        plan=plan,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
    )
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
    rows = list(
        (
            await db_session.scalars(select(NarrativeUnit).order_by(NarrativeUnit.id))
        ).all()
    )
    assert report.status == "candidate" and report.writes["llm"] == 0
    assert [(row.source_snapshot_id, row.status) for row in rows] == [
        (snapshot.id, "deprecated"),
        (changed.id, "candidate"),
    ]


async def test_delta_rejects_cross_owner_work_and_domain(db_session):
    snapshot = await _accepted_source(db_session)
    for scope in (
        (snapshot.owner_id + 1, snapshot.novel_id, "fiction"),
        (snapshot.owner_id, snapshot.novel_id + 1, "fiction"),
        (snapshot.owner_id, snapshot.novel_id, "history"),
    ):
        import pytest

        with pytest.raises(ValueError, match="outside refresh scope"):
            await prepare_delta(
                db_session,
                owner_id=scope[0],
                novel_id=scope[1],
                domain_profile=scope[2],
                after_snapshot_id=snapshot.id,
            )


async def test_complete_refresh_executes_release_chain(db_session, tmp_path):
    import json

    snapshot = await _accepted_source(db_session)
    plan = await prepare_delta(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        after_snapshot_id=snapshot.id,
    )
    payload = {
        "version": "test.v1",
        "domain": "fiction",
        "split": "frozen",
        "cases": [
            {
                "id": "q1",
                "query": "人物关系",
                "gold_ids": ["u1"],
                "gold_evidence_ids": ["e1"],
            }
        ],
    }
    payload["dataset_hash"] = stable_hash(payload)
    fixture = tmp_path / "frozen.json"
    fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    store = FakeStore()
    real = NarrativeIndexingService(store)

    class Indexer:
        async def build_candidate(self, db, *, build_id):
            async def embed(texts):
                return [[0.1, 0.2] for _ in texts]

            return await real.build_candidate(db, build_id=build_id, embedder=embed)

    calls = []

    async def retrieve(query, context):
        calls.append(context["strategy"])
        return [
            {
                "id": "u1",
                "evidence_ids": ["e1"],
                "metadata": {
                    "build_id": context["build_id"],
                    "manifest_checksum": context["candidate_checksum"],
                    "owner_id": context["owner_id"],
                    "novel_id": context["novel_id"],
                    "lifecycle_status": "current",
                },
            }
        ]

    report = await complete_refresh(
        db_session,
        plan=plan,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        approved_by="tester",
        evidence_secret="secret",
        fixture_path=str(fixture),
        indexing_service=Indexer(),
        retrieve=retrieve,
        store=store,
    )
    assert report.status == "committed" and calls == ["chunks", "units", "hybrid"]
    assert report.writes["pointer"] == report.writes["watermark"] == 1


@pytest.mark.parametrize("interrupted_stage", ("indexed", "promoted", "post_reconciled"))
async def test_committed_failure_resumes_in_new_session_without_duplicate_artifacts(
    db_session, tmp_path, interrupted_stage
):
    import json

    snapshot = await _accepted_source(db_session)
    plan = await prepare_delta(
        db_session,
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        domain_profile="fiction",
        after_snapshot_id=snapshot.id,
    )
    payload = {
        "version": "resume.v1",
        "domain": "fiction",
        "split": "frozen",
        "cases": [
            {
                "id": "q1",
                "query": "人物关系",
                "gold_ids": ["u1"],
                "gold_evidence_ids": ["e1"],
            }
        ],
    }
    payload["dataset_hash"] = stable_hash(payload)
    fixture = tmp_path / f"{interrupted_stage}.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    store = FakeStore()
    real = NarrativeIndexingService(store)

    class Indexer:
        calls = 0

        async def build_candidate(self, db, *, build_id):
            self.calls += 1

            async def embed(texts):
                return [[0.1, 0.2] for _ in texts]

            return await real.build_candidate(db, build_id=build_id, embedder=embed)

    indexer = Indexer()

    async def retrieve(query, context):
        return [
            {
                "id": "u1",
                "evidence_ids": ["e1"],
                "metadata": {
                    "build_id": context["build_id"],
                    "manifest_checksum": context["candidate_checksum"],
                    "owner_id": context["owner_id"],
                    "novel_id": context["novel_id"],
                    "lifecycle_status": "current",
                },
            }
        ]

    async def interrupt(stage, run_id):
        if stage == interrupted_stage:
            raise RuntimeError(f"interrupt after {stage}")

    await db_session.commit()
    with pytest.raises(RuntimeError, match="interrupt after"):
        await complete_refresh(
            db_session,
            plan=plan,
            owner_id=snapshot.owner_id,
            novel_id=snapshot.novel_id,
            domain_profile="fiction",
            approved_by="tester",
            evidence_secret="secret",
            fixture_path=str(fixture),
            indexing_service=indexer,
            retrieve=retrieve,
            store=store,
            stage_hook=interrupt,
        )

    async with TestSessionLocal() as fresh:
        failed = await fresh.scalar(select(NarrativeRefreshRun))
        assert failed.status == "failed"
        assert await fresh.scalar(select(NarrativeSourceWatermark)) is None
        if interrupted_stage in {"promoted", "post_reconciled"}:
            assert await fresh.scalar(select(NarrativeActivePointer)) is None
        resumed = await complete_refresh(
            fresh,
            plan=plan,
            owner_id=snapshot.owner_id,
            novel_id=snapshot.novel_id,
            domain_profile="fiction",
            approved_by="tester",
            evidence_secret="secret",
            fixture_path=str(fixture),
            indexing_service=indexer,
            retrieve=retrieve,
            store=store,
        )
        assert resumed.status == "committed"
        assert await fresh.scalar(select(NarrativeSourceWatermark)) is not None
        assert await fresh.scalar(select(NarrativeActivePointer)) is not None
        assert await fresh.scalar(select(func.count()).select_from(NarrativeIndexBuild)) == 1
        assert await fresh.scalar(select(func.count()).select_from(NarrativePromotionJournal)) == 1
        assert indexer.calls == 1
