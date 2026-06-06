"""同人文章节模型"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFictionChapter(TimestampMixin, Base):
    __tablename__ = "fanfiction_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fanfiction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fan_fictions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str | None] = mapped_column(Text)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    model_used: Mapped[str | None] = mapped_column(String(100))
    style_score: Mapped[float | None] = mapped_column(Float)  # 风格一致性评分
    rag_context: Mapped[dict | None] = mapped_column(JSON, default=dict)  # RAG 参考段落
    word_count: Mapped[int] = mapped_column(Integer, default=0)
