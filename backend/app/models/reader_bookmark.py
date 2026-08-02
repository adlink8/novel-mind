"""Reader bookmark persistence."""

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ReaderBookmark(TimestampMixin, Base):
    """Owner-scoped chapter bookmark with an optional in-chapter position."""

    __tablename__ = "reader_bookmarks"
    __table_args__ = (
        Index("idx_reader_bookmarks_owner_novel", "owner_id", "novel_id"),
        Index("idx_reader_bookmarks_chapter", "chapter_id"),
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
    position_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
