"""Phase 16 dependency-aware rebuild authority (candidate-only).

Immutable rebuild plans/items and append-only reuse reports. No active pointer,
promotion, current-version resolver, provider call, or budget table lives here.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

REBUILD_DECISIONS = (
    "dirty",
    "carried",
    "stale_blocked",
    "not_applicable",
)
REBUILD_ASSET_KINDS = (
    "source_chapter",
    "evidence_leaf",
    "chapter_state",
    "story_arc",
    "volume",
    "global_story",
    "boundary_plan",
    "optional_source",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class NarrativeMemoryRebuildPlan(TimestampMixin, Base):
    """One immutable rebuild plan bound to explicit parent and target versions."""

    __tablename__ = "narrative_memory_rebuild_plans"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "parent_version_id",
            "target_version_id",
            name="uq_memory_rebuild_plans_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "parent_version_id",
            "target_version_id",
            "id",
            name="uq_memory_rebuild_plans_id_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_memory_rebuild_plans_owner_novel_id",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "plan_checksum",
            name="uq_memory_rebuild_plans_checksum",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "parent_version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_rebuild_plans_parent",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "target_version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_rebuild_plans_target",
        ),
        CheckConstraint(
            "parent_version_id <> target_version_id",
            name="ck_memory_rebuild_plans_distinct_versions",
        ),
        CheckConstraint(
            "length(old_source_snapshot_hash) = 64",
            name="ck_memory_rebuild_plans_old_snapshot",
        ),
        CheckConstraint(
            "length(new_source_snapshot_hash) = 64",
            name="ck_memory_rebuild_plans_new_snapshot",
        ),
        CheckConstraint(
            "length(old_hierarchy_checksum) = 64",
            name="ck_memory_rebuild_plans_old_hierarchy",
        ),
        CheckConstraint(
            "length(new_hierarchy_checksum) = 64",
            name="ck_memory_rebuild_plans_new_hierarchy",
        ),
        CheckConstraint(
            "length(boundary_plan_checksum) = 64",
            name="ck_memory_rebuild_plans_boundary",
        ),
        CheckConstraint(
            "length(oracle_policy_checksum) = 64",
            name="ck_memory_rebuild_plans_oracle_policy",
        ),
        CheckConstraint(
            "length(compatibility_policy_checksum) = 64",
            name="ck_memory_rebuild_plans_compat_policy",
        ),
        CheckConstraint(
            "length(graph_checksum) = 64",
            name="ck_memory_rebuild_plans_graph",
        ),
        CheckConstraint(
            "length(plan_checksum) = 64",
            name="ck_memory_rebuild_plans_plan",
        ),
        Index("idx_memory_rebuild_plans_scope", "owner_id", "novel_id"),
        Index(
            "idx_memory_rebuild_plans_versions",
            "parent_version_id",
            "target_version_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="RESTRICT"), nullable=False
    )
    parent_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    old_source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    new_source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    old_hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    new_hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    old_hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    new_hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    boundary_plan: Mapped[dict] = mapped_column(JSONB, nullable=False)
    boundary_plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    oracle_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    oracle_policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    compatibility_policy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    change_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    eligibility_report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeMemoryRebuildItem(TimestampMixin, Base):
    """Normalized per-asset rebuild decision under one plan."""

    __tablename__ = "narrative_memory_rebuild_items"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "asset_key",
            name="uq_memory_rebuild_items_key",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "plan_id",
            "id",
            name="uq_memory_rebuild_items_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "plan_id"],
            [
                "narrative_memory_rebuild_plans.owner_id",
                "narrative_memory_rebuild_plans.novel_id",
                "narrative_memory_rebuild_plans.id",
            ],
            ondelete="CASCADE",
            name="fk_memory_rebuild_items_plan_scope",
        ),
        CheckConstraint(
            f"asset_kind IN ({_quoted(REBUILD_ASSET_KINDS)})",
            name="ck_memory_rebuild_items_kind",
        ),
        CheckConstraint(
            f"decision IN ({_quoted(REBUILD_DECISIONS)})",
            name="ck_memory_rebuild_items_decision",
        ),
        CheckConstraint(
            "chapter_start IS NULL OR "
            "(chapter_start > 0 AND chapter_end IS NOT NULL "
            "AND chapter_end >= chapter_start)",
            name="ck_memory_rebuild_items_range",
        ),
        CheckConstraint(
            "old_content_checksum IS NULL OR length(old_content_checksum) = 64",
            name="ck_memory_rebuild_items_old_cs",
        ),
        CheckConstraint(
            "new_content_checksum IS NULL OR length(new_content_checksum) = 64",
            name="ck_memory_rebuild_items_new_cs",
        ),
        CheckConstraint(
            "dependency_checksum IS NULL OR length(dependency_checksum) = 64",
            name="ck_memory_rebuild_items_dep_cs",
        ),
        Index("idx_memory_rebuild_items_plan", "plan_id"),
        Index("idx_memory_rebuild_items_decision", "plan_id", "decision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_key: Mapped[str] = mapped_column(String(180), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    chapter_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    direct_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    propagated_reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    predecessor_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    old_content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_content_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dependency_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class NarrativeMemoryReuseReport(Base):
    """Append-only reuse economics report for one rebuild plan."""

    __tablename__ = "narrative_memory_reuse_reports"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "plan_id",
            "report_checksum",
            name="uq_memory_reuse_reports_checksum",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "plan_id"],
            [
                "narrative_memory_rebuild_plans.owner_id",
                "narrative_memory_rebuild_plans.novel_id",
                "narrative_memory_rebuild_plans.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_reuse_reports_plan_scope",
        ),
        CheckConstraint(
            "length(report_checksum) = 64",
            name="ck_memory_reuse_reports_checksum",
        ),
        CheckConstraint(
            "length(plan_checksum) = 64",
            name="ck_memory_reuse_reports_plan_cs",
        ),
        Index("idx_memory_reuse_reports_plan", "plan_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rebuilt_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    carried_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    stale_counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dirty_ranges: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    observed_actual: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    full_rebuild_upper_bound: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    avoided_upper_bound: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cache_reuse: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    carry_reuse: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    formula_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at_immutable: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
