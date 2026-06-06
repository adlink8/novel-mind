"""同人文/续写模型"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFiction(TimestampMixin, Base):
    __tablename__ = "fan_fictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)  # 用户输入的续写提示
    content: Mapped[str | None] = mapped_column(Text)  # AI 生成的内容
    style_config: Mapped[str | None] = mapped_column(Text)  # JSON: 风格配置
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft / generating / completed / failed
    model_used: Mapped[str | None] = mapped_column(String(100))
    parent_chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )  # 从哪个章节开始续写
