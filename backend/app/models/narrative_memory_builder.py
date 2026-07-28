"""Phase 14 durable narrative-memory builder control plane.

Builder-owned run/stage/call/budget/report tables only. Candidate content
authority remains in narrative_memory.py. No active pointer, promotion,
or current-version resolver exists here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base, TimestampMixin

BUILD_RUN_STATUSES = (
    "pending",
    "running",
    "partial",
    "paused_budget",
    "paused_dependency",
    "cancelled",
    "completed",
    "failed",
)
BUILD_STAGE_KINDS = (
    "chapter_state",
    "arc_volume_plan",
    "arc_volume_aggregate",
    "global_aggregate",
    "manifest_validation",
)
BUILD_STAGE_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "blocked_dependency",
    "cancelled",
    "paused_budget",
    "paused_dependency",
)
BUILD_RESERVATION_STATUSES = ("reserved", "settled", "released", "failed")
BUILD_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cache_hit",
    "cancelled",
    "outcome_unknown",
    "budget_rejected",
)
BUILD_REPORT_OUTCOMES = (
    "completed_candidate",
    "partial",
    "paused",
    "cancelled",
    "failed",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class NarrativeMemoryBuildRun(TimestampMixin, Base):
    """One durable builder run bound to an explicit candidate version."""

    __tablename__ = "narrative_memory_build_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            name="uq_memory_build_runs_version",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "id",
            name="uq_memory_build_runs_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_build_runs_version_scope",
        ),
        CheckConstraint(
            f"status IN ({_quoted(BUILD_RUN_STATUSES)})",
            name="ck_memory_build_runs_status",
        ),
        CheckConstraint(
            "length(eligibility_report_checksum) = 64",
            name="ck_memory_build_runs_eligibility_checksum",
        ),
        Index("idx_memory_build_runs_scope", "owner_id", "novel_id"),
        Index("idx_memory_build_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    eligibility_report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    run_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    boundary_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    boundary_plan_checksum: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )


class NarrativeMemoryBuildStage(TimestampMixin, Base):
    """Stage/checkpoint row for one builder unit of work."""

    __tablename__ = "narrative_memory_build_stages"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage_key",
            name="uq_memory_build_stages_key",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "run_id"],
            [
                "narrative_memory_build_runs.owner_id",
                "narrative_memory_build_runs.novel_id",
                "narrative_memory_build_runs.version_id",
                "narrative_memory_build_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_memory_build_stages_run_scope",
        ),
        CheckConstraint(
            f"stage_kind IN ({_quoted(BUILD_STAGE_KINDS)})",
            name="ck_memory_build_stages_kind",
        ),
        CheckConstraint(
            f"status IN ({_quoted(BUILD_STAGE_STATUSES)})",
            name="ck_memory_build_stages_status",
        ),
        Index("idx_memory_build_stages_run", "run_id"),
        Index("idx_memory_build_stages_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_key: Mapped[str] = mapped_column(String(180), nullable=False)
    stage_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    chapter_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dependency_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    package_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class NarrativeMemoryBuildBudgetLedger(TimestampMixin, Base):
    """Per-run budget ceilings and counters."""

    __tablename__ = "narrative_memory_build_budget_ledgers"
    __table_args__ = (UniqueConstraint("run_id", name="uq_memory_build_budget_run"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("narrative_memory_build_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserved_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default="0"
    )
    settled_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    settled_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    settled_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    settled_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default="0"
    )


class NarrativeMemoryBuildBudgetReservation(TimestampMixin, Base):
    """Row-locked reservation before every model call."""

    __tablename__ = "narrative_memory_build_budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "ledger_id",
            "reservation_key",
            name="uq_memory_build_budget_reservation",
        ),
        CheckConstraint(
            f"status IN ({_quoted(BUILD_RESERVATION_STATUSES)})",
            name="ck_memory_build_budget_reservation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("narrative_memory_build_budget_ledgers.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    settled_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class NarrativeMemoryBuildModelCallAttempt(TimestampMixin, Base):
    """Immutable call/cache-hit audit row."""

    __tablename__ = "narrative_memory_build_model_call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage_key",
            "attempt_number",
            name="uq_memory_build_model_call_attempt",
        ),
        CheckConstraint(
            f"status IN ({_quoted(BUILD_ATTEMPT_STATUSES)})",
            name="ck_memory_build_model_call_attempt_status",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_memory_build_model_call_request_hash",
        ),
        Index("idx_memory_build_model_call_run", "run_id"),
        Index("idx_memory_build_model_call_cache", "cache_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("narrative_memory_build_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "narrative_memory_build_budget_reservations.id", ondelete="SET NULL"
        ),
        nullable=True,
    )
    stage_key: Mapped[str] = mapped_column(String(180), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cache_source_attempt_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "narrative_memory_build_model_call_attempts.id", ondelete="SET NULL"
        ),
        nullable=True,
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deployment_lineage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    validated_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class NarrativeMemoryBuildReport(TimestampMixin, Base):
    """Append-only structural execution report (not Phase 17 quality)."""

    __tablename__ = "narrative_memory_build_reports"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "report_checksum",
            name="uq_memory_build_reports_checksum",
        ),
        CheckConstraint(
            f"outcome IN ({_quoted(BUILD_REPORT_OUTCOMES)})",
            name="ck_memory_build_reports_outcome",
        ),
        CheckConstraint(
            "length(report_checksum) = 64",
            name="ck_memory_build_reports_checksum",
        ),
        Index("idx_memory_build_reports_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("narrative_memory_build_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    stage_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dependency_closure: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    call_totals: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_statuses: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    worker_artifact_checksum: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    database_manifest_checksum: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at_immutable: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
