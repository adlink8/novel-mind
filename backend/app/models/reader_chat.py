"""
Phase 10 reader-chat persistence authority.

PostgreSQL is the sole fact source for conversations, messages, immutable
selections/manifests, citations, generation jobs, call attempts and dual-scope
budgets. Chat tables never parent or source timeline/relationship/clue facts.
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

READER_CONVERSATION_STATUSES = ("active", "archived")
READER_MESSAGE_ROLES = ("user", "assistant")
READER_MESSAGE_TYPES = ("text", "image")
READER_JOB_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
    "cancelled",
    "completed",
    "failed",
    "failed_validation",
)
READER_JOB_NONTERMINAL_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
)
# Fiction-only allowlist for persisted context evidence source types (D-12).
# Clue evidence is source-bound and citeable; narrative-memory summaries remain
# prompt-only context and are intentionally not listed here.
READER_EVIDENCE_SOURCE_TYPES = (
    "selection",
    "hierarchy",
    "timeline",
    "knowledge",
    "relationship_observation",
    "clue_evidence",
)
READER_BUDGET_SCOPE_TYPES = ("conversation", "novel")
READER_RESERVATION_STATUSES = ("reserved", "settled", "released", "failed")
READER_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cache_hit",
    "cancelled",
    "outcome_unknown",
)


class ReaderConversation(TimestampMixin, Base):
    """Owner-scoped multi-session conversation for one novel."""

    __tablename__ = "reader_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_reader_conversations_status",
        ),
        CheckConstraint("next_sequence >= 1", name="ck_reader_conversations_next_seq"),
        Index("idx_reader_conversations_scope", "owner_id", "novel_id"),
        Index("idx_reader_conversations_status", "owner_id", "novel_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New chat")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    next_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReaderMessage(TimestampMixin, Base):
    """Ordered conversation message with client idempotency for user sends."""

    __tablename__ = "reader_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant')",
            name="ck_reader_messages_role",
        ),
        CheckConstraint(
            "message_type IN ('text','image')",
            name="ck_reader_messages_type",
        ),
        CheckConstraint("sequence >= 1", name="ck_reader_messages_sequence"),
        UniqueConstraint(
            "conversation_id",
            "sequence",
            name="uq_reader_messages_conversation_sequence",
        ),
        UniqueConstraint(
            "conversation_id",
            "client_message_id",
            name="uq_reader_messages_conversation_client",
        ),
        Index("idx_reader_messages_scope", "owner_id", "novel_id", "conversation_id"),
        Index("idx_reader_messages_replay", "conversation_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text", server_default="text"
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    reply_to_message_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reader_messages.id", ondelete="SET NULL")
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))
    image_generation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("generated_images.id", ondelete="SET NULL")
    )


class GeneratedImage(TimestampMixin, Base):
    """A generated reader image persisted in owner/novel/conversation scope."""

    __tablename__ = "generated_images"
    __table_args__ = (
        Index("idx_generated_images_scope", "owner_id", "novel_id", "conversation_id"),
        Index("idx_generated_images_chapter", "novel_id", "chapter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reader_conversations.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    prompt_cn: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_en: Mapped[str] = mapped_column(Text, nullable=False)
    source_start: Mapped[int | None] = mapped_column(Integer)
    source_end: Mapped[int | None] = mapped_column(Integer)
    selected_text: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)


class ReaderMessageSelection(TimestampMixin, Base):
    """Immutable selection coordinates frozen with a user message (REQ-CHAT-01)."""

    __tablename__ = "reader_message_selections"
    __table_args__ = (
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_reader_selection_offsets",
        ),
        UniqueConstraint("user_message_id", name="uq_reader_selection_user_message"),
        Index("idx_reader_selection_conversation", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    selection_text: Mapped[str] = mapped_column(Text, nullable=False)
    selection_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class ReaderContextManifest(TimestampMixin, Base):
    """Immutable visible-context snapshot for one user message."""

    __tablename__ = "reader_context_manifests"
    __table_args__ = (
        CheckConstraint(
            "cutoff_chapter_number >= 1",
            name="ck_reader_manifest_cutoff",
        ),
        UniqueConstraint("user_message_id", name="uq_reader_manifest_user_message"),
        Index("idx_reader_manifest_conversation", "conversation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    reading_progress_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    full_book: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cutoff_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("analysis_versions.id", ondelete="SET NULL")
    )
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_inputs: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    omitted_evidence_counts: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )


class ReaderContextEvidenceRef(Base):
    """Allowlisted evidence row belonging to one immutable manifest."""

    __tablename__ = "reader_context_evidence_refs"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('selection','hierarchy','timeline','knowledge',"
            "'relationship_observation','clue_evidence')",
            name="ck_reader_evidence_source_type",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_reader_evidence_offsets",
        ),
        UniqueConstraint(
            "manifest_id",
            "evidence_key",
            name="uq_reader_evidence_manifest_key",
        ),
        Index("idx_reader_evidence_manifest", "manifest_id", "sort_order"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    manifest_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_context_manifests.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    chapter_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    version_lineage: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )


class ReaderMessageCitation(Base):
    """Assistant answer-block citation restricted to a persisted evidence ref."""

    __tablename__ = "reader_message_citations"
    __table_args__ = (
        UniqueConstraint(
            "assistant_message_id",
            "block_id",
            "context_evidence_ref_id",
            name="uq_reader_citation_block_ref",
        ),
        Index("idx_reader_citations_assistant", "assistant_message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assistant_message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    block_id: Mapped[str] = mapped_column(String(80), nullable=False)
    context_evidence_ref_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_context_evidence_refs.id", ondelete="RESTRICT"),
        nullable=False,
    )


class ReaderGenerationJob(TimestampMixin, Base):
    """Durable generation job with lease/cancel/retry and frozen lineage."""

    __tablename__ = "reader_generation_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','paused_budget','paused_dependency',"
            "'cancelled','completed','failed','failed_validation')",
            name="ck_reader_gen_jobs_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_reader_gen_jobs_retry"),
        Index("idx_reader_gen_jobs_scope", "owner_id", "novel_id", "conversation_id"),
        Index("idx_reader_gen_jobs_status", "status"),
        Index(
            "uq_reader_gen_job_nonterminal_user_msg",
            "user_message_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','paused_budget','paused_dependency')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    user_message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    status_reason: Mapped[str | None] = mapped_column(String(160))
    lease_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    decoding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    response_hash: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))


class ReaderModelCallAttempt(TimestampMixin, Base):
    """Auditable model call attempt for a generation job."""

    __tablename__ = "reader_model_call_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('started','succeeded','failed','cache_hit',"
            "'cancelled','outcome_unknown')",
            name="ck_reader_call_attempts_status",
        ),
        CheckConstraint("attempt_number >= 1", name="ck_reader_call_attempts_number"),
        UniqueConstraint(
            "generation_job_id",
            "attempt_number",
            name="uq_reader_call_attempt_job_number",
        ),
        Index("idx_reader_call_attempts_job", "generation_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("reader_budget_reservations.id", ondelete="SET NULL"),
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(128))
    cache_source_attempt_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reader_model_call_attempts.id", ondelete="SET NULL")
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    usage: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class ReaderBudgetLedger(TimestampMixin, Base):
    """Conversation-scoped or novel-scoped chat budget ledger."""

    __tablename__ = "reader_budget_ledgers"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('conversation','novel')",
            name="ck_reader_budget_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'conversation' AND conversation_id IS NOT NULL) OR "
            "(scope_type = 'novel' AND conversation_id IS NULL)",
            name="ck_reader_budget_scope_shape",
        ),
        Index(
            "uq_reader_budget_ledger_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("scope_type = 'conversation'"),
            sqlite_where=text("scope_type = 'conversation'"),
        ),
        Index(
            "uq_reader_budget_ledger_novel",
            "owner_id",
            "novel_id",
            unique=True,
            postgresql_where=text("scope_type = 'novel'"),
            sqlite_where=text("scope_type = 'novel'"),
        ),
        Index("idx_reader_budget_ledgers_novel", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("reader_conversations.id", ondelete="CASCADE"),
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


class ReaderBudgetReservation(TimestampMixin, Base):
    """Worst-case budget reservation with settled usage payload."""

    __tablename__ = "reader_budget_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved','settled','released','failed')",
            name="ck_reader_budget_reservation_status",
        ),
        UniqueConstraint(
            "ledger_id",
            "reservation_key",
            name="uq_reader_budget_reservation_key",
        ),
        Index("idx_reader_budget_reservations_ledger", "ledger_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("reader_budget_ledgers.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    settled_usage: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
