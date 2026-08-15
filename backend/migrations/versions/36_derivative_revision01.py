"""Phase 36-03: immutable derivative chapter revision table (REQ-FORK-02 / REQ-CRE-04).

Creates the ``derivative_revisions`` table behind the D-36-02 append-only
lineage contract:

- every row is bound to its chapter via a NOT NULL CASCADE FK (a revision
  cannot exist outside a chapter) and carries the owner/novel/project scope
  denormalized so no query can escape the owner scope;
- ``revision_number`` is the chapter's optimistic-concurrency token value at
  write time and is unique per chapter, so a client's ``base_revision`` maps
  1:1 to a row and history ordering is deterministic;
- ``parent_revision_id`` is a self-FK to the previous snapshot (NULL only for
  the chapter's root ``create`` row), giving every rollback and diff a stable,
  auditable lineage pointer;
- ``content`` stores the canonical Markdown snapshot and ``content_checksum``
  seals it (64-hex SHA-256 of the deterministic canonicalization, D-36-02);
- ``kind`` is ``create``/``autosave``/``rollback`` only and
  ``approval_state`` is ``not_required``/``approved`` — the rollback journal
  columns ``actor_id`` / ``reason`` / ``approval_state`` make every rollback
  traceable and unreplayable as a silent history rewrite (T-36-03-02).

Revision ID: 20260801_derivative_revision01
Revises: 20260801_derivative_chapter01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260801_derivative_revision01"
down_revision = "20260801_derivative_chapter01"
branch_labels = None
depends_on = None

TABLE = "derivative_revisions"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the append-only revision table (idempotent guard)."""
    if _has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approval_state", sa.String(length=16), nullable=False),
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
        sa.ForeignKeyConstraint(["chapter_id"], ["derivative_chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["derivative_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["derivative_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "chapter_id",
            "revision_number",
            name="uq_derivative_revisions_chapter_number",
        ),
        sa.CheckConstraint(
            "revision_number > 0", name="ck_derivative_revisions_number"
        ),
        sa.CheckConstraint(
            "length(content_checksum) = 64", name="ck_derivative_revisions_checksum"
        ),
        sa.CheckConstraint(
            "kind IN ('create','autosave','rollback')",
            name="ck_derivative_revisions_kind",
        ),
        sa.CheckConstraint(
            "approval_state IN ('not_required','approved')",
            name="ck_derivative_revisions_approval",
        ),
    )
    op.create_index(
        "ix_derivative_revisions_scope",
        TABLE,
        ["owner_id", "novel_id", "project_id", "chapter_id"],
    )
    # Match the ORM model: chapter_id/owner_id/novel_id/project_id/
    # parent_revision_id/actor_id are index=True FK columns.
    op.create_index("ix_derivative_revisions_chapter_id", TABLE, ["chapter_id"])
    op.create_index("ix_derivative_revisions_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_derivative_revisions_novel_id", TABLE, ["novel_id"])
    op.create_index("ix_derivative_revisions_project_id", TABLE, ["project_id"])
    op.create_index(
        "ix_derivative_revisions_parent_revision_id", TABLE, ["parent_revision_id"]
    )
    op.create_index("ix_derivative_revisions_actor_id", TABLE, ["actor_id"])


def downgrade() -> None:
    """Drop the revision table symmetrically."""
    if not _has_table(TABLE):
        return
    op.drop_index("ix_derivative_revisions_actor_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_parent_revision_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_project_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_novel_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_owner_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_chapter_id", table_name=TABLE)
    op.drop_index("ix_derivative_revisions_scope", table_name=TABLE)
    op.drop_table(TABLE)
