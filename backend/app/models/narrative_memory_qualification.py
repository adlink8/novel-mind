"""Phase 17 append-only single-book qualification audit authority.

Candidate-only. No active/current pointer, promotion, or consumer selector.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base, TimestampMixin

QUAL_VERDICTS = ("qualified_candidate", "blocked")
QUAL_RUN_STATUSES = ("running", "completed", "blocked")
QUAL_STRATEGIES = ("hierarchical_candidate", "leaf_raw_baseline")


class NarrativeMemoryQualificationRun(TimestampMixin, Base):
    """One explicit-version qualification run with frozen lineage hashes."""

    __tablename__ = "narrative_memory_qualification_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "fixture_checksum",
            "policy_checksum",
            name="uq_nm_qual_runs_identity",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_nm_qual_runs_owner_novel_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_nm_qual_runs_version",
        ),
        CheckConstraint(
            "status IN ('running','completed','blocked')",
            name="ck_nm_qual_runs_status",
        ),
        CheckConstraint("length(fixture_checksum) = 64", name="ck_nm_qual_runs_fx"),
        CheckConstraint("length(policy_checksum) = 64", name="ck_nm_qual_runs_pol"),
        CheckConstraint(
            "length(source_snapshot_hash) = 64", name="ck_nm_qual_runs_snap"
        ),
        CheckConstraint("length(hierarchy_checksum) = 64", name="ck_nm_qual_runs_hier"),
        CheckConstraint(
            "length(candidate_manifest_checksum) = 64", name="ck_nm_qual_runs_man"
        ),
        CheckConstraint(
            "length(pointer_before_digest) = 64", name="ck_nm_qual_runs_ptr"
        ),
        Index("idx_nm_qual_runs_scope", "owner_id", "novel_id", "version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    fixture_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    judge_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pricing_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    pointer_before_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NarrativeMemoryQualificationCaseResult(TimestampMixin, Base):
    """Per-case per-strategy retrieval/answer/call artifact checksums."""

    __tablename__ = "narrative_memory_qualification_case_results"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "case_key",
            "strategy",
            name="uq_nm_qual_cases_identity",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "run_id",
            "id",
            name="uq_nm_qual_cases_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "run_id"],
            [
                "narrative_memory_qualification_runs.owner_id",
                "narrative_memory_qualification_runs.novel_id",
                "narrative_memory_qualification_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_nm_qual_cases_run",
        ),
        CheckConstraint(
            "strategy IN ('hierarchical_candidate','leaf_raw_baseline')",
            name="ck_nm_qual_cases_strategy",
        ),
        CheckConstraint("length(artifact_checksum) = 64", name="ck_nm_qual_cases_art"),
        Index("idx_nm_qual_cases_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    case_key: Mapped[str] = mapped_column(String(180), nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    bucket: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    sanitized_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    artifact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class NarrativeMemoryQualificationReport(TimestampMixin, Base):
    """Sealed final report with two-value verdict only."""

    __tablename__ = "narrative_memory_qualification_reports"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_nm_qual_reports_run"),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "run_id",
            "id",
            name="uq_nm_qual_reports_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "run_id"],
            [
                "narrative_memory_qualification_runs.owner_id",
                "narrative_memory_qualification_runs.novel_id",
                "narrative_memory_qualification_runs.id",
            ],
            ondelete="CASCADE",
            name="fk_nm_qual_reports_run",
        ),
        CheckConstraint(
            "verdict IN ('qualified_candidate','blocked')",
            name="ck_nm_qual_reports_verdict",
        ),
        CheckConstraint(
            "qualification_kind = 'single_book_candidate'",
            name="ck_nm_qual_reports_kind",
        ),
        CheckConstraint(
            "length(metric_payload_checksum) = 64", name="ck_nm_qual_reports_metric"
        ),
        CheckConstraint(
            "length(verifier_checksum) = 64", name="ck_nm_qual_reports_ver"
        ),
        CheckConstraint(
            "length(pointer_after_digest) = 64", name="ck_nm_qual_reports_ptr"
        ),
        CheckConstraint("length(output_digest) = 64", name="ck_nm_qual_reports_out"),
        Index("idx_nm_qual_reports_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    qualification_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="single_book_candidate"
    )
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    metric_payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    pointer_after_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    command_payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    output_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    report_body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
