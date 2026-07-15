"""
Phase 11 clue and foreshadow PostgreSQL authority.

Clue-owned run/version/candidate/lifecycle/evidence/link/override/call/budget
and active pointer tables. Lifecycle history and human overrides are
append-only; current state is always derived by replaying events.

Does not reuse AnalysisRun/AnalysisVersion active_key uniqueness (timeline-
specific). Does not FK into reader_chat fact tables as clue sources.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

CLUE_LIFECYCLE_STATES = (
    "candidate",
    "active",
    "reinforced",
    "paid_off",
    "dismissed",
)
CLUE_EVIDENCE_ROLES = ("cue", "reinforcement", "payoff", "disposition")
CLUE_LINK_TARGET_KINDS = (
    "character",
    "timeline_event",
    "relationship_observation",
)
CLUE_LINK_VALIDATION_STATUSES = (
    "valid",
    "unresolved",
    "source_unavailable",
    "invalid",
)
CLUE_ACTOR_SOURCES = ("machine", "human")
CLUE_OVERRIDE_ACTIONS = ("confirm", "reject", "annotate", "adjust_link")
CLUE_OVERRIDE_STATUSES = ("active", "superseded", "needs_relink")
CLUE_RUN_STATUSES = (
    "pending",
    "running",
    "paused_budget",
    "paused_dependency",
    "cancelled",
    "completed",
    "failed",
)
CLUE_VERSION_STATUSES = ("candidate", "validated", "failed", "superseded")
CLUE_PUBLICATION_STATUSES = ("provisional", "published")
CLUE_RESERVATION_STATUSES = ("reserved", "settled", "released", "failed")
CLUE_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cache_hit",
    "cancelled",
    "outcome_unknown",
)
CLUE_POINTER_ACTIONS = ("promote", "rollback", "compare")


class ClueAnalysisVersion(TimestampMixin, Base):
    """Immutable clue analysis version lineage (clue-owned, not timeline)."""

    __tablename__ = "clue_analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_key",
            name="uq_clue_analysis_versions_scope_key",
        ),
        CheckConstraint(
            "status IN ('candidate','validated','failed','superseded')",
            name="ck_clue_analysis_versions_status",
        ),
        Index("idx_clue_analysis_versions_scope", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    timeline_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("analysis_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    timeline_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decoding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    price_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClueAnalysisRun(TimestampMixin, Base):
    """Durable clue worker run with lease/checkpoint (separate from timeline)."""

    __tablename__ = "clue_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "active_key",
            name="uq_clue_analysis_runs_active",
        ),
        CheckConstraint(
            "status IN ('pending','running','paused_budget','paused_dependency',"
            "'cancelled','completed','failed')",
            name="ck_clue_analysis_runs_status",
        ),
        Index("idx_clue_analysis_runs_scope", "owner_id", "novel_id"),
        Index("idx_clue_analysis_runs_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_key: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default="active"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class MachineClue(TimestampMixin, Base):
    """Immutable machine clue row for one version/logical id."""

    __tablename__ = "machine_clues"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "logical_clue_id",
            name="uq_machine_clues_version_logical",
        ),
        CheckConstraint(
            "publication_status IN ('provisional','published')",
            name="ck_machine_clues_publication_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_machine_clues_confidence",
        ),
        Index("idx_machine_clues_scope", "owner_id", "novel_id", "version_id"),
        Index("idx_machine_clues_logical", "logical_clue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    logical_clue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    package_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False)
    publication_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="provisional"
    )
    # Denormalized for indexes only — never authoritative current state.
    first_cue_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_cue_source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ClueEvidenceRef(TimestampMixin, Base):
    """Evidence locator attached to a machine clue and/or lifecycle event."""

    __tablename__ = "clue_evidence_refs"
    __table_args__ = (
        CheckConstraint(
            "role IN ('cue','reinforcement','payoff','disposition')",
            name="ck_clue_evidence_refs_role",
        ),
        CheckConstraint(
            "source_end > source_start AND source_start >= 0",
            name="ck_clue_evidence_refs_offsets",
        ),
        CheckConstraint(
            "narrative_chapter_number > 0",
            name="ck_clue_evidence_refs_chapter",
        ),
        # At least one parent attachment.
        CheckConstraint(
            "machine_clue_id IS NOT NULL OR lifecycle_event_id IS NOT NULL",
            name="ck_clue_evidence_refs_parent",
        ),
        # Parent-scoped uniqueness so the same cue can attach to both the
        # machine clue and a lifecycle event without colliding.
        UniqueConstraint(
            "machine_clue_id",
            "evidence_identity",
            "role",
            name="uq_clue_evidence_machine_identity",
        ),
        UniqueConstraint(
            "lifecycle_event_id",
            "evidence_identity",
            "role",
            name="uq_clue_evidence_event_identity",
        ),
        Index(
            "idx_clue_evidence_scope_chapter",
            "owner_id",
            "novel_id",
            "version_id",
            "chapter_id",
        ),
        Index("idx_clue_evidence_machine_clue", "machine_clue_id"),
        Index("idx_clue_evidence_lifecycle_event", "lifecycle_event_id"),
        Index("idx_clue_evidence_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    logical_clue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    machine_clue_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machine_clues.id", ondelete="CASCADE"),
        nullable=True,
    )
    lifecycle_event_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_lifecycle_events.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    # Stable identity: evidence_id:chapter_id:start:end:content_hash
    evidence_identity: Mapped[str] = mapped_column(String(220), nullable=False)
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    narrative_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class ClueLifecycleEvent(TimestampMixin, Base):
    """Append-only lifecycle transition for one logical clue in a version."""

    __tablename__ = "clue_lifecycle_events"
    __table_args__ = (
        CheckConstraint(
            "from_status IN ('candidate','active','reinforced','paid_off','dismissed')",
            name="ck_clue_lifecycle_from_status",
        ),
        CheckConstraint(
            "to_status IN ('candidate','active','reinforced','paid_off','dismissed')",
            name="ck_clue_lifecycle_to_status",
        ),
        CheckConstraint(
            "actor_source IN ('machine','human')",
            name="ck_clue_lifecycle_actor_source",
        ),
        # Reject obviously illegal pairs at the DB boundary (full graph in app).
        CheckConstraint(
            "NOT ("
            " (from_status = 'candidate' AND to_status NOT IN ('active','dismissed'))"
            " OR (from_status = 'active' AND to_status NOT IN ('reinforced','dismissed'))"
            " OR (from_status = 'reinforced' AND to_status NOT IN "
            "('reinforced','paid_off','dismissed'))"
            " OR (from_status = 'paid_off')"
            " OR (from_status = 'dismissed')"
            ")",
            name="ck_clue_lifecycle_legal_pair",
        ),
        UniqueConstraint(
            "version_id",
            "logical_clue_id",
            "event_key",
            name="uq_clue_lifecycle_event_identity",
        ),
        Index(
            "idx_clue_lifecycle_scope",
            "owner_id",
            "novel_id",
            "version_id",
            "logical_clue_id",
        ),
        Index("idx_clue_lifecycle_replay", "version_id", "logical_clue_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    logical_clue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    machine_clue_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machine_clues.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    # Sorted list of evidence_identity strings consumed by this event.
    evidence_identities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # For paid_off: store cue/payoff chapter coordinates for ordering checks.
    cue_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cue_source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payoff_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payoff_source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_audit: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ClueLink(TimestampMixin, Base):
    """Evidence-only typed link with exactly one target kind."""

    __tablename__ = "clue_links"
    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('character','timeline_event','relationship_observation')",
            name="ck_clue_links_target_kind",
        ),
        CheckConstraint(
            "validation_status IN ('valid','unresolved','source_unavailable','invalid')",
            name="ck_clue_links_validation_status",
        ),
        # Exactly one target payload column is non-null.
        CheckConstraint(
            "("
            "CASE WHEN character_id IS NOT NULL THEN 1 ELSE 0 END +"
            "CASE WHEN timeline_event_id IS NOT NULL THEN 1 ELSE 0 END +"
            "CASE WHEN relationship_observation_ref IS NOT NULL THEN 1 ELSE 0 END"
            ") = 1",
            name="ck_clue_links_exactly_one_target",
        ),
        CheckConstraint(
            "("
            "(target_kind = 'character' AND character_id IS NOT NULL) OR "
            "(target_kind = 'timeline_event' AND timeline_event_id IS NOT NULL) OR "
            "(target_kind = 'relationship_observation' AND "
            "relationship_observation_ref IS NOT NULL)"
            ")",
            name="ck_clue_links_kind_matches_payload",
        ),
        UniqueConstraint(
            "version_id",
            "logical_clue_id",
            "target_kind",
            "link_identity",
            name="uq_clue_links_identity",
        ),
        Index("idx_clue_links_scope", "owner_id", "novel_id", "version_id"),
        Index("idx_clue_links_logical", "logical_clue_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    logical_clue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    character_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="RESTRICT"), nullable=True
    )
    timeline_event_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("machine_timeline_events.id", ondelete="RESTRICT"),
        nullable=True,
    )
    relationship_observation_ref: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    # Opaque identity string for uniqueness (e.g. char:9 or obs:ref).
    link_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    supporting_evidence_ids: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    validation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unresolved"
    )


class ClueOverride(TimestampMixin, Base):
    """Append-only human override with supersession chain."""

    __tablename__ = "clue_overrides"
    __table_args__ = (
        CheckConstraint(
            "action IN ('confirm','reject','annotate','adjust_link')",
            name="ck_clue_overrides_action",
        ),
        CheckConstraint(
            "status IN ('active','superseded','needs_relink')",
            name="ck_clue_overrides_status",
        ),
        Index("idx_clue_overrides_scope", "owner_id", "novel_id", "logical_clue_id"),
        Index("idx_clue_overrides_supersedes", "supersedes_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    logical_clue_id: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    author: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    supersedes_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("clue_overrides.id", ondelete="SET NULL"), nullable=True
    )
    needs_relink: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    evidence_signature: Mapped[str | None] = mapped_column(String(160), nullable=True)


class ClueBudgetLedger(TimestampMixin, Base):
    """Per-run budget ceilings and settlement counters."""

    __tablename__ = "clue_budget_ledgers"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_clue_budget_ledgers_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_runs.id", ondelete="CASCADE"),
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


class ClueBudgetReservation(TimestampMixin, Base):
    """Fail-closed reservation before a provider call."""

    __tablename__ = "clue_budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "ledger_id",
            "reservation_key",
            name="uq_clue_budget_reservation",
        ),
        CheckConstraint(
            "status IN ('reserved','settled','released','failed')",
            name="ck_clue_budget_reservation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_budget_ledgers.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    settled_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ClueModelCallAttempt(TimestampMixin, Base):
    """Exact cache / call attempt audit for clue stages."""

    __tablename__ = "clue_model_call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage_key",
            "attempt_number",
            name="uq_clue_model_call_attempt",
        ),
        CheckConstraint(
            "status IN ('started','succeeded','failed','cache_hit',"
            "'cancelled','outcome_unknown')",
            name="ck_clue_model_call_attempt_status",
        ),
        Index("idx_clue_model_call_run", "run_id"),
        Index("idx_clue_model_call_cache", "cache_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_budget_reservations.id", ondelete="SET NULL"),
        nullable=True,
    )
    stage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cache_source_attempt_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_model_call_attempts.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ClueActivePointer(TimestampMixin, Base):
    """Single active clue version pointer per owner/novel with CAS revision."""

    __tablename__ = "clue_active_pointers"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            name="uq_clue_active_pointer",
        ),
        Index("idx_clue_active_pointer_version", "version_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class CluePointerJournal(TimestampMixin, Base):
    """Append-only journal of pointer promote/rollback actions."""

    __tablename__ = "clue_pointer_journal"
    __table_args__ = (
        CheckConstraint(
            "action IN ('promote','rollback','compare')",
            name="ck_clue_pointer_journal_action",
        ),
        Index("idx_clue_pointer_journal_scope", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    from_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    to_version_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("clue_analysis_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
