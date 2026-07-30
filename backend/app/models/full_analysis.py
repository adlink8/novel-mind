"""Durable orchestration state for the one-click full analysis pipeline."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FullAnalysisRun(TimestampMixin, Base):
    """One owner-scoped, sequential full-analysis orchestration run.

    Domain workers continue to own their own checkpoints and versions. This
    table only mirrors the orchestration cursor so the API can be restarted or
    polled without keeping state in a FastAPI process.
    """

    __tablename__ = "full_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "active_key",
            name="uq_full_analysis_runs_active",
        ),
        CheckConstraint(
            "status IN ('pending','running','paused_dependency','paused_budget',"
            "'cancelled','completed','failed')",
            name="ck_full_analysis_runs_status",
        ),
        Index("idx_full_analysis_runs_scope", "owner_id", "novel_id"),
        Index("idx_full_analysis_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    active_key: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default="active"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    current_stage: Mapped[str] = mapped_column(
        String(64), nullable=False, default="queued"
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

