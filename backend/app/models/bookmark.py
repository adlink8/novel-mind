"""用户在阅读器中保存的原文选区书签。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Bookmark(TimestampMixin, Base):
    """Owner-scoped bookmark with code-point offsets into immutable chapter text."""

    __tablename__ = "bookmarks"
    __table_args__ = (
        CheckConstraint("source_start >= 0", name="ck_bookmarks_source_start"),
        CheckConstraint(
            "source_end > source_start", name="ck_bookmarks_source_range"
        ),
        Index("ix_bookmarks_owner_novel", "owner_id", "novel_id"),
        Index("ix_bookmarks_chapter", "chapter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_text: Mapped[str] = mapped_column(Text, nullable=False)
    selection_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
