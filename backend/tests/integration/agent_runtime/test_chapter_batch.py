"""Public chapter-batch and queued-run claim contracts."""

from __future__ import annotations

import hashlib
import json
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Chapter, Novel, User
from app.models.agent_runtime import SkillRun
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.registry import register_skill_version
from app.services.agent_runtime.chapter_batch import (
    continue_chapter_batch_after_finalize,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

GATEWAY_TOKEN = "chapter-batch-gateway-token"


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(migrated_postgres: str, monkeypatch):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "app.core.security.settings.novelmind_gateway_token", GATEWAY_TOKEN
    )

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed(factory, *, count: int = 100) -> dict[str, int | str]:
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        owner = User(
            username=f"chapter-batch-{suffix}",
            email=f"chapter-batch-{suffix}@example.com",
            hashed_password=hash_password("test-password"),
            is_active=True,
            is_superuser=False,
        )
        session.add(owner)
        await session.flush()
        novel = Novel(
            owner_id=owner.id,
            title=f"chapter-batch-{suffix}",
            status="ready",
            chapter_count=count,
        )
        session.add(novel)
        await session.flush()
        chapters = [
            Chapter(
                novel_id=novel.id,
                chapter_number=number,
                title=f"第{number}章",
                content=f"第{number}章正文",
                word_count=6,
            )
            for number in range(1, count + 1)
        ]
        session.add_all(chapters)
        await session.flush()
        novel.reading_progress = {"chapter_id": chapters[-1].id}
        contract = SkillVersionRegister.model_validate(
            {
                "novel_id": novel.id,
                "name": "analyze-chapter",
                "version": "1.0.0",
                "prompt": "Analyze one chapter using only supplied evidence.",
                "allowed_tools": ["get_chapter"],
                "read_permissions": ["novel:read"],
                "write_permissions": [],
                "forbidden_spaces": ["canon:original", "derivative:write"],
                "budget": {
                    "max_calls": 40,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 1000,
                },
                "approval_required_for": [],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        )
        _, version = await register_skill_version(
            session, owner_id=owner.id, novel_id=novel.id, contract=contract
        )
        await session.commit()
        return {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "skill_version_id": version.id,
            "token": create_access_token({"sub": str(owner.id)}),
            "chapter_ids": [chapter.id for chapter in chapters],
        }


@pytest.mark.asyncio
async def test_batch_100_is_idempotent_and_refills_only_after_terminal_runs(
    api_client, runtime_factory
):
    client, factory = api_client
    seed = await _seed(factory)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    payload = {"chapter_start": 1, "chapter_end": 100, "concurrency_window": 4}

    first = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json=payload,
        headers=headers,
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["total"] == 100
    assert body["queued"] == 4
    assert body["pending"] == 96
    assert len(body["created_run_ids"]) == 4
    batch_id = body["batch_id"]

    second = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json=payload,
        headers=headers,
    )
    assert second.status_code == 202, second.text
    assert second.json()["batch_id"] == batch_id
    assert second.json()["created_run_ids"] == []

    async with factory() as session:
        runs = list((await session.scalars(select(SkillRun))).all())
        assert len(runs) == 4
        assert {run.origin for run in runs} == {"chapter_batch"}
        assert all(run.input.get("question") for run in runs)
        assert all(
            run.input.get("execution_prompt") == run.input.get("question")
            for run in runs
        )
        for run in runs:
            run.status = "completed"
        await session.commit()

    third = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json=payload,
        headers=headers,
    )
    assert third.status_code == 202, third.text
    assert third.json()["completed"] == 4
    assert third.json()["queued"] == 4
    assert third.json()["pending"] == 92

    status = await client.get(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches/{batch_id}",
        headers=headers,
    )
    assert status.status_code == 200, status.text
    assert status.json()["total"] == 100
    assert len(status.json()["chapters"]) == 100


