"""
Phase 09 relationship observation authority.

PostgreSQL is the sole fact source for candidates, judgments, accepted
observations, evidence links, protective overrides, and projection audits.
Legacy CharacterRelation is intentionally not referenced here.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
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

# Fiction-only person-to-person edge labels (D-05/D-07). Timeline and identity
# signals (causes/precedes/same_entity) are never relationship graph edges.
RELATIONSHIP_EDGE_TYPES = ("ally", "enemy", "family", "mentor", "romantic")
RELATIONSHIP_TRANSITIONS = ("establish", "change", "end", "uncertain")
OBSERVATION_PIPELINE_STATUSES = (
    "candidate",
    "judged",
    "gated",
    "accepted",
    "needs_human_review",
    "rejected",
)
BUILD_RUN_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "paused_budget",
    "paused_dependency",
)
OVERRIDE_STATUSES = ("active", "superseded", "needs_relink")
PROJECTION_AUDIT_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "disabled",
)
RELATIONSHIP_OVERRIDE_FIELDS = (
    "relation_type",
    "valid_from",
    "valid_to",
    "transition",
)


class RelationshipBuildRun(TimestampMixin, Base):
    """Durable build orchestration for one owner/novel/analysis version."""

    __tablename__ = "relationship_build_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled',"
            "'paused_budget','paused_dependency')",
            name="ck_rel_build_runs_status",
        ),
        Index(
            "idx_rel_build_runs_scope", "owner_id", "novel_id", "analysis_version_id"
        ),
        Index("idx_rel_build_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decoding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    candidate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    judgment_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    accepted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    review_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rejected_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class RelationshipObservationCandidate(TimestampMixin, Base):
    """Deterministic package bound to a Phase 04 accepted source judgment."""

    __tablename__ = "relationship_observation_candidates"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('ally','enemy','family','mentor','romantic')",
            name="ck_rel_obs_candidates_relation_type",
        ),
        CheckConstraint(
            "status IN ('candidate','judged','gated','accepted',"
            "'needs_human_review','rejected')",
            name="ck_rel_obs_candidates_status",
        ),
        CheckConstraint(
            "source_character_id <> target_character_id",
            name="ck_rel_obs_candidates_endpoints_distinct",
        ),
        Index(
            "idx_rel_obs_candidates_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index("idx_rel_obs_candidates_source_judgment", "source_judgment_id"),
        Index("idx_rel_obs_candidates_build_run", "build_run_id"),
        Index(
            "idx_rel_obs_candidates_pair", "source_character_id", "target_character_id"
        ),
        UniqueConstraint(
            "analysis_version_id",
            "package_hash",
            name="uq_rel_obs_candidates_version_package",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    build_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_build_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_judgment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_judgments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_relation_candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False
    )
    target_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    package_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    recall_signals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")


class RelationshipObservationJudgment(TimestampMixin, Base):
    """Bounded LLM judgment plus deterministic gate audit for one candidate."""

    __tablename__ = "relationship_observation_judgments"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('ally','enemy','family','mentor','romantic')",
            name="ck_rel_obs_judgments_relation_type",
        ),
        CheckConstraint(
            "transition IN ('establish','change','end','uncertain')",
            name="ck_rel_obs_judgments_transition",
        ),
        CheckConstraint(
            "status IN ('pending','schema_failed','evidence_failed','threshold_failed',"
            "'conflict_failed','needs_human_review','accepted','rejected')",
            name="ck_rel_obs_judgments_status",
        ),
        CheckConstraint(
            "gate_status IN ('pending','schema_passed','schema_failed','evidence_passed',"
            "'evidence_failed','threshold_passed','threshold_failed','conflict_passed',"
            "'conflict_failed','accepted','needs_human_review','rejected')",
            name="ck_rel_obs_judgments_gate_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rel_obs_judgments_confidence",
        ),
        Index(
            "idx_rel_obs_judgments_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index("idx_rel_obs_judgments_candidate", "candidate_id"),
        Index("idx_rel_obs_judgments_build_run", "build_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    build_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_build_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_observation_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    transition: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    valid_from_evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_to_evidence_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    supporting_evidence_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    structured_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    gate_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending"
    )
    gate_failures: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    call_skipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


class RelationshipObservation(TimestampMixin, Base):
    """Immutable accepted relationship fact with narrative interval and lineage."""

    __tablename__ = "relationship_observations"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('ally','enemy','family','mentor','romantic')",
            name="ck_rel_observations_relation_type",
        ),
        CheckConstraint(
            "transition IN ('establish','change','end')",
            name="ck_rel_observations_transition",
        ),
        CheckConstraint(
            "status = 'accepted'",
            name="ck_rel_observations_status_accepted",
        ),
        CheckConstraint(
            "source_character_id <> target_character_id",
            name="ck_rel_observations_endpoints_distinct",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rel_observations_confidence",
        ),
        CheckConstraint(
            "valid_from_chapter > 0 AND valid_from_narrative_index >= 0",
            name="ck_rel_observations_valid_from",
        ),
        CheckConstraint(
            "valid_to_chapter IS NULL OR ("
            "valid_to_chapter > 0 AND valid_to_narrative_index IS NOT NULL "
            "AND valid_to_narrative_index >= 0 AND ("
            "valid_to_chapter > valid_from_chapter OR ("
            "valid_to_chapter = valid_from_chapter AND "
            "valid_to_narrative_index >= valid_from_narrative_index)))",
            name="ck_rel_observations_interval_order",
        ),
        UniqueConstraint("idempotency_key", name="uq_rel_observations_idempotency"),
        Index(
            "idx_rel_observations_version_interval",
            "analysis_version_id",
            "valid_from_chapter",
            "valid_from_narrative_index",
        ),
        Index(
            "idx_rel_observations_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index(
            "idx_rel_observations_pair_type",
            "analysis_version_id",
            "source_character_id",
            "target_character_id",
            "relation_type",
        ),
        Index("idx_rel_observations_source_judgment", "source_judgment_id"),
        Index("idx_rel_observations_judgment", "judgment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    build_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_build_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_observation_candidates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    judgment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_observation_judgments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_judgment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_judgments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False
    )
    target_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    transition: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="accepted")
    valid_from_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_from_narrative_index: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_to_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_to_narrative_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valid_from_evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_to_evidence_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class RelationshipEvidenceLink(TimestampMixin, Base):
    """Normalized evidence locator attached to an accepted observation."""

    __tablename__ = "relationship_evidence_links"
    __table_args__ = (
        CheckConstraint(
            "source_end > source_start AND source_start >= 0",
            name="ck_rel_evidence_offsets",
        ),
        UniqueConstraint(
            "observation_id",
            "evidence_id",
            name="uq_rel_evidence_observation_ref",
        ),
        Index("idx_rel_evidence_observation", "observation_id"),
        Index(
            "idx_rel_evidence_scope_chapter",
            "owner_id",
            "novel_id",
            "analysis_version_id",
            "chapter_id",
        ),
        Index("idx_rel_evidence_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("relationship_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class CharacterIdentityOverride(TimestampMixin, Base):
    """Append-only character merge correction with supersession and relink state."""

    __tablename__ = "character_identity_overrides"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded','needs_relink')",
            name="ck_char_identity_override_status",
        ),
        Index(
            "idx_char_identity_overrides_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index("idx_char_identity_overrides_signature", "evidence_signature"),
        Index("idx_char_identity_overrides_status", "status"),
        Index("idx_char_identity_overrides_canonical", "canonical_character_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    canonical_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=False
    )
    merged_character_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("character_identity_overrides.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RelationshipOverride(TimestampMixin, Base):
    """Append-only relation type / interval correction overlay."""

    __tablename__ = "relationship_overrides"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','superseded','needs_relink')",
            name="ck_rel_overrides_status",
        ),
        CheckConstraint(
            "field_name IN ('relation_type','valid_from','valid_to','transition')",
            name="ck_rel_overrides_field_name",
        ),
        Index(
            "idx_rel_overrides_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index("idx_rel_overrides_signature", "evidence_signature"),
        Index("idx_rel_overrides_status", "status"),
        Index("idx_rel_overrides_observation", "observation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("relationship_observations.id", ondelete="SET NULL"),
        nullable=True,
    )
    logical_relationship_key: Mapped[str] = mapped_column(String(160), nullable=False)
    field_name: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    author: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("relationship_overrides.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RelationshipProjectionAudit(TimestampMixin, Base):
    """Non-authoritative Neo4j/projection replay checkpoint."""

    __tablename__ = "relationship_projection_audits"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','disabled')",
            name="ck_rel_projection_audits_status",
        ),
        Index(
            "idx_rel_projection_audits_scope",
            "owner_id",
            "novel_id",
            "analysis_version_id",
        ),
        Index("idx_rel_projection_audits_checksum", "manifest_checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target: Mapped[str] = mapped_column(String(40), nullable=False, default="neo4j")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
