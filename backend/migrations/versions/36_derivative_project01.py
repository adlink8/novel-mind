"""Phase 36-01: owner-scoped derivative project table (REQ-FORK-02 / REQ-CRE-03).

Creates the ``derivative_projects`` table behind the D-36-01 / D-36-03 contract:

- a project **cannot exist without its Canon Fork**: ``fork_id`` is a NOT NULL
  FK to ``canon_forks`` (CASCADE);
- ``project_key`` is the immutable per-owner/novel identity (unique constraint,
  mirroring ``canon_forks.fork_key``);
- ``space`` is bound to ``'fanfiction_canon'`` so no Original Canon / User
  Interpretation write target can ever be created (D-36-03);
- the frozen fork lineage (``fork_key``, ``source_version_key``,
  ``source_snapshot_hash``, server-derived ``through_chapter``,
  ``full_book_authorized``, ``cutoff_snapshot_hash``, ``scope_hash``,
  ``manifest_hash``) is copied from the chosen fork and sealed with
  check constraints (64-hex hashes, ``through_chapter > 0``);
- only ``status IN ('active','archived')`` is a valid project state.

Revision ID: 20260801_derivative_project01
Revises: 20260801_canon_contamination04
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_derivative_project01"
down_revision = "20260801_canon_contamination04"
branch_labels = None
depends_on = None

TABLE = "derivative_projects"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the owner-scoped project table (idempotent guard)."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("fork_id", sa.Integer(), nullable=False),
        sa.Column("project_key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("space", sa.String(length=32), nullable=False),
        sa.Column("fork_key", sa.String(length=128), nullable=False),
        sa.Column("source_version_key", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("through_chapter", sa.Integer(), nullable=False),
        sa.Column("full_book_authorized", sa.Boolean(), nullable=False),
        sa.Column("cutoff_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
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
        sa.UniqueConstraint(
            "owner_id", "novel_id", "project_key", name="uq_derivative_projects_key"
        ),
        sa.CheckConstraint(
            "space = 'fanfiction_canon'", name="ck_derivative_projects_space"
        ),
        sa.CheckConstraint(
            "status IN ('active','archived')", name="ck_derivative_projects_status"
        ),
        sa.CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_projects_snapshot_hash",
        ),
        sa.CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_derivative_projects_cutoff_hash",
        ),
        sa.CheckConstraint(
            "length(scope_hash) = 64", name="ck_derivative_projects_scope_hash"
        ),
        sa.CheckConstraint(
            "length(manifest_hash) = 64", name="ck_derivative_projects_manifest_hash"
        ),
        sa.CheckConstraint(
            "through_chapter > 0", name="ck_derivative_projects_cutoff"
        ),
    )
    op.create_index("ix_derivative_projects_scope", TABLE, ["owner_id", "novel_id"])
    op.create_index(
        "ix_derivative_projects_fork", TABLE, ["owner_id", "novel_id", "fork_id"]
    )
    # Match the ORM model: owner_id / novel_id / fork_id are index=True FK columns.
    op.create_index("ix_derivative_projects_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_derivative_projects_novel_id", TABLE, ["novel_id"])
    op.create_index("ix_derivative_projects_fork_id", TABLE, ["fork_id"])


def downgrade() -> None:
    """Drop the project table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_derivative_projects_fork_id", table_name=TABLE)
    op.drop_index("ix_derivative_projects_novel_id", table_name=TABLE)
    op.drop_index("ix_derivative_projects_owner_id", table_name=TABLE)
    op.drop_index("ix_derivative_projects_fork", table_name=TABLE)
    op.drop_index("ix_derivative_projects_scope", table_name=TABLE)
    op.drop_table(TABLE)
