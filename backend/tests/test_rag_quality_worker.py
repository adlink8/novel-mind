"""Durable quality worker: lease/heartbeat/checkpoint/resume/cancel + lineage (06-04/06-08)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.eval import CalibrationReport, ChunkerLineage, EvalCase, SourceSnapshot
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, load_json, stable_hash
from app.services.rag_quality import (
    default_healthy,
    default_stub_answer,
    make_baseline_from_metrics,
    recompute_chunker_config_hash,
    run_quality_evaluation,
)
from app.services.rag_quality_worker import (
    QualityJobStore,
    QualityWorkerError,
    RagQualityWorker,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

SECRET = DEFAULT_SIGNING_SECRET
EVALS = Path(__file__).resolve().parents[1] / "evals"


def _chunker_for(
    snap: SourceSnapshot,
    name: str = "baseline-fixed",
    version: str = "1.0.0",
    **cfg_extra,
):
    cfg = {"size": 512, "overlap": 64, **cfg_extra}
    return ChunkerLineage(
        chunker_name=name,
        chunker_version=version,
        chunker_config=cfg,
        chunker_config_hash=recompute_chunker_config_hash(cfg),
        chunk_manifest_hash=stable_hash(
            {
                "chunks": [c.content_hash for c in snap.chunks],
                "chunker": name,
                "version": version,
                "config": cfg,
            }
        ),
        source_snapshot_hash=snap.manifest_hash,
    )


def _fixtures():
    data = load_json(EVALS / "fixtures" / "rag-quality-benchmark.v1.json")
    snap = SourceSnapshot.model_validate(data["snapshot"])
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    g, j = cases[0].generator_lineage, cases[0].judge_lineage
    cal = CalibrationReport(
        suite_hash="a" * 64,
        suite_signature="b" * 64,
        prompt_hash=j.prompt_hash,
        schema_hash=j.schema_hash,
        judge_lineage=j,
        domain="calibration-synthetic",
        repeats=3,
        confusion_matrix={},
        critical_false_accept=0,
        consistency=1.0,
        status="passed",
        metrics={"consistency": 1.0, "critical_false_accept": 0},
        quality_comparable=False,
    )
    chunker = _chunker_for(snap)
    pre = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 0.0,
            "answer_relevance_mean": 0.0,
            "cost_usd_total": 999.0,
        },
        health=default_healthy(),
        secret=SECRET,
        chunker_lineage=chunker,
    )
    assert pre.get("metrics"), pre
    baseline = make_baseline_from_metrics(pre["metrics"])
    return snap, cases, g, j, cal, baseline, chunker


@pytest.fixture()
def worker():
    store = QualityJobStore()
    return RagQualityWorker(store=store, secret=SECRET)


@pytest.mark.asyncio
async def test_create_and_run_to_terminal(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=chunker,
    )
    assert job.status == "queued"
    assert job.chunker_name == chunker.chunker_name
    assert job.chunker_config_hash == recompute_chunker_config_hash(
        chunker.chunker_config
    )
    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    done = await worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status in {"passed", "qualified"}
    assert done.quality_comparable is True
    assert done.metrics is not None
    assert done.report_signature
    assert done.output_hash
    public = await worker.get_status(job.job_id, owner_id=1)
    assert public["status"] == done.status
    assert public["quality_comparable"] is True


@pytest.mark.asyncio
async def test_cross_owner_denied(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker,
    )
    with pytest.raises(QualityWorkerError) as ei:
        await worker.get_status(job.job_id, owner_id=2)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_lease_conflict(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker,
    )
    await worker.acquire_lease(job.job_id, owner_id=1)
    with pytest.raises(QualityWorkerError) as ei:
        await worker.acquire_lease(job.job_id, owner_id=1)
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_heartbeat_extends_lease(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker,
    )
    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    j0 = await worker.store.get(job.job_id)
    exp1 = j0.lease_expires_at
    assert await worker.heartbeat(job.job_id, lease)
    j1 = await worker.store.get(job.job_id)
    assert j1.lease_expires_at >= exp1


@pytest.mark.asyncio
async def test_crash_resume_idempotent_no_duplicate_calls(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=chunker,
    )
    calls = {"n": 0}

    def counting_answer(case, retrieved):
        calls["n"] += 1
        return default_stub_answer(case, retrieved)

    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await worker.run(
            job.job_id,
            lease_id=lease,
            owner_id=1,
            answer_fn=counting_answer,
            crash_after_stage="scoring",
        )
    n_after_crash = calls["n"]
    assert n_after_crash >= 1
    # Release and resume with a fresh worker (restart simulation)
    await worker.release_lease(job.job_id, lease)
    mid = await worker.store.get(job.job_id)
    assert "scoring" in (mid.checkpoint.get("committed") or [])
    store2 = worker.store  # same backing store; new worker instance
    worker2 = RagQualityWorker(store=store2, secret=SECRET)
    done = await worker2.resume(job.job_id, owner_id=1, answer_fn=counting_answer)
    assert done.status in {
        "passed",
        "qualified",
        "quality_regression",
        "failed_policy",
        "blocked_dependency",
    }
    # Resume must not re-issue answer calls for cached stages
    assert calls["n"] == n_after_crash


@pytest.mark.asyncio
async def test_cancel_queued(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker,
    )
    cancelled = await worker.request_cancel(job.job_id, owner_id=1)
    assert cancelled.status == "cancelled"
    assert cancelled.metrics is None
    assert cancelled.quality_comparable is False


@pytest.mark.asyncio
async def test_cancel_during_run(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=chunker,
    )
    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    # Mark cancel then run — should exit cancelled if checked
    job2 = await worker.store.get(job.job_id)
    job2.cancel_requested = True
    await worker.store.save(job2)
    done = await worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status == "cancelled"
    assert done.metrics is None


@pytest.mark.asyncio
async def test_terminal_status_metrics_null_on_policy_fail(worker: RagQualityWorker):
    snap, cases, g, j, cal, _, chunker = _fixtures()
    # Force quality_regression via high baseline recall
    baseline = {
        "context_recall_at_5_mean": 1.0,
        "answer_relevance_mean": 1.0,
        "cost_usd_total": 0.0000001,
    }
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=chunker,
    )
    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    done = await worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status in {
        "failed_policy",
        "quality_regression",
        "passed",
        "qualified",
    }
    if done.status not in {"passed", "qualified"}:
        assert done.metrics is None
        assert done.quality_comparable is False


@pytest.mark.asyncio
async def test_missing_lineage_invalid_lineage_before_scoring(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline, _ = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=None,
        require_chunker_lineage=True,
    )
    assert job.quality_comparable is False
    assert job.incomparable_reason == "legacy_incomparable"
    lease = await worker.acquire_lease(job.job_id, owner_id=1)
    done = await worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status == "invalid_lineage"
    assert done.metrics is None
    assert done.quality_comparable is False


@pytest.mark.asyncio
async def test_same_snapshot_different_chunker_input_hashes_differ(
    worker: RagQualityWorker,
):
    snap, cases, g, j, cal, baseline, chunker_a = _fixtures()
    chunker_b = _chunker_for(snap, name="semantic-v2", version="2.0.0", size=256)
    job_a = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker_a,
    )
    job_b = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker_b,
    )
    assert job_a.input_hash != job_b.input_hash
    assert job_a.chunker_config_hash != job_b.chunker_config_hash

    lease_a = await worker.acquire_lease(job_a.job_id, owner_id=1)
    done_a = await worker.run(job_a.job_id, lease_id=lease_a, owner_id=1)
    await worker.release_lease(job_a.job_id, lease_a)
    lease_b = await worker.acquire_lease(job_b.job_id, owner_id=1)
    done_b = await worker.run(job_b.job_id, lease_id=lease_b, owner_id=1)
    assert done_a.output_hash != done_b.output_hash
    assert done_a.report_signature != done_b.report_signature
    # Stage cache keys must not collide across chunkers
    keys_a = set((done_a.stage_cache or {}).keys())
    keys_b = set((done_b.stage_cache or {}).keys())
    if keys_a and keys_b:
        assert keys_a.isdisjoint(keys_b)


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed(worker: RagQualityWorker):
    from datetime import timedelta

    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    job = await worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        chunker_lineage=chunker,
    )
    lease1 = await worker.acquire_lease(job.job_id, owner_id=1)
    j0 = await worker.store.get(job.job_id)
    j0.lease_expires_at = j0.lease_expires_at - timedelta(seconds=120)
    await worker.store.save(j0)
    lease2 = await worker.acquire_lease(job.job_id, owner_id=1)
    assert lease2 != lease1


@pytest.mark.asyncio
async def test_db_repository_restart_resume(db_session):
    """Fresh repository/worker instance resumes checkpoint from QualityRun rows."""
    from app.models.user import User
    from app.services.rag_quality_worker import QualityRunRepository

    user = User(username="qrun_db", email="qrun_db@test.com", hashed_password="h")
    db_session.add(user)
    await db_session.flush()

    snap, cases, g, j, cal, baseline, chunker = _fixtures()
    # Snapshot owner_id must match for realism; work_id FK optional
    repo1 = QualityRunRepository(db_session)
    worker1 = RagQualityWorker(store=repo1, secret=SECRET)
    job = await worker1.create_job(
        owner_id=user.id,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        chunker_lineage=chunker,
    )
    calls = {"n": 0}

    def counting_answer(case, retrieved):
        calls["n"] += 1
        return default_stub_answer(case, retrieved)

    lease = await worker1.acquire_lease(job.job_id, owner_id=user.id)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await worker1.run(
            job.job_id,
            lease_id=lease,
            owner_id=user.id,
            answer_fn=counting_answer,
            crash_after_stage="scoring",
        )
    n_crash = calls["n"]
    await worker1.release_lease(job.job_id, lease)

    # New repository + worker over same session (process restart simulation)
    repo2 = QualityRunRepository(db_session)
    worker2 = RagQualityWorker(store=repo2, secret=SECRET)
    loaded = await repo2.get(job.job_id)
    assert loaded is not None
    assert "scoring" in (loaded.checkpoint.get("committed") or [])
    assert loaded.stage_cache or (loaded.checkpoint or {}).get("stage_cache")
    done = await worker2.resume(job.job_id, owner_id=user.id, answer_fn=counting_answer)
    assert done.status in {
        "passed",
        "qualified",
        "quality_regression",
        "failed_policy",
        "blocked_dependency",
    }
    assert calls["n"] == n_crash
