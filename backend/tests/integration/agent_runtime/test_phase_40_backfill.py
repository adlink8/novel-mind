"""Phase 40 — 问答按需分析（chat_backfill）poller 端点 + materializer 集成测试。

覆盖：
- GET /api/agent/queued-runs：gateway token 认证（401/200）、只列 queued chat_backfill
- POST /api/agent/queued-runs/{id}/claim：原子 claim（queued→running）、重复 409、
  返回 internal_token
- materialize_skill_run：非 backfill 跳过、digest 类型跳过、幂等

依赖真实 Postgres（CI PG @ 5433）+ alembic 迁移。
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import app
from app.core.database import get_db
from app.services.agent_runtime.materialize import materialize_skill_run
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
GW_TOKEN = "p40-gateway-token"


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
    aengine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(
        "app.core.security.settings.novelmind_gateway_token", GW_TOKEN
    )

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()
    await aengine.dispose()


async def _seed_owner_novel(factory, *, suffix: str) -> dict[str, Any]:
    from app.schemas.agent_runtime import SkillVersionRegister
    from app.services.agent_runtime.registry import register_skill_version

    async with factory() as session:
        user = (
            await session.execute(
                text(
                    "INSERT INTO users (username, email, hashed_password, is_active, is_superuser, created_at, updated_at) "
                    "VALUES (:u, :e, :p, true, true, now(), now()) RETURNING id"
                ),
                {"u": f"p40_{suffix}", "e": f"p40_{suffix}@example.com", "p": "x"},
            )
        ).scalar()
        novel = (
            await session.execute(
                text(
                    "INSERT INTO novels (owner_id, title, author, status, chapter_count, word_count, created_at, updated_at) "
                    "VALUES (:o, :t, 't', 'ready', 1, 10, now(), now()) RETURNING id"
                ),
                {"o": user, "t": f"p40-book-{suffix}"},
            )
        ).scalar()
        contract = SkillVersionRegister.model_validate(
            {
                "novel_id": novel,
                "name": "detect-key-scenes",
                "version": "1.0.0",
                "allowed_tools": ["get_events"],
                "read_permissions": ["canon"],
                "write_permissions": [],
                "forbidden_spaces": ["canon:original"],
                "budget": {
                    "max_calls": 10,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 1000,
                    "max_cost_usd": "1.00",
                },
                "approval_required_for": [],
                "input_schema": {
                    "type": "object",
                    "properties": {"novel_id": {"type": "integer"}},
                    "required": ["novel_id"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"type": {"const": "scene_candidate"}},
                },
            }
        )
        _, version = await register_skill_version(
            session, owner_id=user, novel_id=novel, contract=contract
        )
        await session.commit()
        return {
            "owner_id": user,
            "novel_id": novel,
            "skill_version_id": version.id,
        }


async def _create_backfill_run(
    factory, *, owner_id: int, novel_id: int, skill_version_id: int
) -> int:
    async with factory() as session:
        r = (
            await session.execute(
                text(
                    "INSERT INTO skill_runs (owner_id, novel_id, skill_version_id, status, "
                    "input, input_hash, frozen_manifest, budget_snapshot, origin, "
                    "backfill_dimension, internal_token_hash, created_at, updated_at) "
                    "VALUES (:o, :n, :sv, 'queued', :inp, :ih, '{}', '{}', 'chat_backfill', "
                    "'raw_text', :th, now(), now()) RETURNING id"
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "sv": skill_version_id,
                    "inp": '{"novel_id":1,"question":"q","dimension":"raw_text","branch":null}',
                    "ih": HEX64,
                    "th": hashlib.sha256(b"token").hexdigest(),
                },
            )
        ).scalar()
        await session.commit()
        return r


class TestQueuedRunsEndpoint:
    async def test_requires_gateway_token(self, api_client):
        client, _ = api_client
        resp = await client.get("/api/agent/queued-runs")
        assert resp.status_code == 401

    async def test_lists_only_backfill_queued(self, api_client, runtime_factory):
        client, factory = api_client
        seed = await _seed_owner_novel(factory, suffix="lq")
        await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        resp = await client.get(
            "/api/agent/queued-runs",
            headers={"Authorization": f"Bearer {GW_TOKEN}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        for item in body["items"]:
            assert item["backfill_dimension"] == "raw_text"
            assert "internal_token" not in item


class TestClaimEndpoint:
    async def test_claim_atomic_and_idempotent(self, api_client, runtime_factory):
        client, factory = api_client
        seed = await _seed_owner_novel(factory, suffix="cl")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        headers = {"Authorization": f"Bearer {GW_TOKEN}"}
        resp = await client.post(
            f"/api/agent/queued-runs/{run_id}/claim", headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == run_id
        assert len(body["internal_token"]) >= 20

        # 第二次 claim → 409（已在途）。
        resp2 = await client.post(
            f"/api/agent/queued-runs/{run_id}/claim", headers=headers
        )
        assert resp2.status_code == 409

        # run 已 running。
        async with factory() as session:
            st = (
                await session.execute(
                    text("SELECT status FROM skill_runs WHERE id=:id"), {"id": run_id}
                )
            ).scalar()
            assert st == "running"

    async def test_claim_unknown_run_409(self, api_client):
        client, _ = api_client
        resp = await client.post(
            "/api/agent/queued-runs/999999999/claim",
            headers={"Authorization": f"Bearer {GW_TOKEN}"},
        )
        assert resp.status_code == 409


class TestMaterializer:
    async def test_materialize_skips_non_backfill(self, runtime_factory):
        factory = runtime_factory
        seed = await _seed_owner_novel(factory, suffix="m1")
        async with factory() as session:
            r = (
                await session.execute(
                    text(
                        "INSERT INTO skill_runs (owner_id, novel_id, skill_version_id, status, "
                        "input, input_hash, frozen_manifest, budget_snapshot, origin, "
                        "internal_token_hash, created_at, updated_at) "
                        "VALUES (:o, :n, 1, 'completed', '{}', :ih, '{}', '{}', 'user_sse', "
                        ":th, now(), now()) RETURNING id"
                    ),
                    {
                        "o": seed["owner_id"],
                        "n": seed["novel_id"],
                        "ih": HEX64,
                        "th": hashlib.sha256(b"t").hexdigest(),
                    },
                )
            ).scalar()
            await session.commit()
            run_id = r
        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "skipped:not_backfill"

    async def test_materialize_digest_type_skipped(self, runtime_factory):
        factory = runtime_factory
        seed = await _seed_owner_novel(factory, suffix="m2")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        async with factory() as session:
            await session.execute(
                text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
                {"id": run_id},
            )
            art = (
                await session.execute(
                    text(
                        "INSERT INTO artifacts (owner_id, novel_id, skill_version_id, run_id, "
                        "schema_version, type, status, model_lineage, source_versions, input_hash, created_at, updated_at) "
                        "VALUES (:o, :n, :sv, :r, 'v1', 'chapter_analysis', 'candidate', '{}', '{}', :ih, now(), now()) "
                        "RETURNING id"
                    ),
                    {"o": seed["owner_id"], "n": seed["novel_id"], "sv": seed["skill_version_id"], "r": run_id, "ih": HEX64},
                )
            ).scalar()
            await session.execute(
                text(
                    "INSERT INTO artifact_revisions (artifact_id, owner_id, novel_id, "
                    "revision_no, content, content_hash, evidence_refs, created_at) "
                    "VALUES (:a, :o, :n, 1, '{\"type\":\"chapter_analysis\"}', :ch, '[]', now())"
                ),
                {
                    "a": art,
                    "o": seed["owner_id"],
                    "n": seed["novel_id"],
                    "ch": HEX64,
                },
            )
            await session.commit()
        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "skipped_digest_not_evidence"
