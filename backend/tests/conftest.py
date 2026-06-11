"""
pytest 配置文件

提供测试用的数据库会话和 FastAPI TestClient。
使用 SQLite 内存数据库替代 PostgreSQL，无需外部依赖。

使用方式:
  cd backend
  .venv/Scripts/python.exe -m pip install -q aiosqlite pytest pytest-asyncio httpx
  .venv/Scripts/python.exe -m pytest -v
"""

import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event

from app.core.database import get_db
from app.models.base import Base
from app.main import app as fastapi_app

# 导入所有模型，确保 Base.metadata 包含所有表定义
import app.models  # noqa: F401

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


@pytest_asyncio.fixture
async def db_session():
    """创建测试用数据库会话（每个测试函数独立的内存数据库）"""
    async with engine.begin() as conn:
        # 创建所有表（SQLite 会忽略 PostgreSQL 特定的 server_default）
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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
