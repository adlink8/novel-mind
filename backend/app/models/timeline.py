"""
时间线事件 ORM 模型

事件类型 (event_type):
  - plot     : 剧情推进事件（核心情节转折）
  - character: 角色相关事件（角色登场、死亡、转变）
  - world    : 世界观事件（设定揭示、历史背景）
  - conflict : 冲突事件（战斗、争执、对决）

排序机制:
  sort_order 使用浮点数，支持在两个事件之间插入新事件（如 1.0, 1.5, 2.0）。
  AI 提取时按文本位置自动排序，用户可手动调整。
"""

from sqlalchemy import ForeignKey, Integer, String, Text, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TimelineEvent(TimestampMixin, Base):
    """
    时间线事件表：存储小说中的关键事件。

    数据来源:
    - Phase 4 AI 自动提取（LLM 从全文抽取事件 + 因果链）
    - 用户手动添加/编辑

    与 Chapter 的关系: 一个章节可能包含多个事件，一个事件通常属于一个章节。
    """

    __tablename__ = "timeline_events"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键关联
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )  # 关联章节（可选，章节删除时设为 NULL）

    # 事件信息
    event_title: Mapped[str] = mapped_column(String(200), nullable=False)  # 事件标题
    event_description: Mapped[str | None] = mapped_column(Text)  # 事件详细描述
    event_type: Mapped[str] = mapped_column(String(50), default="plot")  # 事件类型
    sort_order: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 排序权重（越小越靠前）

    # 关联信息
    characters_involved: Mapped[str | None] = mapped_column(
        Text
    )  # 涉及角色（JSON 数组字符串）
    location: Mapped[str | None] = mapped_column(String(200))  # 事件发生地点
    time_reference: Mapped[str | None] = mapped_column(
        String(200)
    )  # 时间参考（"第3天", "一年后"）
