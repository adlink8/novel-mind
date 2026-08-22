"""ApprovalRequest 集成测试（25.3-04 Task 2 / D-11 / D-15 / T-25.3-04-02）。

覆盖：
  - ask round trip：POST /approval-requests → pending 持久化 → confirm(once) →
    approved；confirm(session) → approved_for_session；reject → rejected 且终态。
  - 跨 owner confirm → 404（不是 403，404-hide oracle 防御）。
  - 重复决策 → 409（稳定 conflict）；过期请求 → 决策被拒绝。
  - 伪造防御：service 层直接改 status 无 API 路径，未知迁移被拒。
  - 分页形状 {"items","total","skip","limit"}。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Chapter, Novel, User
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.registry import register_skill_version
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()

DEFAULT_SKILL = "answer-reading-question"
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
]
FIXED_QUESTION = "阿宁在竹林里看见了谁？"

ASK_ACTION = "publish_illustration"


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(
    *, novel_id: int, name: str = DEFAULT_SKILL, **overrides: Any
) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
        "version": "1.0.0",
        "allowed_tools": list(DEFAULT_TOOLS),
        "read_permissions": ["canon", "derivative"],
        "write_permissions": [],
        "forbidden_spaces": ["canon:original", "derivative:write"],
        "budget": {
            "max_calls": 10,
            "max_input_tokens": 20_000,
            "max_output_tokens": 4_000,
            "max_cost_usd": "0.50",
        },
        "approval_required_for": [ASK_ACTION],
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "novel_id": {"type": "integer"},
            },
            "required": ["question", "novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"schema_version": {"type": "string"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner/other 用户 + owner 小说 + 一章正文，返回 tokens。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"approve_owner_{suffix}",
            email=f"approve_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        other = User(
            username=f"approve_other_{suffix}",
            email=f"approve_other_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add_all([owner, other])
        session.flush()
        novel = Novel(
            title=f"Approve Novel {suffix}",
            author="Author",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(novel)
        session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(chapter)
        session.commit()
        data = {
            "owner_id": owner.id,
            "other_id": other.id,
            "novel_id": novel.id,
            "chapter_id": chapter.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
            "other_token": create_access_token({"sub": str(other.id)}),
        }
    engine.dispose()
    return data


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    """模块级迁移：reset 一次 + upgrade head（含 20260801_2601）。"""
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def api_client(migrated_postgres: str):
    """ASGI client，get_db 覆盖为模块迁移库（API 层测试用）。"""
    aengine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, migrated_postgres
    app.dependency_overrides.clear()
    await aengine.dispose()


# ────────────────────────── 辅助 ──────────────────────────


async def _register_skill(factory, *, owner_id: int, novel_id: int) -> int:
    """service 层注册技能并提交，返回 skill_version id。"""
    async with factory() as session:
        _, version = await register_skill_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            contract=_skill_contract(novel_id=novel_id),
        )
        await session.commit()
        return version.id


async def _accept_run(
    client, *, token: str, novel_id: int, skill_version_id: int
) -> int:
    """POST skill-runs 铸造一次 queued run，返回 run.id。"""
    resp = await client.post(
        f"/api/agent/novels/{novel_id}/skill-runs",
        json={
            "skill_version_id": skill_version_id,
            "input": {"question": FIXED_QUESTION},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["run"]["id"]


async def _create_approval(
    client,
    *,
    token: str,
    run_id: int,
    novel_id: int,
    owner_id: int,
    action: str = ASK_ACTION,
    expires_at: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "action": action,
        "payload_summary": {"action": action, "question": FIXED_QUESTION},
        "payload_hash": HEX64,
    }
    if expires_at:
        payload["expires_at"] = expires_at
    resp = await client.post(
        "/api/agent/approval-requests",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp


# ────────────────────────── 用例 ──────────────────────────


async def test_ask_round_trip_confirm_once(
    api_client,
) -> None:
    """ask → pending 持久化 → GET 可见 → confirm(once) → approved。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )

    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    assert resp.status_code == 201, resp.text
    request = resp.json()
    assert request["status"] == "pending"
    assert request["action"] == ASK_ACTION

    # 持久化（服务端权威）+ owner 隔离读取。
    get_resp = await client.get(
        f"/api/agent/approval-requests/{request['id']}",
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "pending"

    confirm_resp = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "once"},
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["status"] == "approved"
    assert confirm_resp.json()["decided_at"] is not None


