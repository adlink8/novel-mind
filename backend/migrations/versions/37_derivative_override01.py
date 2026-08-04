"""Phase 37-04: explicit divergence override table (D-37-03 / REQ-CRE-06).

Creates ``derivative_overrides``: the append-only, owner-scoped explicit
CanonDelta override record for a blocked/``needs_override`` derivative candidate.

- ``kind`` / ``reason`` / ``affected_evidence`` / ``canon_delta_hash`` /
  ``evidence_snapshot`` / ``actor_id`` are the frozen override surface.
- ``approval_state`` (``pending | approved | rejected``) + approver/time/reason
  journal the explicit owner review action; only that journal may change.
- One override row per candidate (``uq_derivative_overrides_candidate``), so a
  candidate can never be approved twice.
- The row carries no pointer/publish column and nothing here writes Original
  Canon / User Interpretation / Narrative Memory (D-37-02 forbidden publish path).

Revision ID: 20260802_derivative_override01
Revises: 20260802_derivative_generation01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260802_derivative_override01"
down_revision = "20260802_derivative_generation01"
branch_labels = None
depends_on = None

OVERRIDES = "derivative_overrides"

# JSONB on PostgreSQL, plain JSON on SQLite (matches the ORM model variant so
# alembic check reports no drift).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the override table (idempotent guard)."""
    if not _has_table(OVERRIDES):
        op.create_table(
            OVERRIDES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("chapter_id", sa.Integer(), nullable=False),
            sa.Column("fork_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("affected_evidence", JSONB, nullable=False),
            sa.Column("canon_delta_hash", sa.String(length=64), nullable=False),
            sa.Column("evidence_snapshot", JSONB, nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column(
                "approval_state",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("approver_id", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approval_reason", sa.Text(), nullable=True),
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
                ["project_id"], ["derivative_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["chapter_id"], ["derivative_chapters.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["fork_id"], ["canon_forks.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["candidate_id"],
                ["derivative_generation_candidates.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["job_id"], ["derivative_generation_jobs.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["approver_id"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint(
                "candidate_id", name="uq_derivative_overrides_candidate"
            ),
            sa.CheckConstraint(
                "kind IN ('character','timeline','world_rule','clue','other')",
                name="ck_derivative_overrides_kind",
            ),
            sa.CheckConstraint(
                "approval_state IN ('pending','approved','rejected')",
                name="ck_derivative_overrides_approval",
            ),
            sa.CheckConstraint(
                "length(canon_delta_hash) = 64",
                name="ck_derivative_overrides_delta_hash",
            ),
            sa.CheckConstraint(
                "reason <> ''", name="ck_derivative_overrides_reason"
            ),
        )
        op.create_index(
            "ix_derivative_overrides_scope",
            OVERRIDES,
            ["owner_id", "novel_id", "project_id", "candidate_id"],
        )
        op.create_index(
            "ix_derivative_overrides_status",
            OVERRIDES,
            ["owner_id", "novel_id", "approval_state"],
        )
        # Match the ORM model: project_id / chapter_id / actor_id are index=True.
        op.create_index("ix_derivative_overrides_project_id", OVERRIDES, ["project_id"])
        op.create_index("ix_derivative_overrides_chapter_id", OVERRIDES, ["chapter_id"])
        op.create_index("ix_derivative_overrides_actor_id", OVERRIDES, ["actor_id"])


def downgrade() -> None:
    """Drop the override table symmetrically."""
    if not _has_table(OVERRIDES):
        return
    for index in (
        "ix_derivative_overrides_scope",
        "ix_derivative_overrides_status",
        "ix_derivative_overrides_project_id",
        "ix_derivative_overrides_chapter_id",
        "ix_derivative_overrides_actor_id",
    ):
        try:
            op.drop_index(index, table_name=OVERRIDES)
        except Exception:
            pass
    op.drop_table(OVERRIDES)
