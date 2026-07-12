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