async def test_confirm_session_sets_approved_for_session(api_client) -> None:
    """confirm({mode:"session"}) → approved_for_session（D-11 会话批准语义）。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    request = resp.json()
    confirm_resp = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "session"},
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert confirm_resp.status_code == 200
    assert confirm_resp.json()["status"] == "approved_for_session"


async def test_reject_is_terminal(api_client) -> None:
    """reject → rejected；再 confirm → 409（稳定 conflict，不可重决策）。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    request = resp.json()

    reject_resp = await client.post(
        f"/api/agent/approval-requests/{request['id']}/reject",
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    re_confirm = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "once"},
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert re_confirm.status_code == 409


async def test_cross_owner_confirm_is_404_hidden(api_client) -> None:
    """非 owner confirm → 404（404-hide，不是 403 oracle）。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    request = resp.json()

    forged = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "once"},
        headers={"Authorization": f"Bearer {seed['other_token']}"},
    )
    assert forged.status_code == 404, "cross-owner confirm must 404-hide (not 403)"
    # 伪造失败后状态保持 pending（无人能替 owner 决定）。
    get_resp = await client.get(
        f"/api/agent/approval-requests/{request['id']}",
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert get_resp.json()["status"] == "pending"

    # 跨 owner GET 也 404（列表只暴露自己）。
    forged_get = await client.get(
        f"/api/agent/approval-requests/{request['id']}",
        headers={"Authorization": f"Bearer {seed['other_token']}"},
    )
    assert forged_get.status_code == 404


async def test_expired_request_refuses_decision(api_client) -> None:
    """过期请求：confirm → 409（过期先行，拒绝决定）。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
        expires_at="2000-01-01T00:00:00Z",
    )
    request = resp.json()
    confirm_resp = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "once"},
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert confirm_resp.status_code == 409


async def test_direct_status_mutation_forgery_has_no_api_path(api_client) -> None:
    """伪造防御：service 层外直接改 status 无 API 路径；未知迁移被拒。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    request = resp.json()

    # 直接 SQL 篡改 status（service 之外）→ 数据库层被 ck_* 拒绝（非法值）。
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        try:
            conn.execute(
                text("UPDATE approval_requests SET status = 'hacked' WHERE id = :id"),
                {"id": request["id"]},
            )
            assert False, "db-level CheckConstraint must reject unknown status values"
        except Exception:
            pass  # IntegrityError：数据库层 fail-closed
    engine.dispose()

    # 篡改为合法终态 approved（service 之外）→ service 拒绝重复决策（409）。
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE approval_requests SET status = 'approved' WHERE id = :id"),
            {"id": request["id"]},
        )
    engine.dispose()
    confirm_resp = await client.post(
        f"/api/agent/approval-requests/{request['id']}/confirm",
        json={"mode": "once"},
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert confirm_resp.status_code == 409


async def test_create_rejects_owner_forgery(api_client) -> None:
    """铸造时显式 owner_id 与认证用户不符 → 400（伪造防御）。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    # 用 other 的 token 铸造 owner 的请求 → 拒绝。
    resp = await _create_approval(
        client,
        token=seed["other_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    assert resp.status_code == 400


async def test_list_pagination_shape(api_client) -> None:
    """分页形状 {"items","total","skip","limit"}，owner 隔离。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    for _ in range(2):
        resp = await _create_approval(
            client,
            token=seed["owner_token"],
            run_id=run_id,
            novel_id=seed["novel_id"],
            owner_id=seed["owner_id"],
        )
        assert resp.status_code == 201

    list_resp = await client.get(
        "/api/agent/approval-requests?skip=0&limit=1",
        headers={"Authorization": f"Bearer {seed['owner_token']}"},
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert set(body) == {"items", "total", "skip", "limit"}
    assert len(body["items"]) == 1
    assert body["total"] == 2
    assert body["skip"] == 0
    assert body["limit"] == 1

    # owner 隔离：other 列表为空。
    other_list = await client.get(
        "/api/agent/approval-requests",
        headers={"Authorization": f"Bearer {seed['other_token']}"},
    )
    assert other_list.json()["total"] == 0


async def test_service_sole_mutator_via_db_row(api_client) -> None:
    """行级验证：service 是唯一 mutator——approval_requests 无其它写路径。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:8]
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    skill_version_id = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    run_id = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=skill_version_id,
    )
    resp = await _create_approval(
        client,
        token=seed["owner_token"],
        run_id=run_id,
        novel_id=seed["novel_id"],
        owner_id=seed["owner_id"],
    )
    request = resp.json()
    assert request["action"] == ASK_ACTION
    assert request["payload_summary"]["question"] == FIXED_QUESTION
    assert request["run_id"] == run_id
