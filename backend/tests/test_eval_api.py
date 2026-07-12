"""Evaluation API authentication and ownership regression tests."""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import EvalDataset, EvalRun
from app.models.novel import Novel
from app.models.user import User


async def _register_and_login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "correct-horse-123",
        },
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "correct-horse-123"},
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


async def _user(db: AsyncSession, username: str) -> User:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one()


async def _create_eval_records(db: AsyncSession, owner: User):
    novel = Novel(title=f"{owner.username} private novel", owner_id=owner.id)
    db.add(novel)
    await db.flush()
    dataset = EvalDataset(
        novel_id=novel.id,
        question="private question",
        question_type="original_text",
        gold_chunks=[1],
        expected_points=["private evidence"],
        must_not_say=[],
    )
    run = EvalRun(
        novel_id=novel.id,
        run_name="private run",
        strategy="bm25",
        config_snapshot={"top_k": 5},
    )
    db.add_all([dataset, run])
    await db.commit()
    return novel, dataset, run


@pytest.mark.asyncio
async def test_eval_endpoints_require_authentication(client: AsyncClient):
    for method, path in [
        ("GET", "/api/eval/datasets"),
        ("GET", "/api/eval/runs"),
        ("GET", "/api/eval/runs/1"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_eval_resources_are_isolated_by_novel_owner(
    client: AsyncClient, db_session: AsyncSession
):
    await _register_and_login(client, "evalowner")
    owner = await _user(db_session, "evalowner")
    novel, dataset, run = await _create_eval_records(db_session, owner)

    assert len((await client.get("/api/eval/datasets")).json()) == 1
    assert len((await client.get("/api/eval/runs")).json()) == 1
    assert (await client.get(f"/api/eval/runs/{run.id}")).status_code == 200

    await _register_and_login(client, "evalother")

    assert (await client.get("/api/eval/datasets")).json() == []
    assert (await client.get("/api/eval/runs")).json() == []
    assert (await client.get(f"/api/eval/runs/{run.id}")).status_code == 404
    response = await client.patch(
        f"/api/eval/datasets/{dataset.id}", json={"status": "confirmed"}
    )
    assert response.status_code == 404
    response = await client.post(
        "/api/eval/runs",
        json={
            "run_name": "forbidden run",
            "strategy": "bm25",
            "novel_id": novel.id,
            "dataset_ids": [dataset.id],
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_eval_api_rejects_unimplemented_strategy(client: AsyncClient):
    await _register_and_login(client, "evalstrategy")
    response = await client.post(
        "/api/eval/runs",
        json={
            "run_name": "unsupported",
            "strategy": "hybrid_worker",
            "novel_id": 1,
            "dataset_ids": [1],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_eval_report_includes_compatibility_fields(
    client: AsyncClient, db_session: AsyncSession
):
    """Legacy GET report must expose job_id/status/quality_comparable/deprecation."""
    await _register_and_login(client, "evalcompat")
    owner = await _user(db_session, "evalcompat")
    _novel, _dataset, run = await _create_eval_records(db_session, owner)

    response = await client.get(f"/api/eval/runs/{run.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "deprecation" in body
    assert body["deprecation"]["deprecated"] is True
    assert body["quality_comparable"] is False
    assert "job_id" in body
    data = body["data"]
    assert data["quality_comparable"] is False
    assert "deprecation" in data


@pytest.mark.asyncio
async def test_quality_run_endpoints_require_auth(client: AsyncClient):
    for method, path in [
        ("GET", "/api/eval/quality/runs"),
        ("GET", "/api/eval/quality/runs/nope"),
        ("POST", "/api/eval/quality/runs/nope/resume"),
        ("POST", "/api/eval/quality/runs/nope/cancel"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_quality_job_cross_owner_and_cancel(client: AsyncClient):
    from app.schemas.eval import CalibrationReport, EvalCase, SourceSnapshot
    from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, load_json
    from app.services.rag_quality import default_healthy, make_baseline_from_metrics, run_quality_evaluation
    from app.services.rag_quality_worker import QualityJobStore, RagQualityWorker, quality_job_store
    from pathlib import Path

    # Isolate global store for this test
    quality_job_store.clear()

    evals = Path(__file__).resolve().parents[1] / "evals"
    data = load_json(evals / "fixtures" / "rag-quality-benchmark.v1.json")
    # Force snapshot owner to match registering user id after login (unknown yet).
    # We'll create job via worker with owner_id then hit API with wrong user.

    await _register_and_login(client, "qowner")
    # owner id is typically 1 in fresh sqlite but may vary — fetch from me if available
    # Use worker directly bound to owner 999 for isolation then login other user
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
        secret=DEFAULT_SIGNING_SECRET,
    )
    baseline = make_baseline_from_metrics(pre["metrics"])

    worker = RagQualityWorker(store=quality_job_store, secret=DEFAULT_SIGNING_SECRET)
    job = worker.create_job(
        owner_id=999999,
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
    )

    # Current user is not owner 999999
    r = await client.get(f"/api/eval/quality/runs/{job.job_id}")
    assert r.status_code == 404

    # Cancel endpoint also owner-scoped
    r = await client.post(f"/api/eval/quality/runs/{job.job_id}/cancel")
    assert r.status_code == 404

    # Owner-side cancel via worker
    cancelled = worker.request_cancel(job.job_id, owner_id=999999)
    assert cancelled.status == "cancelled"
    assert cancelled.metrics is None
    quality_job_store.clear()
