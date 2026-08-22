"""Phase 37-01: immutable derivative context package table (REQ-FORK-03/REQ-CRE-05).

Creates the ``derivative_context_packages`` table behind the D-37-01 contract:

- a package **cannot exist without its Canon Fork**: ``fork_id`` is a NOT NULL
  FK to ``canon_forks`` (CASCADE);
- ``package_key`` is the immutable per-owner/novel identity (unique constraint);
- ``space`` is bound to ``'fanfiction_canon'`` so no Original Canon / User
  Interpretation write target can ever be created (D-37-01 / D-36-03);
- the frozen fork lineage (``fork_key``, ``source_version_key``,
  ``source_snapshot_hash``, server-derived ``through_chapter``,
  ``full_book_authorized``, ``cutoff_snapshot_hash``, ``scope_hash``,
  ``manifest_hash``) is copied from the chosen fork and sealed with 64-hex
  check constraints; the canonical payload is sealed with ``package_hash``;
- ``intent IN ('continuation','rewrite')`` is the only generation intent
  vocabulary.

Revision ID: 20260801_derivative_context01
Revises: 20260801_derivative_agent_edit01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_derivative_context01"
down_revision = "20260801_derivative_agent_edit01"
branch_labels = None
depends_on = None

TABLE = "derivative_context_packages"

# JSONB on PostgreSQL, plain JSON on SQLite (matches the ORM model variant so
# alembic check reports no drift).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the sealed context package table (idempotent guard)."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("fork_id", sa.Integer(), nullable=False),
        sa.Column("package_key", sa.String(length=128), nullable=False),
        sa.Column("space", sa.String(length=32), nullable=False),
        sa.Column("intent", sa.String(length=16), nullable=False),
        sa.Column("fork_key", sa.String(length=128), nullable=False),
        sa.Column("source_version_key", sa.String(length=128), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("through_chapter", sa.Integer(), nullable=False),
        sa.Column("full_book_authorized", sa.Boolean(), nullable=False),
        sa.Column("cutoff_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("canonical_payload", JSONB, nullable=False),
        sa.Column("budget_estimate", JSONB, nullable=False),
        sa.Column("package_hash", sa.String(length=64), nullable=False),
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
            "owner_id",
            "novel_id",
            "package_key",
            name="uq_derivative_context_packages_key",
        ),
        sa.CheckConstraint(
            "space = 'fanfiction_canon'",
            name="ck_derivative_context_packages_space",
        ),
        sa.CheckConstraint(
            "intent IN ('continuation','rewrite')",
            name="ck_derivative_context_packages_intent",
        ),
        sa.CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_context_packages_snapshot_hash",
        ),
        sa.CheckConstraint(
            "length(cutoff_snapshot_hash) = 64",
            name="ck_derivative_context_packages_cutoff_hash",
        ),
        sa.CheckConstraint(
            "length(scope_hash) = 64",
            name="ck_derivative_context_packages_scope_hash",
        ),
        sa.CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_derivative_context_packages_manifest_hash",
        ),
        sa.CheckConstraint(
            "length(package_hash) = 64",
            name="ck_derivative_context_packages_package_hash",
        ),
        sa.CheckConstraint(
            "through_chapter > 0",
            name="ck_derivative_context_packages_cutoff",
        ),
    )
    op.create_index(
        "ix_derivative_context_packages_scope", TABLE, ["owner_id", "novel_id"]
    )
    op.create_index(
        "ix_derivative_context_packages_fork",
        TABLE,
        ["owner_id", "novel_id", "fork_id"],
    )
    # Match the ORM model: owner_id / novel_id / fork_id are index=True FK columns.
    op.create_index("ix_derivative_context_packages_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_derivative_context_packages_novel_id", TABLE, ["novel_id"])
    op.create_index("ix_derivative_context_packages_fork_id", TABLE, ["fork_id"])


def downgrade() -> None:
    """Drop the package table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_derivative_context_packages_fork_id", table_name=TABLE)
    op.drop_index("ix_derivative_context_packages_novel_id", table_name=TABLE)
    op.drop_index("ix_derivative_context_packages_owner_id", table_name=TABLE)
    op.drop_index("ix_derivative_context_packages_fork", table_name=TABLE)
    op.drop_index("ix_derivative_context_packages_scope", table_name=TABLE)
    op.drop_table(TABLE)
