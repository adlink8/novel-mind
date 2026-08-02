"""Explicit creative deviations, isolated from Original Canon."""

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFictionOverride(TimestampMixin, Base):
    __tablename__ = "fanfiction_overrides"
    __table_args__ = (
        UniqueConstraint("fanfiction_id", "override_key", name="uq_fanfiction_override_key"),
        Index("ix_fanfiction_overrides_project", "fanfiction_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fanfiction_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fan_fictions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    override_key: Mapped[str] = mapped_column(String(160), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    original_evidence_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
