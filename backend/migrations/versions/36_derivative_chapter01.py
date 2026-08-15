"""Phase 36-02: owner-scoped derivative chapter plan table (REQ-FORK-02 / REQ-CRE-03).

Creates the ``derivative_chapters`` table behind the D-36-02 contract:

- every chapter is bound to its derivative project via a NOT NULL CASCADE FK
  (a chapter cannot exist outside a project) and carries the owner/novel scope
  denormalized so no query can escape the owner/novel scope;
- ``position`` is unique per project: the ordered chapter plan is stable and
  reordering rewrites the full set in one transaction;
- ``markdown`` stores the canonical Markdown draft and ``markdown_checksum``
  seals it (64-hex SHA-256 of the deterministic canonicalization, D-36-02);
- ``revision`` is the optimistic-concurrency token (> 0) the client echoes back
  as ``base_revision`` so a stale write fails closed instead of overwriting;
- ``status`` is ``draft`` or ``archived`` only — there is no client-controlled
  published state (Phase 39 owns publication). D-36-03 is inherited: the project
  row is database-sealed to ``fanfiction_canon``, so chapter writes can only
  ever form a Fanfiction Canon draft.

Revision ID: 20260801_derivative_chapter01
Revises: 20260801_derivative_project01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_derivative_chapter01"
down_revision = "20260801_derivative_project01"
branch_labels = None
depends_on = None

TABLE = "derivative_chapters"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the ordered chapter plan table (idempotent guard)."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("markdown_checksum", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
        sa.UniqueConstraint(
            "project_id", "position", name="uq_derivative_chapters_position"
        ),
        sa.CheckConstraint(
            "status IN ('draft','archived')", name="ck_derivative_chapters_status"
        ),
        sa.CheckConstraint(
            "length(markdown_checksum) = 64", name="ck_derivative_chapters_checksum"
        ),
        sa.CheckConstraint(
            "position >= 0", name="ck_derivative_chapters_position_nonneg"
        ),
        sa.CheckConstraint("revision > 0", name="ck_derivative_chapters_revision"),
    )
    op.create_index(
        "ix_derivative_chapters_scope", TABLE, ["owner_id", "novel_id", "project_id"]
    )
    # Match the ORM model: owner_id / novel_id / project_id are index=True FKs.
    op.create_index("ix_derivative_chapters_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_derivative_chapters_novel_id", TABLE, ["novel_id"])
    op.create_index("ix_derivative_chapters_project_id", TABLE, ["project_id"])


def downgrade() -> None:
    """Drop the chapter table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_derivative_chapters_project_id", table_name=TABLE)
    op.drop_index("ix_derivative_chapters_novel_id", table_name=TABLE)
    op.drop_index("ix_derivative_chapters_owner_id", table_name=TABLE)
    op.drop_index("ix_derivative_chapters_scope", table_name=TABLE)
    op.drop_table(TABLE)
