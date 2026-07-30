"""
应用设置 ORM 模型（键值存储）

本表存储需要持久化的全局键值设置。

说明:
  - 键值结构（key 主键 + value 字符串），避免每加一个设置就改表结构
  - 复杂结构可序列化为 JSON 字符串存入 value
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AppSetting(TimestampMixin, Base):
    """
    应用设置表：全局键值对存储。

    复杂设置以 JSON 字符串存储在 value 中。
    """

    __tablename__ = "app_settings"

    # 设置键（主键）
    key: Mapped[str] = mapped_column(String(100), primary_key=True)

    # 设置值（字符串；复杂值序列化为 JSON）
    value: Mapped[str] = mapped_column(String(500), nullable=False)
