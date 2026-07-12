"""
pytest 配置文件

提供测试用的数据库会话和 FastAPI TestClient。
使用 SQLite 内存数据库替代 PostgreSQL，无需外部依赖。

Phase 06-01:
- 测试分类 gate：每个测试必须有 unit|integration|contract|live 主分类
- e2e 可作为跨层组合标记，但不能单独替代主分类
- 按主分类应用 timeout（D-16）
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event

from app.core.database import get_db
from app.models.base import Base
from app.main import app as fastapi_app

# 导入所有模型，确保 Base.metadata 包含所有表定义
import app.models  # noqa: F401

# Primary classification markers (D-04). e2e is a scope combinator only.
PRIMARY_MARKERS = frozenset({"unit", "integration", "contract", "live"})
# Timeout seconds per primary marker (D-16). Most specific wins.
MARKER_TIMEOUTS = {
    "live": 180,
    "integration": 30,
    "contract": 15,
    "unit": 5,
}
# browser timeout (60s) is reserved for Playwright; not applied here.


# 使用 SQLite 内存数据库（测试隔离，不影响开发数据库）
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


# 为 SQLite 启用外键支持（SQLite 默认不强制外键约束）
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _item_marker_names(item: pytest.Item) -> set[str]:
    return {m.name for m in item.iter_markers()}


def _resolve_timeout(marker_names: set[str]) -> int | None:
    """Pick the most specific primary-marker timeout (live > integration > contract > unit)."""
    for name in ("live", "integration", "contract", "unit"):
        if name in marker_names:
            return MARKER_TIMEOUTS[name]
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """
    Fail closed on uncategorized tests and apply D-16 timeouts.

    Every collected test must carry at least one primary marker:
    unit | integration | contract | live.
    The e2e marker alone is not sufficient.
    """
    uncategorized: list[str] = []
    for item in items:
        names = _item_marker_names(item)
        primary = names & PRIMARY_MARKERS
        if not primary:
            uncategorized.append(item.nodeid)
            continue

        timeout_s = _resolve_timeout(names)
        if timeout_s is not None:
            # pytest-timeout: override any broader default for this item.
            item.add_marker(pytest.mark.timeout(timeout_s))

    if uncategorized:
        preview = "\n  ".join(uncategorized[:20])
        more = f"\n  ... and {len(uncategorized) - 20} more" if len(uncategorized) > 20 else ""
        raise pytest.UsageError(
            "Test classification gate failed (D-04): every test must have a primary "
            f"marker from {sorted(PRIMARY_MARKERS)}. Uncategorized tests:\n  {preview}{more}"
        )


@pytest_asyncio.fixture
async def db_session():
    """创建测试用数据库会话（每个测试函数独立的内存数据库）"""
    # PostgreSQL owns the tsvector generated expression. SQLite tests retain
    # the plain Text variant but temporarily omit that dialect-specific DDL.
    search_vector = Base.metadata.tables["text_chunks"].c.search_vector
    postgres_computed = search_vector.computed
    search_vector.computed = None
    async with engine.begin() as conn:
        # 创建所有表（SQLite 会忽略 PostgreSQL 特定的 server_default）
        await conn.run_sync(Base.metadata.create_all)
    search_vector.computed = postgres_computed

    async with TestSessionLocal() as session:
        yield session

    search_vector.computed = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        search_vector.computed = postgres_computed


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """
    创建异步 HTTP 测试客户端。

    覆盖 get_db 依赖注入，使用测试数据库会话。
    """

    async def override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(client: AsyncClient):
    """
    已认证的测试客户端。

    自动注册测试用户并登录，所有请求携带 Bearer Token。
    """
    # 注册测试用户
    register_resp = await client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "testpass123",
        },
    )
    # 如果用户已存在（幂等），继续登录
    if register_resp.status_code not in (201, 400):
        register_resp.raise_for_status()

    # 登录获取 Token
    login_resp = await client.post(
        "/api/auth/login",
        json={"username": "testuser", "password": "testpass123"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]

    # 设置默认请求头
    client.headers["Authorization"] = f"Bearer {token}"
    yield client
    del client.headers["Authorization"]
