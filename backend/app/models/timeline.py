"""时间线事件模型"""

from sqlalchemy import ForeignKey, Integer, String, Text, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TimelineEvent(TimestampMixin, Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )
    event_title: Mapped[str] = mapped_column(String(200), nullable=False)
    event_description: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(String(50), default="plot")  # plot / character / world / conflict
    sort_order: Mapped[float] = mapped_column(Float, default=0.0)  # 排序权重
    characters_involved: Mapped[str | None] = mapped_column(Text)  # JSON list of character names
    location: Mapped[str | None] = mapped_column(String(200))
    time_reference: Mapped[str | None] = mapped_column(String(200))  # "第3天" "一年后" etc.
