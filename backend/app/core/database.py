"""
数据库引擎与会话管理

本模块负责:
1. 创建 SQLAlchemy async 引擎（连接池 + 自动重连）
2. 提供会话工厂（每次请求一个独立会话）
3. FastAPI 依赖注入函数 get_db（自动 commit/rollback/close）
4. 开发环境的快速建表工具 init_db

数据流:
  FastAPI 路由 → Depends(get_db) → AsyncSession → ORM 模型操作 → PostgreSQL
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# 创建异步数据库引擎
# - echo=settings.debug: 调试模式下打印所有 SQL 语句
# - pool_pre_ping=True: 每次取连接前先 ping，自动剔除断开的连接
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,        # 开发环境打印 SQL，生产环境关闭
    pool_pre_ping=True,         # 连接健康检查，防止使用已断开的连接
)

# 异步会话工厂
# - class_=AsyncSession: 使用异步会话
# - expire_on_commit=False: commit 后不自动过期对象属性，避免惰性加载异常
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类"""
    pass


async def get_db() -> AsyncSession:
    """
    FastAPI 依赖注入: 获取数据库会话。

    使用方式:
        @router.get("/xxx")
        async def handler(db: AsyncSession = Depends(get_db)):
            ...

    行为:
    - 正常结束时自动 commit
    - 异常时自动 rollback
    - 无论成功与否都 close 会话（归还连接池）
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()       # 正常退出时提交事务
        except Exception:
            await session.rollback()     # 异常时回滚，保证数据一致性
            raise
        finally:
            await session.close()        # 归还连接到池中


async def init_db():
    """
    初始化数据库：根据 ORM 模型创建所有表。

    ⚠️ 仅用于开发环境快速验证，生产环境请使用 Alembic 迁移:
        alembic upgrade head
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
