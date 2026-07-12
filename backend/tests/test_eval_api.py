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
        ("POST", "/api/eval/quality/baseline/prepare"),
        ("POST", "/api/eval/quality/baseline/commit"),
        ("GET", "/api/eval/quality/baseline/active"),
        ("POST", "/api/eval/quality/reports/cross-chunker"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_quality_job_cross_owner_and_cancel(client: AsyncClient, db_session):
    from app.models.eval import QualityRun
    from app.models.user import User
    from pathlib import Path

    # Persist a QualityRun owned by a different user than the logged-in caller.
    other = User(
        username="qother_owner",
        email="qother_owner@test.com",
        hashed_password="hash",
    )
    db_session.add(other)
    await db_session.flush()
    job = QualityRun(
        job_id="qjob-cross-owner-api",
        owner_id=other.id,
        status="queued",
        payload={},
        checkpoint={"stage": "queued", "committed": []},
        stage_cache={},
        quality_comparable=False,
        incomparable_reason="legacy_incomparable",
    )
    db_session.add(job)
    await db_session.commit()

    await _register_and_login(client, "qowner")

    r = await client.get(f"/api/eval/quality/runs/{job.job_id}")
    assert r.status_code == 404

    r = await client.post(f"/api/eval/quality/runs/{job.job_id}/cancel")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_baseline_prepare_commit_and_report_api(
    client: AsyncClient, db_session: AsyncSession
):
    from app.models.eval import QualityRun
    from app.services.rag_fixture import stable_hash
    from app.services.rag_quality import recompute_chunker_config_hash

    await _register_and_login(client, "bl_api_owner")
    owner = await _user(db_session, "bl_api_owner")

    cfg = {"size": 128}
    cfg_hash = recompute_chunker_config_hash(cfg)
    snap = "c" * 64
    man = stable_hash({"m": 1})
    metrics = {
        "context_recall_at_5_mean": 0.9,
        "answer_relevance_mean": 0.8,
        "cost_usd_total": 0.02,
        "answer_faithfulness_95lb": 0.75,
        "context_precision_mean": 0.7,
    }
    run = QualityRun(
        job_id="api-bl-job-1",
        owner_id=owner.id,
        status="passed",
        payload={},
        checkpoint={},
        stage_cache={},
        metrics=metrics,
        input_hash="1" * 64,
        output_hash="2" * 64,
        report_signature="s" * 64,
        chunker_name="api-chunker",
        chunker_version="1.0.0",
        chunker_config_hash=cfg_hash,
        chunk_manifest_hash=man,
        source_snapshot_hash=snap,
        quality_comparable=True,
    )
    db_session.add(run)
    # second chunker same snap for report
    run2 = QualityRun(
        job_id="api-bl-job-2",
        owner_id=owner.id,
        status="passed",
        payload={},
        checkpoint={},
        stage_cache={},
        metrics=metrics,
        input_hash="3" * 64,
        output_hash="4" * 64,
        report_signature="t" * 64,
        chunker_name="api-chunker-b",
        chunker_version="2.0.0",
        chunker_config_hash=recompute_chunker_config_hash({"size": 200}),
        chunk_manifest_hash=stable_hash({"m": 2}),
        source_snapshot_hash=snap,
        quality_comparable=True,
    )
    db_session.add(run2)
    await db_session.commit()

    r = await client.post(
        "/api/eval/quality/baseline/prepare", json={"job_id": "api-bl-job-1"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "prepared"
    cand = body["candidate"]
    assert cand["prepare_token"]

    r = await client.post(
        "/api/eval/quality/baseline/commit",
        json={"candidate_id": cand["id"], "prepare_token": cand["prepare_token"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "committed"
    assert r.json()["active"]["candidate_id"] == cand["id"]

    r = await client.get("/api/eval/quality/baseline/active")
    assert r.status_code == 200
    assert r.json()["active"]["candidate_id"] == cand["id"]

    r = await client.post(
        "/api/eval/quality/reports/cross-chunker",
        json={"source_snapshot_hash": snap},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert len(report["series"]) == 2
    assert {s["chunker_name"] for s in report["series"]} == {
        "api-chunker",
        "api-chunker-b",
    }
