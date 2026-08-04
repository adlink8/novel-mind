"""Phase 35-02: immutable Canon Fork candidate table (REQ-FORK-01 / REQ-CRE-01).

Creates the ``canon_forks`` table behind the D-35-03 frozen fork contract:

- ``fork_key`` is the immutable per-owner/novel identity; a conflicting retry
  is rejected, an identical replay reuses the sealed row.
- ``owner`` / ``novel`` / Original Canon ``source_version_key`` /
  ``source_snapshot_id`` / ``source_snapshot_hash`` / server-derived
  ``through_chapter`` / ``full_book_authorized`` / ``cutoff_snapshot_hash`` /
  ``citation_lineage`` / ``authorization`` are frozen at creation.
- ``scope_hash`` and ``manifest_hash`` are deterministic (same input -> same
  hash) and 64-hex enforced.
- ``active`` is bound to ``FALSE`` so no production active pointer can ever be
  created or switched by a fork mutation (candidate-only, D-35-03).

Revision ID: 20260801_canon_fork01
Revises: 20260801_canon_space01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_canon_fork01"
down_revision = "20260801_canon_space01"
branch_labels = None
depends_on = None

TABLE = "canon_forks"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the immutable candidate fork table (idempotent guard)."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("fork_key", sa.String(length=128), nullable=False),
        sa.Column("space", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("source_version_key", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("through_chapter", sa.Integer(), nullable=False),
        sa.Column("full_book_authorized", sa.Boolean(), nullable=False),
        sa.Column("cutoff_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("citation_lineage", sa.JSON(), nullable=False),
        sa.Column("authorization", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint(
            "owner_id", "novel_id", "fork_key", name="uq_canon_forks_key"
        ),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "manifest_hash", name="uq_canon_forks_manifest"
        ),
        sa.CheckConstraint(
            "space = 'fanfiction_canon'", name="ck_canon_forks_space"
        ),
        sa.CheckConstraint(
            "status IN ('candidate','approved','rejected','archived')",
            name="ck_canon_forks_status",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_canon_forks_snapshot_hash",
        ),
        sa.CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_canon_forks_cutoff_hash",
        ),
        sa.CheckConstraint(
            "length(scope_hash) = 64", name="ck_canon_forks_scope_hash"
        ),
        sa.CheckConstraint(
            "length(manifest_hash) = 64", name="ck_canon_forks_manifest_hash"
        ),
        sa.CheckConstraint("through_chapter > 0", name="ck_canon_forks_cutoff"),
        sa.CheckConstraint(
            "active = false", name="ck_canon_forks_no_active_pointer"
        ),
    )
    op.create_index("ix_canon_forks_scope", TABLE, ["owner_id", "novel_id"])
    op.create_index("ix_canon_forks_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_canon_forks_novel_id", TABLE, ["novel_id"])
    op.create_index(
        "ix_canon_forks_lineage",
        TABLE,
        ["space", "source_version_key", "source_snapshot_hash"],
    )


def downgrade() -> None:
    """Drop the fork table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_canon_forks_lineage", table_name=TABLE)
    op.drop_index("ix_canon_forks_novel_id", table_name=TABLE)
    op.drop_index("ix_canon_forks_owner_id", table_name=TABLE)
    op.drop_index("ix_canon_forks_scope", table_name=TABLE)
    op.drop_table(TABLE)
