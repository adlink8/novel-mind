"""Durable checkpoint, terminal-state, and cost/cache ledger (Phase 28-01).

REQ-NM-01: every whole-book builder stage converges to an explicit terminal
state (``completed``/``isolated``/``blocked``) or a recoverable checkpoint;
resume never re-runs confirmed stages; cost/budget/evidence/owner lineage is
auditable; single chapter failure isolates only that chapter and blocks its
dependents (D-02/D-03/D-04); all outputs remain immutable candidate-only
(D-07).

Schema changes (all backward-compatible; old builder rows stay readable):
  - ``narrative_memory_build_stages``: + ``reason_code``, ``terminal_state``,
    ``idempotency_key``, ``source_checksum``, ``model_lineage``.
  - ``narrative_memory_build_runs``: + ``source_snapshot_hash``,
    ``resume_count``, ``last_error_code``.
  - ``narrative_memory_build_budget_ledgers``: + ``cache_hits``,
    ``cache_input_tokens``, ``cache_cost_usd`` (exact-cache cost ledger).
  - new append-only ``narrative_memory_build_checkpoints`` journal.

Design conventions (matching 20260801_2703_world_entity_projection.py):
  - Idempotent inspector guards + symmetric downgrade.
  - JSONB via ``JSONB().with_variant(JSON(), "sqlite")``.
  - New columns are nullable or carry server defaults so pre-existing rows
    remain valid before/after upgrade/downgrade.

Revision ID: 20260801_2801
Revises: 20260801_2703
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_2801"
down_revision = "20260801_2703"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_STAGE_TERMINAL_VALUES = "'completed','isolated','blocked'"


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(table):
        return False
    columns = {col["name"] for col in insp.get_columns(table)}
    return column in columns


def _add_column(
    table: str, column: sa.Column, check_exists: bool = True
) -> None:
    if check_exists and _has_column(table, column.name):
        return
    op.add_column(table, column)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""
    insp = sa.inspect(op.get_bind())

    # ---- narrative_memory_build_runs --------------------------------
    if insp.has_table("narrative_memory_build_runs"):
        _add_column(
            "narrative_memory_build_runs",
            sa.Column("source_snapshot_hash", sa.String(64), nullable=True),
        )
        _add_column(
            "narrative_memory_build_runs",
            sa.Column(
                "resume_count",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
        )
        _add_column(
            "narrative_memory_build_runs",
            sa.Column("last_error_code", sa.String(80), nullable=True),
        )

    # ---- narrative_memory_build_stages -------------------------------
    if insp.has_table("narrative_memory_build_stages"):
        _add_column(
            "narrative_memory_build_stages",
            sa.Column("reason_code", sa.String(64), nullable=True),
        )
        _add_column(
            "narrative_memory_build_stages",
            sa.Column("terminal_state", sa.String(24), nullable=True),
        )
        _add_column(
            "narrative_memory_build_stages",
            sa.Column("idempotency_key", sa.String(128), nullable=True),
        )
        _add_column(
            "narrative_memory_build_stages",
            sa.Column("source_checksum", sa.String(64), nullable=True),
        )
        _add_column(
            "narrative_memory_build_stages",
            sa.Column("model_lineage", JSONB, nullable=True),
        )
        # Normalise existing rows so old data immediately carries a terminal
        # state: completed -> completed; failed/paused/cancelled -> isolated;
        # blocked_dependency -> blocked. pending/running stay NULL (recoverable).
        op.execute(
            "UPDATE narrative_memory_build_stages SET terminal_state = 'completed'"
            " WHERE status = 'completed'"
        )
        op.execute(
            "UPDATE narrative_memory_build_stages SET terminal_state = 'isolated'"
            " WHERE status IN ('failed','paused_budget','paused_dependency',"
            "'cancelled','isolated')"
        )
        op.execute(
            "UPDATE narrative_memory_build_stages SET terminal_state = 'blocked'"
            " WHERE status = 'blocked_dependency'"
        )
        op.execute(
            "ALTER TABLE narrative_memory_build_stages ADD CONSTRAINT"
            " ck_memory_build_stages_terminal CHECK"
            f" (terminal_state IS NULL OR terminal_state IN ({_STAGE_TERMINAL_VALUES}))"
        )

    # ---- narrative_memory_build_budget_ledgers -----------------------
    if insp.has_table("narrative_memory_build_budget_ledgers"):
        _add_column(
            "narrative_memory_build_budget_ledgers",
            sa.Column(
                "cache_hits",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
        )
        _add_column(
            "narrative_memory_build_budget_ledgers",
            sa.Column(
                "cache_input_tokens",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
        )
        _add_column(
            "narrative_memory_build_budget_ledgers",
            sa.Column(
                "cache_cost_usd",
                sa.Numeric(18, 8),
                nullable=False,
                server_default="0",
            ),
        )

    # ---- narrative_memory_build_checkpoints (append-only journal) ----
    if not insp.has_table("narrative_memory_build_checkpoints"):
        op.create_table(
            "narrative_memory_build_checkpoints",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.Integer,
                sa.ForeignKey(
                    "narrative_memory_build_runs.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column("stage_key", sa.String(180), nullable=False),
            sa.Column("terminal_state", sa.String(24), nullable=False),
            sa.Column("reason_code", sa.String(64), nullable=True),
            sa.Column(
                "attempt_count",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column("checkpoint", JSONB, nullable=False),
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
            sa.CheckConstraint(
                f"terminal_state IN ({_STAGE_TERMINAL_VALUES})",
                name="ck_memory_build_checkpoint_terminal",
            ),
        )
        op.create_index(
            "idx_memory_build_checkpoints_run",
            "narrative_memory_build_checkpoints",
            ["run_id"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; old rows remain readable."""
    insp = sa.inspect(op.get_bind())

    if insp.has_table("narrative_memory_build_stages"):
        check_constraints = {
            c["name"]
            for c in insp.get_check_constraints("narrative_memory_build_stages")
        }
        if "ck_memory_build_stages_terminal" in check_constraints:
            op.drop_constraint(
                "ck_memory_build_stages_terminal",
                "narrative_memory_build_stages",
                type_="check",
            )
        for column in (
            "reason_code",
            "terminal_state",
            "idempotency_key",
            "source_checksum",
            "model_lineage",
        ):
            if _has_column("narrative_memory_build_stages", column):
                op.drop_column("narrative_memory_build_stages", column)

    if insp.has_table("narrative_memory_build_runs"):
        for column in (
            "source_snapshot_hash",
            "resume_count",
            "last_error_code",
        ):
            if _has_column("narrative_memory_build_runs", column):
                op.drop_column("narrative_memory_build_runs", column)

    if insp.has_table("narrative_memory_build_budget_ledgers"):
        for column in ("cache_hits", "cache_input_tokens", "cache_cost_usd"):
            if _has_column("narrative_memory_build_budget_ledgers", column):
                op.drop_column("narrative_memory_build_budget_ledgers", column)

    if insp.has_table("narrative_memory_build_checkpoints"):
        op.drop_index(
            "idx_memory_build_checkpoints_run",
            table_name="narrative_memory_build_checkpoints",
        )
        op.drop_table("narrative_memory_build_checkpoints")
