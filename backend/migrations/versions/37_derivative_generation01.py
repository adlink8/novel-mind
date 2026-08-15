"""Phase 37-02: constrained derivative generation job tables (D-37-02).

Creates the durable generation control plane behind the agent-candidate /
script-publish boundary:

- ``derivative_generation_jobs``: durable task whose only input is a sealed
  ``derivative_context_packages`` row (NOT NULL FK). Freezes the sealed
  ``package_hash``, the generation ``intent``, prompt/schema/config hashes and
  an idempotency key; one nonterminal job per key via a partial unique index.
- ``derivative_generation_attempts``: one auditable provider call per attempt
  with request/response hashes, reserved vs actual usage/cost, latency and a
  stable error code.
- ``derivative_generation_candidates``: strict-schema provider output with the
  deterministic gate verdict (``candidate | blocked | needs_override``). The
  row carries no pointer/publish column; Original Canon is never a write target.

Revision ID: 20260802_derivative_generation01
Revises: 20260801_derivative_context01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260802_derivative_generation01"
down_revision = "20260801_derivative_context01"
branch_labels = None
depends_on = None

JOBS = "derivative_generation_jobs"
ATTEMPTS = "derivative_generation_attempts"
CANDIDATES = "derivative_generation_candidates"

# JSONB on PostgreSQL, plain JSON on SQLite (matches the ORM model variant so
# alembic check reports no drift).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the three generation tables (idempotent guards)."""
    if not _has_table(JOBS):
        op.create_table(
            JOBS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("fork_id", sa.Integer(), nullable=False),
            sa.Column("context_package_id", sa.Integer(), nullable=False),
            sa.Column("package_hash", sa.String(length=64), nullable=False),
            sa.Column("intent", sa.String(length=16), nullable=False),
            sa.Column("job_key", sa.String(length=120), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("status_reason", sa.String(length=160), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column("lease_id", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "cancel_requested", sa.Boolean(), nullable=False, server_default="false"
            ),
            sa.Column(
                "retry_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("prompt_hash", sa.String(length=64), nullable=False),
            sa.Column("schema_hash", sa.String(length=64), nullable=False),
            sa.Column("config_hash", sa.String(length=64), nullable=False),
            sa.Column("model_lineage", JSONB, nullable=False),
            sa.Column("price_snapshot", JSONB, nullable=False),
            sa.Column("budget_policy", JSONB, nullable=False),
            sa.Column("response_hash", sa.String(length=64), nullable=True),
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="derivative-generation.v1",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["fork_id"], ["canon_forks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["context_package_id"],
                ["derivative_context_packages.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "owner_id", "novel_id", "id", name="uq_derivative_generation_jobs_scope"
            ),
            sa.CheckConstraint(
                "status IN ('queued','running','paused_budget','paused_dependency',"
                "'succeeded','blocked','needs_override','failed','cancelled',"
                "'outcome_unknown')",
                name="ck_derivative_generation_jobs_status",
            ),
            sa.CheckConstraint(
                "intent IN ('continuation','rewrite')",
                name="ck_derivative_generation_jobs_intent",
            ),
            sa.CheckConstraint(
                "retry_count >= 0", name="ck_derivative_generation_jobs_retry"
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_derivative_generation_jobs_idempotency",
            ),
            sa.CheckConstraint(
                "length(package_hash) = 64",
                name="ck_derivative_generation_jobs_package_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_hash) = 64",
                name="ck_derivative_generation_jobs_prompt_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_derivative_generation_jobs_schema_hash",
            ),
            sa.CheckConstraint(
                "length(config_hash) = 64",
                name="ck_derivative_generation_jobs_config_hash",
            ),
        )
        op.create_index(
            "idx_derivative_generation_jobs_scope",
            JOBS,
            ["owner_id", "novel_id", "status"],
        )
        op.create_index("idx_derivative_generation_jobs_lease", JOBS, ["lease_expires_at"])
        op.create_index(
            "idx_derivative_generation_jobs_package",
            JOBS,
            ["owner_id", "novel_id", "context_package_id"],
        )
        op.create_index(
            "uq_derivative_generation_jobs_nonterminal_key",
            JOBS,
            ["idempotency_key"],
            unique=True,
            postgresql_where=sa.text(
                "status IN ('queued','running','paused_budget','paused_dependency')"
            ),
        )
        # Match the ORM model: only fork_id / context_package_id are index=True.
        op.create_index("ix_derivative_generation_jobs_fork_id", JOBS, ["fork_id"])
        op.create_index(
            "ix_derivative_generation_jobs_context_package_id",
            JOBS,
            ["context_package_id"],
        )

    if not _has_table(ATTEMPTS):
        op.create_table(
            ATTEMPTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("model_id", sa.String(length=120), nullable=False),
            sa.Column("provider_request_id", sa.String(length=160), nullable=True),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("response_hash", sa.String(length=64), nullable=True),
            sa.Column("reservation_key", sa.String(length=160), nullable=True),
            sa.Column(
                "reserved_input_tokens", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "reserved_output_tokens", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("reserved_cost_usd", sa.Numeric(18, 8), nullable=True),
            sa.Column("usage", JSONB, nullable=False),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("error_code", sa.String(length=80), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["job_id"], ["derivative_generation_jobs.id"], ondelete="CASCADE"
            ),
            sa.CheckConstraint(
                "status IN ('started','succeeded','failed','cancelled',"
                "'outcome_unknown')",
                name="ck_derivative_generation_attempts_status",
            ),
            sa.CheckConstraint(
                "attempt_number >= 1", name="ck_derivative_generation_attempts_number"
            ),
            sa.CheckConstraint(
                "length(request_hash) = 64",
                name="ck_derivative_generation_attempts_request_hash",
            ),
            sa.UniqueConstraint(
                "job_id", "attempt_number", name="uq_derivative_generation_attempts_job_number"
            ),
        )
        op.create_index(
            "idx_derivative_generation_attempts_job", ATTEMPTS, ["job_id"]
        )

    if not _has_table(CANDIDATES):
        op.create_table(
            CANDIDATES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("intent", sa.String(length=16), nullable=False),
            sa.Column("draft_text", sa.Text(), nullable=False),
            sa.Column("summary", sa.String(length=1000), nullable=True),
            sa.Column("citation_keys", JSONB, nullable=False),
            sa.Column("divergence", JSONB, nullable=True),
            sa.Column("branch_suggestions", JSONB, nullable=False),
            sa.Column("canon_delta_hash", sa.String(length=64), nullable=True),
            sa.Column("gate_verdict", sa.String(length=24), nullable=False),
            sa.Column("gate_reason", sa.String(length=80), nullable=True),
            sa.Column("package_hash", sa.String(length=64), nullable=False),
            sa.Column("prompt_hash", sa.String(length=64), nullable=False),
            sa.Column("schema_hash", sa.String(length=64), nullable=False),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("response_hash", sa.String(length=64), nullable=False),
            sa.Column("usage", JSONB, nullable=False),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=True),
            sa.Column("model_lineage", JSONB, nullable=False),
            sa.Column(
                "approval_state",
                sa.String(length=24),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="derivative-generation.v1",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["job_id"], ["derivative_generation_jobs.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint(
                "job_id", name="uq_derivative_generation_candidates_job"
            ),
            sa.CheckConstraint(
                "gate_verdict IN ('candidate','blocked','needs_override')",
                name="ck_derivative_generation_candidates_verdict",
            ),
            sa.CheckConstraint(
                "approval_state IN ('candidate','needs_override')",
                name="ck_derivative_generation_candidates_approval",
            ),
            sa.CheckConstraint(
                "intent IN ('continuation','rewrite')",
                name="ck_derivative_generation_candidates_intent",
            ),
            sa.CheckConstraint(
                "length(package_hash) = 64",
                name="ck_derivative_generation_candidates_package_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_hash) = 64",
                name="ck_derivative_generation_candidates_prompt_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_derivative_generation_candidates_schema_hash",
            ),
            sa.CheckConstraint(
                "length(request_hash) = 64",
                name="ck_derivative_generation_candidates_request_hash",
            ),
            sa.CheckConstraint(
                "length(response_hash) = 64",
                name="ck_derivative_generation_candidates_response_hash",
            ),
        )
        op.create_index(
            "idx_derivative_generation_candidates_scope",
            CANDIDATES,
            ["owner_id", "novel_id", "job_id"],
        )


def downgrade() -> None:
    """Drop the generation tables symmetrically."""
    for table, indexes in (
        (CANDIDATES, ["idx_derivative_generation_candidates_scope"]),
        (ATTEMPTS, ["idx_derivative_generation_attempts_job"]),
        (JOBS, ["idx_derivative_generation_jobs_scope", "idx_derivative_generation_jobs_lease", "idx_derivative_generation_jobs_package", "uq_derivative_generation_jobs_nonterminal_key", "ix_derivative_generation_jobs_fork_id", "ix_derivative_generation_jobs_context_package_id"]),
    ):
        if not _has_table(table):
            continue
        for index in indexes:
            try:
                op.drop_index(index, table_name=table)
            except Exception:
                pass
        op.drop_table(table)
