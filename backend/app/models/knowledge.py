"""
Knowledge graph construction data contracts.

These models are source-of-truth audit records for deterministic recall,
bounded LLM judgments, evidence refs, and human review. They do not represent
accepted graph facts and do not introduce a graph database projection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.novel import Chapter, Novel
    from app.models.text_chunk import TextChunk
    from app.models.user import User


DOMAIN_PROFILES = ("fiction", "history")

FICTION_ENTITY_TYPES = (
    "character",
    "organization",
    "location",
    "artifact",
    "concept",
)
HISTORY_ENTITY_TYPES = (
    "person",
    "polity",
    "institution",
    "location",
    "source_text",
    "concept",
)

FICTION_EVENT_TYPES = (
    "plot",
    "character_action",
    "conflict",
    "reveal",
    "world_event",
)
HISTORY_EVENT_TYPES = (
    "political",
    "military",
    "diplomatic",
    "social",
    "economic",
    "source_claim",
)

FICTION_RELATION_TYPES = (
    "ally",
    "enemy",
    "family",
    "mentor",
    "romantic",
    "causes",
    "precedes",
    "same_entity",
)
HISTORY_RELATION_TYPES = (
    "allied_with",
    "conflicted_with",
    "ruled",
    "served",
    "succeeded",
    "caused",
    "preceded",
    "same_entity",
)

ENTITY_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_ENTITY_TYPES,
    "history": HISTORY_ENTITY_TYPES,
}
EVENT_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_EVENT_TYPES,
    "history": HISTORY_EVENT_TYPES,
}
RELATION_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_RELATION_TYPES,
    "history": HISTORY_RELATION_TYPES,
}

RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
CANDIDATE_STATUSES = (
    "candidate",
    "proposed",
    "rejected",
    "needs_human_review",
    "accepted",
)
JUDGMENT_STATUSES = (
    "pending",
    "schema_failed",
    "evidence_failed",
    "threshold_failed",
    "conflict_failed",
    "needs_human_review",
    "accepted",
    "rejected",
)
GATE_STATUSES = (
    "pending",
    "schema_passed",
    "schema_failed",
    "evidence_passed",
    "evidence_failed",
    "threshold_passed",
    "threshold_failed",
    "conflict_passed",
    "conflict_failed",
    "accepted",
    "needs_human_review",
    "rejected",
)
EVIDENCE_SOURCE_TYPES = ("text_chunk", "chapter", "accepted_relation")
REVIEW_STATUSES = ("open", "in_review", "resolved", "rejected")


class KnowledgeExtractionRun(TimestampMixin, Base):
    """Persisted batch/CLI run for candidate and judgment generation."""

    __tablename__ = "knowledge_extraction_runs"
    __table_args__ = (
        Index("idx_knowledge_runs_owner_id", "owner_id"),
        Index("idx_knowledge_runs_novel_id", "novel_id"),
        Index("idx_knowledge_runs_status", "status"),
        Index("idx_knowledge_runs_domain_profile", "domain_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fiction"
    )
    ontology_profile: Mapped[str] = mapped_column(
        String(100), nullable=False, default="fiction.v1"
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    judgment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    total_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metrics_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    evidence_refs: Mapped[list["KnowledgeEvidenceRef"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    entity_candidates: Mapped[list["KnowledgeEntityCandidate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    event_candidates: Mapped[list["KnowledgeEventCandidate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    relation_candidates: Mapped[list["KnowledgeRelationCandidate"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    judgments: Mapped[list["KnowledgeRelationJudgment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    review_items: Mapped[list["KnowledgeReviewQueue"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )


class KnowledgeEvidenceRef(TimestampMixin, Base):
    """Normalized evidence locator referenced by candidates and judgments."""

    __tablename__ = "knowledge_evidence_refs"
    __table_args__ = (
        Index("idx_knowledge_evidence_owner_id", "owner_id"),
        Index("idx_knowledge_evidence_novel_id", "novel_id"),
        Index("idx_knowledge_evidence_run_id", "run_id"),
        Index("idx_knowledge_evidence_ref_key", "run_id", "ref_key", unique=True),
        Index("idx_knowledge_evidence_source_type", "source_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    ref_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    text_chunk_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    accepted_relation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_locator: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(back_populates="evidence_refs")
    text_chunk: Mapped["TextChunk | None"] = relationship()
    chapter: Mapped["Chapter | None"] = relationship()


class KnowledgeEntityCandidate(TimestampMixin, Base):
    """Entity candidate created by deterministic recall/package scripts."""

    __tablename__ = "knowledge_entity_candidates"
    __table_args__ = (
        Index("idx_knowledge_entities_owner_id", "owner_id"),
        Index("idx_knowledge_entities_novel_id", "novel_id"),
        Index("idx_knowledge_entities_run_id", "run_id"),
        Index("idx_knowledge_entities_status", "status"),
        Index("idx_knowledge_entities_domain_profile", "domain_profile"),
        Index("idx_knowledge_entities_entity_type", "entity_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    domain_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fiction"
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(
        back_populates="entity_candidates"
    )


class KnowledgeEventCandidate(TimestampMixin, Base):
    """Event candidate created before any accepted timeline or graph projection."""

    __tablename__ = "knowledge_event_candidates"
    __table_args__ = (
        Index("idx_knowledge_events_owner_id", "owner_id"),
        Index("idx_knowledge_events_novel_id", "novel_id"),
        Index("idx_knowledge_events_run_id", "run_id"),
        Index("idx_knowledge_events_status", "status"),
        Index("idx_knowledge_events_domain_profile", "domain_profile"),
        Index("idx_knowledge_events_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fiction"
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    time_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    location_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    participant_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(
        back_populates="event_candidates"
    )


class KnowledgeRelationCandidate(TimestampMixin, Base):
    """Potential relation from deterministic recall signals, not graph truth."""

    __tablename__ = "knowledge_relation_candidates"
    __table_args__ = (
        Index("idx_knowledge_rel_candidates_owner_id", "owner_id"),
        Index("idx_knowledge_rel_candidates_novel_id", "novel_id"),
        Index("idx_knowledge_rel_candidates_run_id", "run_id"),
        Index("idx_knowledge_rel_candidates_status", "status"),
        Index("idx_knowledge_rel_candidates_relation_type", "relation_type"),
        Index("idx_knowledge_rel_candidates_domain_profile", "domain_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain_profile: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fiction"
    )
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recall_signals: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    package_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="candidate")

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(
        back_populates="relation_candidates"
    )
    judgments: Mapped[list["KnowledgeRelationJudgment"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )
    review_items: Mapped[list["KnowledgeReviewQueue"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", lazy="selectin"
    )


class KnowledgeRelationJudgment(TimestampMixin, Base):
    """Structured LLM judgment plus deterministic gate status."""

    __tablename__ = "knowledge_relation_judgments"
    __table_args__ = (
        Index("idx_knowledge_judgments_owner_id", "owner_id"),
        Index("idx_knowledge_judgments_novel_id", "novel_id"),
        Index("idx_knowledge_judgments_run_id", "run_id"),
        Index("idx_knowledge_judgments_candidate_id", "relation_candidate_id"),
        Index("idx_knowledge_judgments_status", "status"),
        Index("idx_knowledge_judgments_gate_status", "gate_status"),
        Index("idx_knowledge_judgments_relation_type", "relation_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_candidate_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_flags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    raw_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    structured_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    gate_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="pending"
    )
    gate_failures: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    needs_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(back_populates="judgments")
    candidate: Mapped["KnowledgeRelationCandidate"] = relationship(
        back_populates="judgments"
    )
    review_items: Mapped[list["KnowledgeReviewQueue"]] = relationship(
        back_populates="judgment", cascade="all, delete-orphan", lazy="selectin"
    )


class KnowledgeReviewQueue(TimestampMixin, Base):
    """Human review queue for weak, conflicting, or evidence-failed outputs."""

    __tablename__ = "knowledge_review_queue"
    __table_args__ = (
        Index("idx_knowledge_review_owner_id", "owner_id"),
        Index("idx_knowledge_review_novel_id", "novel_id"),
        Index("idx_knowledge_review_run_id", "run_id"),
        Index("idx_knowledge_review_status", "status"),
        Index("idx_knowledge_review_priority", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("knowledge_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_candidate_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_candidates.id", ondelete="CASCADE"),
        nullable=True,
    )
    judgment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("knowledge_relation_judgments.id", ondelete="CASCADE"),
        nullable=True,
    )
    review_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    resolution: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reviewer_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(120), nullable=True)

    owner: Mapped["User"] = relationship()
    novel: Mapped["Novel"] = relationship()
    run: Mapped["KnowledgeExtractionRun"] = relationship(back_populates="review_items")
    candidate: Mapped["KnowledgeRelationCandidate | None"] = relationship(
        back_populates="review_items"
    )
    judgment: Mapped["KnowledgeRelationJudgment | None"] = relationship(
        back_populates="review_items"
    )
