"""PostgreSQL source-of-truth contracts for narrative knowledge units.

Phase 04 accepted judgments are immutable inputs.  These tables own snapshots,
unit lineage, index-build state, active pointers, and promotion audit state;
they deliberately contain no raw LLM output and no vector-store behavior.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)


NARRATIVE_UNIT_STAGES = ("draft", "canonical")
NARRATIVE_UNIT_STATUSES = (
    "draft",
    "candidate",
    "active",
    "failed",
    "deprecated",
    "rolled_back",
)
NARRATIVE_LIFECYCLE_STATUSES = ("current", "disputed", "deprecated")
NARRATIVE_BUILD_STATUSES = (
    "draft",
    "candidate",
    "active",
    "failed",
    "deprecated",
    "rolled_back",
)
PROMOTION_JOURNAL_STATUSES = ("prepared", "committed", "failed", "rolled_back")
SOURCE_SNAPSHOT_STATUSES = ("frozen",)
REFRESH_RUN_STATUSES = ("prepared", "candidate", "committed", "failed", "no_change")


# Composite references below prevent a row owned by one user/work from pointing
# at Phase 04 lineage owned by another.  Defining the unique indexes here keeps
# the Phase 04 model file untouched while making the scoped keys available to
# metadata/create_all and the migration.
Index(
    "uq_knowledge_judgments_owner_novel_id",
    KnowledgeRelationJudgment.owner_id,
    KnowledgeRelationJudgment.novel_id,
    KnowledgeRelationJudgment.id,
    unique=True,
)
Index(
    "uq_knowledge_candidates_owner_novel_id",
    KnowledgeRelationCandidate.owner_id,
    KnowledgeRelationCandidate.novel_id,
    KnowledgeRelationCandidate.id,
    unique=True,
)
Index(
    "uq_knowledge_evidence_owner_novel_id",
    KnowledgeEvidenceRef.owner_id,
    KnowledgeEvidenceRef.novel_id,
    KnowledgeEvidenceRef.id,
    unique=True,
)


class NarrativeSourceSnapshot(TimestampMixin, Base):
    """Immutable manifest of accepted Phase 04 judgment inputs."""

    __tablename__ = "narrative_source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "domain_profile",
            "manifest_checksum",
            name="uq_narrative_snapshots_manifest",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_narrative_snapshots_scope",
        ),
        CheckConstraint("item_count > 0", name="ck_narrative_snapshots_nonempty"),
        Index("idx_narrative_snapshots_owner_id", "owner_id"),
        Index("idx_narrative_snapshots_novel_id", "novel_id"),
        Index("idx_narrative_snapshots_status", "status"),
        Index(
            "idx_narrative_snapshots_owner_novel_domain",
            "owner_id",
            "novel_id",
            "domain_profile",
        ),
        Index("idx_narrative_snapshots_manifest", "manifest_checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    ontology_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="frozen")
    source_watermark: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)


class NarrativeSourceSnapshotItem(TimestampMixin, Base):
    """One accepted judgment and its frozen candidate/evidence hashes."""

    __tablename__ = "narrative_source_snapshot_items"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "source_judgment_id",
            name="uq_narrative_snapshot_judgment",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            ondelete="CASCADE",
            name="fk_snapshot_items_snapshot_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_judgment_id"],
            [
                "knowledge_relation_judgments.owner_id",
                "knowledge_relation_judgments.novel_id",
                "knowledge_relation_judgments.id",
            ],
            ondelete="RESTRICT",
            name="fk_snapshot_items_judgment_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_candidate_id"],
            [
                "knowledge_relation_candidates.owner_id",
                "knowledge_relation_candidates.novel_id",
                "knowledge_relation_candidates.id",
            ],
            ondelete="RESTRICT",
            name="fk_snapshot_items_candidate_scope",
        ),
        Index("idx_narrative_snapshot_items_snapshot_id", "snapshot_id"),
        Index("idx_narrative_snapshot_items_judgment_id", "source_judgment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_judgment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    judgment_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    item_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_manifest: Mapped[list] = mapped_column(JSON, nullable=False)


class NarrativeUnit(TimestampMixin, Base):
    """Versioned draft or canonical unit with mandatory judgment/evidence lineage."""

    __tablename__ = "narrative_units"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "id", name="uq_narrative_units_scope"
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "canonical_id",
            "version",
            name="uq_narrative_units_canonical_version",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_units_snapshot_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_judgment_id"],
            [
                "knowledge_relation_judgments.owner_id",
                "knowledge_relation_judgments.novel_id",
                "knowledge_relation_judgments.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_units_judgment_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_candidate_id"],
            [
                "knowledge_relation_candidates.owner_id",
                "knowledge_relation_candidates.novel_id",
                "knowledge_relation_candidates.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_units_candidate_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "primary_evidence_id"],
            [
                "knowledge_evidence_refs.owner_id",
                "knowledge_evidence_refs.novel_id",
                "knowledge_evidence_refs.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_units_primary_evidence_scope",
        ),
        CheckConstraint("version > 0", name="ck_narrative_units_version_positive"),
        CheckConstraint("evidence_count > 0", name="ck_narrative_units_evidence_nonempty"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_narrative_units_confidence"),
        Index("idx_narrative_units_owner_id", "owner_id"),
        Index("idx_narrative_units_novel_id", "novel_id"),
        Index("idx_narrative_units_snapshot_id", "source_snapshot_id"),
        Index("idx_narrative_units_status", "status"),
        Index("idx_narrative_units_lifecycle", "lifecycle_status"),
        Index("idx_narrative_units_canonical_id", "canonical_id"),
        Index(
            "idx_narrative_units_owner_novel_status",
            "owner_id",
            "novel_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_judgment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_evidence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    ontology_profile: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_stage: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    lifecycle_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="current"
    )
    canonical_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    subject_key: Mapped[str] = mapped_column(String(240), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NarrativeUnitEvidenceLink(TimestampMixin, Base):
    """Normalized, owner-scoped evidence lineage for a narrative unit."""

    __tablename__ = "narrative_unit_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "unit_id", "source_evidence_id", name="uq_narrative_unit_evidence"
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "unit_id"],
            ["narrative_units.owner_id", "narrative_units.novel_id", "narrative_units.id"],
            ondelete="CASCADE",
            name="fk_unit_evidence_unit_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_evidence_id"],
            [
                "knowledge_evidence_refs.owner_id",
                "knowledge_evidence_refs.novel_id",
                "knowledge_evidence_refs.id",
            ],
            ondelete="RESTRICT",
            name="fk_unit_evidence_source_scope",
        ),
        Index("idx_narrative_unit_evidence_unit_id", "unit_id"),
        Index("idx_narrative_unit_evidence_source_id", "source_evidence_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_evidence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ref_key: Mapped[str] = mapped_column(String(120), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeIndexBuild(TimestampMixin, Base):
    """Immutable candidate index manifest and its publication lifecycle."""

    __tablename__ = "narrative_index_builds"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "id", name="uq_narrative_builds_scope"
        ),
        UniqueConstraint(
            "owner_id", "novel_id", "build_key", name="uq_narrative_builds_key"
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_builds_snapshot_scope",
        ),
        CheckConstraint("unit_count >= 0", name="ck_narrative_builds_unit_count"),
        Index("idx_narrative_builds_owner_id", "owner_id"),
        Index("idx_narrative_builds_novel_id", "novel_id"),
        Index("idx_narrative_builds_status", "status"),
        Index("idx_narrative_builds_manifest", "manifest_checksum"),
        Index(
            "idx_narrative_builds_owner_novel_status",
            "owner_id",
            "novel_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    build_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")
    collection_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    config_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class NarrativeActivePointer(TimestampMixin, Base):
    """Authoritative active-build pointer; Chroma is only its projection."""

    __tablename__ = "narrative_active_pointers"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "domain_profile",
            name="uq_narrative_active_pointer_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_active_pointer_build_scope",
        ),
        CheckConstraint("pointer_version > 0", name="ck_narrative_pointer_version"),
        Index("idx_narrative_active_pointers_owner_id", "owner_id"),
        Index("idx_narrative_active_pointers_novel_id", "novel_id"),
        Index("idx_narrative_active_pointers_build_id", "build_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    build_id: Mapped[int] = mapped_column(Integer, nullable=False)
    pointer_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class NarrativePromotionJournal(TimestampMixin, Base):
    """Prepare/commit journal for auditable promotion and rollback."""

    __tablename__ = "narrative_promotion_journals"
    __table_args__ = (
        UniqueConstraint("transaction_key", name="uq_narrative_promotion_transaction"),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "candidate_build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_promotion_candidate_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "previous_build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            ondelete="RESTRICT",
            name="fk_narrative_promotion_previous_scope",
        ),
        Index("idx_narrative_promotion_owner_id", "owner_id"),
        Index("idx_narrative_promotion_novel_id", "novel_id"),
        Index("idx_narrative_promotion_status", "status"),
        Index("idx_narrative_promotion_candidate_id", "candidate_build_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    transaction_key: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_build_id: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="prepared")
    candidate_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class NarrativeSourceWatermark(TimestampMixin, Base):
    """Last source snapshot proven active after post-promotion reconcile."""

    __tablename__ = "narrative_source_watermarks"
    __table_args__ = (
        UniqueConstraint("owner_id", "novel_id", "domain_profile", name="uq_narrative_watermark_scope"),
        ForeignKeyConstraint(["owner_id", "novel_id", "snapshot_id"], ["narrative_source_snapshots.owner_id", "narrative_source_snapshots.novel_id", "narrative_source_snapshots.id"], ondelete="RESTRICT", name="fk_narrative_watermark_snapshot_scope"),
        ForeignKeyConstraint(["owner_id", "novel_id", "build_id"], ["narrative_index_builds.owner_id", "narrative_index_builds.novel_id", "narrative_index_builds.id"], ondelete="RESTRICT", name="fk_narrative_watermark_build_scope"),
        Index("idx_narrative_watermark_build_id", "build_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    build_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_watermark: Mapped[str] = mapped_column(String(160), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeRefreshRun(TimestampMixin, Base):
    """Auditable affected-subject refresh run; no-change creates no row."""

    __tablename__ = "narrative_refresh_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_narrative_refresh_run_key"),
        Index("idx_narrative_refresh_owner_novel", "owner_id", "novel_id"),
        Index("idx_narrative_refresh_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    domain_profile: Mapped[str] = mapped_column(String(20), nullable=False)
    run_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="prepared")
    before_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    after_snapshot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_build_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delta_manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    affected_subjects: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    counters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


def _reject_snapshot_mutation(_mapper: object, _connection: object, target: object) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable")


event.listen(NarrativeSourceSnapshot, "before_update", _reject_snapshot_mutation)
event.listen(NarrativeSourceSnapshot, "before_delete", _reject_snapshot_mutation)
event.listen(NarrativeSourceSnapshotItem, "before_update", _reject_snapshot_mutation)
event.listen(NarrativeSourceSnapshotItem, "before_delete", _reject_snapshot_mutation)