@pytest.mark.asyncio
async def test_batch_chapter_ids_resolve_real_rows_and_cutoff(
    api_client, runtime_factory
):
    client, factory = api_client
    seed = await _seed(factory, count=10)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    ids = [seed["chapter_ids"][0], seed["chapter_ids"][4], seed["chapter_ids"][9]]
    response = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json={"chapter_ids": ids, "concurrency_window": 2},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["total"] == 3
    assert body["queued"] == 2
    assert body["pending"] == 1
    assert [item["chapter_number"] for item in body["chapters"]] == [1, 5, 10]


@pytest.mark.parametrize("chapter_count", [100, 400])
async def test_success_continuously_refills_one_slot_without_unbounded_enqueue(
    api_client, runtime_factory, chapter_count: int
):
    client, factory = api_client
    seed = await _seed(factory, count=chapter_count)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    created = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json={
            "chapter_start": 1,
            "chapter_end": chapter_count,
            "concurrency_window": 4,
        },
        headers=headers,
    )
    assert created.status_code == 202, created.text
    assert (created.json()["queued"], created.json()["pending"]) == (
        4,
        chapter_count - 4,
    )

    # First prove the strict one-finished -> one-created steady state.
    completed_count = 0
    for _ in range(8):
        async with factory.begin() as session:
            run = await session.scalar(
                select(SkillRun)
                .where(
                    SkillRun.novel_id == seed["novel_id"],
                    SkillRun.origin == "chapter_batch",
                    SkillRun.status == "queued",
                )
                .order_by(SkillRun.id)
                .limit(1)
            )
            assert run is not None
            run.status = "completed"
            run_id = run.id

        outcome = await continue_chapter_batch_after_finalize(factory, run_id)
        assert outcome["continuation"] == "refilled"
        batch = outcome["batch"]
        completed_count += 1
        assert len(batch["created_run_ids"]) == 1
        assert batch["queued"] + batch["running"] <= 4
        assert batch["total"] == chapter_count

    # Then emulate a whole window finishing close together. One background
    # continuation may refill all freed slots; later tasks are idempotent.
    while completed_count < chapter_count:
        async with factory.begin() as session:
            queued = list(
                (
                    await session.scalars(
                        select(SkillRun)
                        .where(
                            SkillRun.novel_id == seed["novel_id"],
                            SkillRun.origin == "chapter_batch",
                            SkillRun.status == "queued",
                        )
                        .order_by(SkillRun.id)
                    )
                ).all()
            )
            assert queued
            for run in queued:
                run.status = "completed"
            run_id = queued[0].id

        completed_count += len(queued)
        outcome = await continue_chapter_batch_after_finalize(factory, run_id)
        batch = outcome["batch"]
        assert len(batch["created_run_ids"]) <= 4
        assert batch["queued"] + batch["running"] <= 4
        assert batch["total"] == chapter_count

    assert batch["status"] == "completed"
    assert (batch["completed"], batch["pending"], batch["queued"]) == (
        chapter_count,
        0,
        0,
    )


