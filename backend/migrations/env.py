# =============================================================================
# Alembic 迁移环境配置
# =============================================================================
# 本文件控制 Alembic 如何执行数据库迁移：
#   - offline 模式: 仅生成 SQL 脚本，不连接数据库（适合审核 SQL）
#   - online 模式: 直接连接数据库执行迁移（常用方式）
#
# 关键逻辑:
#   1. 导入 SQLAlchemy 模型的 Base，使 Alembic 能自动检测模型变更
#   2. 从应用配置读取数据库 URL，并将异步驱动(asyncpg)转为同步驱动(psycopg2)
#      因为 Alembic 迁移必须使用同步连接
#   3. 设置 target_metadata 为模型的 metadata，用于 autogenerate 对比
# =============================================================================

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# 导入所有模型，使 Alembic 可以检测到它们
# Base 包含所有通过 SQLAlchemy 定义的表结构
from app.models import Base  # noqa: F401

# Alembic 配置对象，读取 alembic.ini 中的配置项
config = context.config

# 从应用配置中读取数据库 URL，并转换为同步驱动
# 原因: Alembic 不支持异步驱动，需要将 asyncpg 替换为 psycopg2
from app.config import settings
_sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", _sync_url)

# 加载 alembic.ini 中的日志配置，初始化 Python 日志系统
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置目标 metadata，Alembic 通过对比此 metadata 与数据库实际结构
# 来自动生成迁移脚本（alembic revision --autogenerate）
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """以离线模式运行迁移（不连接数据库）。

    仅使用数据库 URL 生成 SQL 脚本，输出到标准输出。
    适用于需要审核 SQL 语句或无法直连数据库的场景。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """以在线模式运行迁移（连接数据库执行）。

    创建数据库引擎并建立连接，在事务中执行迁移。
    使用 NullPool 避免迁移过程中连接池残留。
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # 不使用连接池，迁移完成后立即释放连接
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


# 根据运行模式选择迁移策略
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
