"""User-authored Markdown revision history for creative projects."""

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFictionRevision(TimestampMixin, Base):
    """Immutable snapshot written on each project/chapter save."""

    __tablename__ = "fanfiction_revisions"
    __table_args__ = (
        Index("ix_fanfiction_revisions_project_created", "fanfiction_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fanfiction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fan_fictions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("fanfiction_chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    editor_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
