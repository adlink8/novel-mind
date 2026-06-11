"""
SQLAlchemy ORM 基类与公共 Mixin

本模块定义:
1. Base: 所有 ORM 模型的声明式基类（DeclarativeBase）
2. TimestampMixin: 自动管理 created_at / updated_at 时间戳

所有模型都应继承 Base，并可选择性混入 TimestampMixin。
Alembic 迁移通过 Base.metadata 发现所有表定义。
"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    所有 ORM 模型的声明式基类。

    功能:
    - 提供 declarative base，让 SQLAlchemy 能发现所有模型
    - 通过 Base.metadata.create_all() 可快速建表（开发用）
    - Alembic 迁移通过 Base.metadata 自动生成 DDL
    """

    pass


class TimestampMixin:
    """
    时间戳 Mixin：为模型自动添加 created_at / updated_at 字段。

    行为:
    - created_at: 记录创建时间，数据库自动填充（server_default）
    - updated_at: 记录最后更新时间，每次 UPDATE 时自动刷新（onupdate）

    使用方式:
      class MyModel(TimestampMixin, Base):
          __tablename__ = "my_table"
          id: Mapped[int] = mapped_column(primary_key=True)
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),  # 带时区的日期时间
        server_default=func.now(),  # 数据库层面的默认值（不依赖 Python）
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),  # 每次 UPDATE 时自动刷新
        nullable=False,
    )
