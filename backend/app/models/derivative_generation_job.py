"""Durable constrained derivative generation job (Phase 37-02, D-37-02).

REQ-FORK-03 / REQ-CRE-06: an LLM/agent may only ever produce a strict-schema
candidate; the deterministic server owns evidence/scope/schema/budget gates and
any publication authority. This module is the control plane (the
``reader_chat.py`` / ``illustration_job.py`` analog):

- ``derivative_generation_jobs``: durable generation task whose *only* input is
  a sealed ``derivative_context_packages`` row. The job freezes the sealed
  ``package_hash``, the generation intent, the prompt/schema/config hashes and
  the provider-neutral model lineage; it carries lease/cancel/retry semantics
  so a paused job is recoverable and a terminal job is never silently re-called.
  A duplicate idempotency key replays the existing nonterminal job instead of
  charging twice.
- ``derivative_generation_attempts``: one auditable provider call per attempt
  with request/response hashes, provider request id, reserved vs actual
  usage/cost, latency, status and a stable error code. ``outcome_unknown``
  keeps the provider-may-have-consumed-the-request case explicit.
- ``derivative_generation_candidates``: the strict-schema provider output plus
  the deterministic gate verdict (``candidate | blocked | needs_override``).
  A row is append-only; it is never written to any Original Canon space and
  never promoted to an active pointer. Explicit divergence (D-37-03) is
  recorded as a ``CanonDelta`` and only ever yields ``needs_override``.
  BranchSuggestions (D-37-05) are disabled-by-default candidate outputs.

Design conventions (following ``illustration_job.py``):
- One nonterminal job per idempotency key via a partial unique index; a
  duplicate request replays the existing job instead of charging twice.
- The job can only exist for an owned sealed package: ``context_package_id`` is
  a NOT NULL FK and ``package_hash`` replays on every run (T-37-02-01).
- No active-pointer / promotion / published column exists: nothing here becomes
  reader visible (D-37-02 forbidden publish path).
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

DERIVATIVE_GENERATION_SCHEMA_VERSION = "derivative-generation.v1"
# Explicit terminal and paused/failure states (reader_chat/illustration analog);
# ``outcome_unknown`` is an explicit state (provider may have charged us).
DERIVATIVE_GENERATION_JOB_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
    "succeeded",
    "blocked",
    "needs_override",
    "failed",
    "cancelled",
    "outcome_unknown",
)
# Recoverable statuses: a run may (re)execute a job in one of these states.
# ``needs_override`` is terminal for the runner: the candidate awaits explicit
# human approval (a later phase) and must not be silently re-generated.
DERIVATIVE_GENERATION_NONTERMINAL_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
)
DERIVATIVE_GENERATION_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
)
# D-37-02 deterministic gate verdicts (RESEARCH: candidate|blocked|needs_override).
DERIVATIVE_CANDIDATE_VERDICTS = ("candidate", "blocked", "needs_override")
# Candidate approval state is a projection; approval actions arrive in a later
# phase. Nothing here can be promoted to Original or an active pointer.
DERIVATIVE_CANDIDATE_APPROVAL_STATES = ("candidate", "needs_override")


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class DerivativeGenerationJob(TimestampMixin, Base):
    """One durable candidate-generation task for one sealed context package."""

    __tablename__ = "derivative_generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_derivative_generation_jobs_scope",
        ),
        CheckConstraint(
            f"status IN ({_sql_values(DERIVATIVE_GENERATION_JOB_STATUSES)})",
            name="ck_derivative_generation_jobs_status",
        ),
        CheckConstraint(
            "intent IN ('continuation','rewrite')",
            name="ck_derivative_generation_jobs_intent",
        ),
        CheckConstraint("retry_count >= 0", name="ck_derivative_generation_jobs_retry"),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_derivative_generation_jobs_idempotency",
        ),
        CheckConstraint(
            "length(package_hash) = 64",
            name="ck_derivative_generation_jobs_package_hash",
        ),
        CheckConstraint(
            "length(prompt_hash) = 64",
            name="ck_derivative_generation_jobs_prompt_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_derivative_generation_jobs_schema_hash",
        ),
        CheckConstraint(
            "length(config_hash) = 64",
            name="ck_derivative_generation_jobs_config_hash",
        ),
        Index(
            "idx_derivative_generation_jobs_scope",
            "owner_id",
            "novel_id",
            "status",
        ),
        Index("idx_derivative_generation_jobs_lease", "lease_expires_at"),
        Index(
            "idx_derivative_generation_jobs_package",
            "owner_id",
            "novel_id",
            "context_package_id",
        ),
        # D-37-02 idempotency: one nonterminal job per lineage key. A duplicate
        # submission replays the existing job instead of charging twice.
        Index(
            "uq_derivative_generation_jobs_nonterminal_key",
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
    # D-37-02: a generation job cannot exist without its explicit Canon Fork
    # anchor; the fork is the one the sealed package was compiled from.
    fork_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("canon_forks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The ONLY accepted input: a sealed context package (T-37-02-01).
    context_package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_context_packages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The sealed package hash the job consumes; replayed on every run.
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
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
    # Frozen lineage: the prompt, schema and config the job must replay.
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Provider-neutral deployment lineage recorded by the gateway (no secrets).
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # D-37-02: cost is always settled against a frozen price snapshot.
    price_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Frozen budget policy snapshot the run reserved against (auditable).
    budget_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DERIVATIVE_GENERATION_SCHEMA_VERSION
    )


class DerivativeGenerationAttempt(TimestampMixin, Base):
    """Auditable provider call attempt for one generation job (D-37-02)."""

    __tablename__ = "derivative_generation_attempts"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_values(DERIVATIVE_GENERATION_ATTEMPT_STATUSES)})",
            name="ck_derivative_generation_attempts_status",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_derivative_generation_attempts_number",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_derivative_generation_attempts_request_hash",
        ),
        UniqueConstraint(
            "job_id",
            "attempt_number",
            name="uq_derivative_generation_attempts_job_number",
        ),
        Index("idx_derivative_generation_attempts_job", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Frozen deployment lineage for this attempt (provider-neutral).
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    # Budget lineage: the worst-case reservation vs the actual settled usage.
    reservation_key: Mapped[str | None] = mapped_column(String(160))
    reserved_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    # Stable failure reason code (e.g. provider_timeout, schema_invalid).
    error_code: Mapped[str | None] = mapped_column(String(80))


class DerivativeGenerationCandidate(TimestampMixin, Base):
    """Strict-schema provider output with the deterministic gate verdict."""

    __tablename__ = "derivative_generation_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_derivative_generation_candidates_job"),
        CheckConstraint(
            f"gate_verdict IN ({_sql_values(DERIVATIVE_CANDIDATE_VERDICTS)})",
            name="ck_derivative_generation_candidates_verdict",
        ),
        CheckConstraint(
            f"approval_state IN ({_sql_values(DERIVATIVE_CANDIDATE_APPROVAL_STATES)})",
            name="ck_derivative_generation_candidates_approval",
        ),
        CheckConstraint(
            "intent IN ('continuation','rewrite')",
            name="ck_derivative_generation_candidates_intent",
        ),
        CheckConstraint(
            "length(package_hash) = 64",
            name="ck_derivative_generation_candidates_package_hash",
        ),
        CheckConstraint(
            "length(prompt_hash) = 64",
            name="ck_derivative_generation_candidates_prompt_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_derivative_generation_candidates_schema_hash",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_derivative_generation_candidates_request_hash",
        ),
        CheckConstraint(
            "length(response_hash) = 64",
            name="ck_derivative_generation_candidates_response_hash",
        ),
        Index(
            "idx_derivative_generation_candidates_scope",
            "owner_id",
            "novel_id",
            "job_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    intent: Mapped[str] = mapped_column(String(16), nullable=False)
    # Strict-schema output fields (T-37-02-01).
    draft_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(1000))
    # Evidence refs the model cited; must be a subset of the package allowlist.
    citation_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Explicit derivative override (D-37-03) — stored, never promoted.
    divergence: Mapped[dict | None] = mapped_column(JSONB)
    # Disabled-by-default branch suggestions (D-37-05).
    branch_suggestions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    canon_delta_hash: Mapped[str | None] = mapped_column(String(64))
    # Deterministic gate verdict + stable reason code (RESEARCH).
    gate_verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    gate_reason: Mapped[str | None] = mapped_column(String(80))
    # Lineage the candidate replays.
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Candidate-only: approval state is a projection; publish is out of scope.
    approval_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DERIVATIVE_GENERATION_SCHEMA_VERSION
    )


def _reject_candidate_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Candidates are append-only: the gate verdict and lineage are frozen at write
# time. Job/attempt rows stay mutable for explicit state transitions and follow
# the reader-chat pattern without a delete guard (the job FK cascades).
event.listen(DerivativeGenerationCandidate, "before_update", _reject_candidate_mutation)
event.listen(DerivativeGenerationCandidate, "before_delete", _reject_candidate_mutation)

__all__ = [
    "DERIVATIVE_CANDIDATE_APPROVAL_STATES",
    "DERIVATIVE_CANDIDATE_VERDICTS",
    "DERIVATIVE_GENERATION_ATTEMPT_STATUSES",
    "DERIVATIVE_GENERATION_JOB_STATUSES",
    "DERIVATIVE_GENERATION_NONTERMINAL_STATUSES",
    "DERIVATIVE_GENERATION_SCHEMA_VERSION",
    "DerivativeGenerationAttempt",
    "DerivativeGenerationCandidate",
    "DerivativeGenerationJob",
]
