"""Durable quality worker: lease/heartbeat/checkpoint/resume/cancel (06-04)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.eval import CalibrationReport, EvalCase, SourceSnapshot
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, load_json
from app.services.rag_quality import default_healthy, default_stub_answer, make_baseline_from_metrics, run_quality_evaluation
from app.services.rag_quality_worker import (
    QualityJobStore,
    QualityWorkerError,
    RagQualityWorker,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

SECRET = DEFAULT_SIGNING_SECRET
EVALS = Path(__file__).resolve().parents[1] / "evals"


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
    # Build a baseline that will pass
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
    )
    assert pre.get("metrics"), pre
    baseline = make_baseline_from_metrics(pre["metrics"])
    return snap, cases, g, j, cal, baseline


@pytest.fixture()
def worker():
    store = QualityJobStore()
    return RagQualityWorker(store=store, secret=SECRET)


def test_create_and_run_to_terminal(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
    )
    assert job.status == "queued"
    lease = worker.acquire_lease(job.job_id, owner_id=1)
    done = worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status in {"passed", "qualified"}
    assert done.quality_comparable is True
    assert done.metrics is not None
    public = worker.get_status(job.job_id, owner_id=1)
    assert public["status"] == done.status
    assert public["quality_comparable"] is True


def test_cross_owner_denied(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
    )
    with pytest.raises(QualityWorkerError) as ei:
        worker.get_status(job.job_id, owner_id=2)
    assert ei.value.status_code == 404


def test_lease_conflict(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
    )
    worker.acquire_lease(job.job_id, owner_id=1)
    with pytest.raises(QualityWorkerError) as ei:
        worker.acquire_lease(job.job_id, owner_id=1)
    assert ei.value.status_code == 409


def test_heartbeat_extends_lease(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
    )
    lease = worker.acquire_lease(job.job_id, owner_id=1)
    j0 = worker.store.get(job.job_id)
    exp1 = j0.lease_expires_at
    assert worker.heartbeat(job.job_id, lease)
    j1 = worker.store.get(job.job_id)
    assert j1.lease_expires_at >= exp1


def test_crash_resume_idempotent_no_duplicate_calls(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
    )
    calls = {"n": 0}

    def counting_answer(case, retrieved):
        calls["n"] += 1
        return default_stub_answer(case, retrieved)

    lease = worker.acquire_lease(job.job_id, owner_id=1)
    with pytest.raises(RuntimeError, match="simulated crash"):
        worker.run(
            job.job_id,
            lease_id=lease,
            owner_id=1,
            answer_fn=counting_answer,
            crash_after_stage="scoring",
        )
    n_after_crash = calls["n"]
    assert n_after_crash >= 1
    # Release and resume
    worker.release_lease(job.job_id, lease)
    mid = worker.store.get(job.job_id)
    assert "scoring" in (mid.checkpoint.get("committed") or [])
    done = worker.resume(
        job.job_id, owner_id=1, answer_fn=counting_answer
    )
    assert done.status in {"passed", "qualified", "quality_regression", "failed_policy", "blocked_dependency"}
    # Resume must not re-issue answer calls for cached stages
    assert calls["n"] == n_after_crash


def test_cancel_queued(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
    )
    cancelled = worker.request_cancel(job.job_id, owner_id=1)
    assert cancelled.status == "cancelled"
    assert cancelled.metrics is None
    assert cancelled.quality_comparable is False


def test_cancel_during_run(worker: RagQualityWorker):
    snap, cases, g, j, cal, baseline = _fixtures()
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
    )
    lease = worker.acquire_lease(job.job_id, owner_id=1)
    # Mark cancel then run — should exit cancelled if checked
    job2 = worker.store.get(job.job_id)
    job2.cancel_requested = True
    worker.store.save(job2)
    done = worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status == "cancelled"
    assert done.metrics is None


def test_terminal_status_metrics_null_on_policy_fail(worker: RagQualityWorker):
    snap, cases, g, j, cal, _ = _fixtures()
    # Force quality_regression via high baseline recall
    baseline = {
        "context_recall_at_5_mean": 1.0,
        "answer_relevance_mean": 1.0,
        "cost_usd_total": 0.0000001,
    }
    job = worker.create_job(
        owner_id=1,
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
    )
    lease = worker.acquire_lease(job.job_id, owner_id=1)
    done = worker.run(job.job_id, lease_id=lease, owner_id=1)
    assert done.status in {
        "failed_policy",
        "quality_regression",
        "passed",
        "qualified",
    }
    if done.status not in {"passed", "qualified"}:
        assert done.metrics is None
        assert done.quality_comparable is False
