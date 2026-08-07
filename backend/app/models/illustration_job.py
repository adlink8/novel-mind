"""Durable illustration generation job and approval contract (Phase 33-01, REQ-VIS-04).

D-33-01..D-33-03: each image generation request is an idempotent durable job
keyed by owner/novel/SceneSpec/prompt/model/config lineage; provider outputs are
immutable candidate asset revisions (``AssetRevision`` in ``illustration.py``)
that stay ``candidate`` until explicit human approval. This module is the
control plane (the ``reader_chat.py`` / ``reader_chat/worker.py`` analog):

- ``illustration_jobs``: durable generation job with explicit queued/running/
  paused/succeeded/failed/cancelled/outcome_unknown states, a lease for worker
  claim, a bounded retry counter and a unique nonterminal idempotency key.
  A provider timeout is never relabeled a success and never becomes an empty
  asset (D-33-01/D-33-02).
- ``illustration_attempts``: one auditable provider call per attempt with
  request/response hashes, provider request id, usage, cost, latency, status
  and error code. ``outcome_unknown`` keeps the provider may-have-created an
  asset case explicit and reconcilable by request id (D-33-02).
- ``illustration_budget_ledgers`` / ``illustration_budget_reservations``:
  novel-scoped worst-case budget reserve/settle with a price snapshot;
  unknown usage/cost stays explicit and budget exhaustion fails closed.
- ``illustration_review_events``: append-only human/machine approval actions
  (approve/reject/supersede/needs_relink) with an idempotent event key; the
  AssetRevision row's approval state is only a projection (D-33-03).

Design conventions (following ``reader_chat.py`` / ``prompt_revision.py``):
- One nonterminal job per idempotency key via a partial unique index; a
  duplicate request replays the existing job instead of charging twice.
- Content rows are append-only — SQLAlchemy events reject UPDATE/DELETE so no
  silent in-place approval or charge mutation is possible.
- No active-pointer / promotion / published-asset column: approval only moves a
  candidate to ``proposal_ready`` for Phase 34; nothing here becomes reader
  visible (D-33-03, forbidden publish path).
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
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

ILLUSTRATION_SCHEMA_VERSION = "illustration.v1"
# D-33-01: explicit terminal and paused/failure states; "unknown" is an explicit
# outcome_unknown state (provider may have created an asset), never a success.
ILLUSTRATION_JOB_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
)
ILLUSTRATION_JOB_NONTERMINAL_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
)
ILLUSTRATION_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
)
ILLUSTRATION_RESERVATION_STATUSES = ("reserved", "settled", "released", "failed")
# Approval vocabulary (mirrored by schemas/illustration.py). Phase 33 ends at
# proposal_ready; publish moves to Phase 34.
ILLUSTRATION_APPROVAL_STATES = (
    "candidate",
    "proposal_ready",
    "rejected",
    "superseded",
)
ILLUSTRATION_REVIEW_ACTIONS = ("approve", "reject", "supersede", "needs_relink")
ILLUSTRATION_ACTOR_SOURCES = ("human", "machine")
# D-33-03: generated/reference outputs carry a rights/provenance status.
ILLUSTRATION_RIGHTS_STATUSES = ("unreviewed", "cleared", "pending", "denied")


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class IllustrationJob(TimestampMixin, Base):
    """Durable generation job with lease/cancel/retry and frozen lineage (D-33-01)."""

    __tablename__ = "illustration_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_illustration_jobs_scope",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(ILLUSTRATION_JOB_STATUSES)})",
            name="ck_illustration_jobs_status",
        ),
        CheckConstraint("retry_count >= 0", name="ck_illustration_jobs_retry"),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_illustration_jobs_idempotency_key",
        ),
        CheckConstraint(
            "length(scene_spec_hash) = 64",
            name="ck_illustration_jobs_scene_spec_hash",
        ),
        CheckConstraint(
            "length(prompt_revision_hash) = 64",
            name="ck_illustration_jobs_prompt_hash",
        ),
        CheckConstraint(
            "length(visual_bible_revision_hash) = 64",
            name="ck_illustration_jobs_vb_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_illustration_jobs_snapshot_hash",
        ),
        CheckConstraint(
            "length(config_hash) = 64",
            name="ck_illustration_jobs_config_hash",
        ),
        CheckConstraint("cutoff_chapter >= 1", name="ck_illustration_jobs_cutoff"),
        Index("idx_illustration_jobs_scope", "owner_id", "novel_id", "status"),
        Index("idx_illustration_jobs_lease", "lease_expires_at"),
        # D-33-01 idempotency: one nonterminal job per lineage key. A duplicate
        # submission replays the existing job instead of charging twice.
        Index(
            "uq_illustration_jobs_nonterminal_key",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','paused_budget','paused_dependency')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    job_key: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    status_reason: Mapped[str | None] = mapped_column(String(160))
    error_code: Mapped[str | None] = mapped_column(String(80))
    lease_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Frozen lineage (D-33-01): the job is keyed by the same lineage that must
    # replay on every attempt and asset.
    scene_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_revision_id: Mapped[int | None] = mapped_column(Integer)
    prompt_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visual_bible_revision_id: Mapped[int | None] = mapped_column(Integer)
    visual_bible_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # D-33-02: the price snapshot that every reservation for this job is
    # settled against; never trusted from the client after creation.
    price_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=ILLUSTRATION_SCHEMA_VERSION
    )


class IllustrationAttempt(TimestampMixin, Base):
    """Auditable provider call attempt for one job (D-33-02)."""

    __tablename__ = "illustration_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_values(ILLUSTRATION_ATTEMPT_STATUSES)})",
            name="ck_illustration_attempts_status",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_illustration_attempts_number",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_illustration_attempts_request_hash",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_illustration_attempts_job_number",
        ),
        Index("idx_illustration_attempts_job", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("illustration_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("illustration_budget_reservations.id", ondelete="SET NULL"),
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class IllustrationBudgetLedger(TimestampMixin, Base):
    """Novel-scoped illustration budget ledger (owner/novel, D-33-02)."""

    __tablename__ = "illustration_budget_ledgers"
    __table_args__ = (
        CheckConstraint("max_calls >= 0", name="ck_illustration_budget_max_calls"),
        CheckConstraint("max_cost_usd >= 0", name="ck_illustration_budget_max_cost"),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            name="uq_illustration_budget_ledger_scope",
        ),
        Index("idx_illustration_budget_ledgers_scope", "owner_id", "novel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserved_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default="0"
    )
    settled_calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    settled_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0, server_default="0"
    )


class IllustrationBudgetReservation(TimestampMixin, Base):
    """Worst-case budget reservation with price snapshot and settled payload."""

    __tablename__ = "illustration_budget_reservations"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_values(ILLUSTRATION_RESERVATION_STATUSES)})",
            name="ck_illustration_budget_reservations_status",
        ),
        CheckConstraint("calls >= 0", name="ck_illustration_budget_reservations_calls"),
        CheckConstraint(
            "cost_usd >= 0", name="ck_illustration_budget_reservations_cost"
        ),
        UniqueConstraint(
            "ledger_id",
            "reservation_key",
            name="uq_illustration_budget_reservations_key",
        ),
        Index("idx_illustration_budget_reservations_ledger", "ledger_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ledger_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("illustration_budget_ledgers.id", ondelete="CASCADE"),
        nullable=False,
    )
    reservation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="reserved", server_default="reserved"
    )
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    price_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # D-33-02: settled usage/cost is explicit; unknown usage/cost is recorded as
    # {"usage_unknown": true} and never silently zeroed.
    settled_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class IllustrationReviewEvent(TimestampMixin, Base):
    """Append-only human/machine approval action (D-33-03).

    One event per explicit review action on one AssetRevision candidate. The
    asset row's approval state is a projection; the event rows are the
    append-only audit history. Approval moves a candidate to ``proposal_ready``
    only; publish is deferred to Phase 34.
    """

    __tablename__ = "illustration_review_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "asset_revision_id",
            "event_key",
            name="uq_illustration_review_events_key",
        ),
        Index(
            "idx_illustration_review_events_asset",
            "owner_id",
            "novel_id",
            "asset_revision_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "asset_revision_id"],
            [
                "asset_revisions.owner_id",
                "asset_revisions.novel_id",
                "asset_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_illustration_review_events_asset_scope",
        ),
        CheckConstraint(
            f"action IN ({_sql_values(ILLUSTRATION_REVIEW_ACTIONS)})",
            name="ck_illustration_review_events_action",
        ),
        CheckConstraint(
            f"actor_source IN ({_sql_values(ILLUSTRATION_ACTOR_SOURCES)})",
            name="ck_illustration_review_events_actor_source",
        ),
        CheckConstraint(
            f"from_approval_state IN ({_sql_values(ILLUSTRATION_APPROVAL_STATES)})",
            name="ck_illustration_review_events_from_state",
        ),
        CheckConstraint(
            f"to_approval_state IN ({_sql_values(ILLUSTRATION_APPROVAL_STATES)})",
            name="ck_illustration_review_events_to_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_revision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    from_approval_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_approval_state: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


def _reject_review_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Review events are append-only: no in-place edit or delete and no replay of an
# approval action (D-33-03). Attempt rows stay mutable for explicit status
# transitions (started -> succeeded/failed/outcome_unknown, D-33-02) and follow
# the reader-chat pattern without a delete guard (the job FK cascades).
event.listen(IllustrationReviewEvent, "before_update", _reject_review_mutation)
event.listen(IllustrationReviewEvent, "before_delete", _reject_review_mutation)