async def test_failed_and_cancelled_runs_wait_for_explicit_resume(
    api_client, runtime_factory
):
    client, factory = api_client
    seed = await _seed(factory, count=10)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    payload = {"chapter_start": 1, "chapter_end": 10, "concurrency_window": 3}
    created = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 202, created.text
    batch_id = created.json()["batch_id"]

    async with factory.begin() as session:
        rows = list(
            (
                await session.scalars(
                    select(SkillRun)
                    .where(
                        SkillRun.novel_id == seed["novel_id"],
                        SkillRun.origin == "chapter_batch",
                    )
                    .order_by(SkillRun.id)
                )
            ).all()
        )
        rows[0].status = "failed"
        rows[0].error_code = "upstream_error"
        failed_run_id = rows[0].id
        failed_manifest = dict(rows[0].frozen_manifest or {})
        rows[1].status = "cancelled"
        rows[1].error_code = "user_cancel"
        cancelled_run_id = rows[1].id
        rows[2].status = "completed"
        completed_run_id = rows[2].id

    continued = await continue_chapter_batch_after_finalize(factory, completed_run_id)
    assert continued["batch"]["failed"] == 1
    assert continued["batch"]["cancelled"] == 1
    assert continued["batch"]["queued"] == 3

    repeated = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json=payload,
        headers=headers,
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["failed"] == 1
    assert repeated.json()["cancelled"] == 1

    while True:
        async with factory.begin() as session:
            queued = await session.scalar(
                select(SkillRun)
                .where(
                    SkillRun.novel_id == seed["novel_id"],
                    SkillRun.origin == "chapter_batch",
                    SkillRun.status == "queued",
                )
                .order_by(SkillRun.id)
                .limit(1)
            )
            if queued is None:
                break
            queued.status = "completed"
            queued_id = queued.id
        await continue_chapter_batch_after_finalize(factory, queued_id)

    resumed = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches/{batch_id}/resume",
        headers=headers,
    )
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["failed"] == 0
    assert resumed.json()["cancelled"] == 0
    assert resumed.json()["queued"] == 2
    assert resumed.json()["completed"] == 8
    assert len(resumed.json()["created_run_ids"]) == 2

    async with factory() as session:
        old_failed = await session.get(SkillRun, failed_run_id)
        old_cancelled = await session.get(SkillRun, cancelled_run_id)
        assert old_failed is not None and old_failed.status == "failed"
        assert old_cancelled is not None and old_cancelled.status == "cancelled"
        assert old_failed.frozen_manifest == failed_manifest
        retry_rows = list(
            (
                await session.scalars(
                    select(SkillRun).where(
                        SkillRun.id.in_(resumed.json()["created_run_ids"])
                    )
                )
            ).all()
        )
        assert len(retry_rows) == 2
        assert all(row.status == "queued" for row in retry_rows)
        assert all("tool_runs" not in (row.frozen_manifest or {}) for row in retry_rows)


@pytest.mark.asyncio
async def test_running_chapter_batch_cancel_is_immediately_terminal(
    api_client, runtime_factory
):
    client, _factory = api_client
    seed = await _seed(runtime_factory, count=3)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    created = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/chapter-batches",
        json={"chapter_start": 1, "chapter_end": 3, "concurrency_window": 1},
        headers=headers,
    )
    run_id = created.json()["created_run_ids"][0]

    claimed = await client.post(
        f"/api/agent/queued-runs/{run_id}/claim",
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
    )
    assert claimed.status_code == 200, claimed.text

    cancelled = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs/{run_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["status_reason"] == "cancelled_during_execution"


@pytest.mark.asyncio
async def test_queued_list_and_claim_support_chapter_batch_and_reader_chat(
    api_client, runtime_factory
):
    client, factory = api_client
    seed = await _seed(factory, count=2)
    async with factory() as session:
        rows = []
        for origin in ("chapter_batch", "reader_chat"):
            payload = {"novel_id": seed["novel_id"], "question": f"分析{origin}"}
            rows.append(
                SkillRun(
                    owner_id=seed["owner_id"],
                    novel_id=seed["novel_id"],
                    skill_version_id=seed["skill_version_id"],
                    status="queued",
                    input=payload,
                    input_hash=hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode()
                    ).hexdigest(),
                    frozen_manifest={},
                    budget_snapshot={},
                    internal_token_hash="a" * 64,
                    origin=origin,
                )
            )
        session.add_all(rows)
        await session.commit()
        ids = [row.id for row in rows]

    gateway = {"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    listed = await client.get("/api/agent/queued-runs", headers=gateway)
    assert listed.status_code == 200, listed.text
    listed_by_id = {item["run_id"]: item for item in listed.json()["items"]}
    assert {listed_by_id[run_id]["origin"] for run_id in ids} == {
        "chapter_batch",
        "reader_chat",
    }

    for run_id in ids:
        claimed = await client.post(
            f"/api/agent/queued-runs/{run_id}/claim", headers=gateway
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["origin"] in {"chapter_batch", "reader_chat"}
        assert claimed.json()["input"]["question"]
