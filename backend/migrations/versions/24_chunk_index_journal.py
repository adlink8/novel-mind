"""Add chunk_index_journal table for raw chunk indexing lifecycle (Phase 24-01).

Journal of every index_novel attempt: phase state machine
(started / deleting_old / chunks_persisted / embedding / completed / failed),
counts, collection name, source signature (idempotency key) and manifest
checksum (Phase 24-02 reconcile binding).

Revision ID: 24idxjournal1
Revises: 25relintake02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "24idxjournal1"
down_revision: Union[str, Sequence[str], None] = "25relintake02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "chunk_index_journal"


def upgrade() -> None:
    """Upgrade schema.

    Idempotent guard: historical migrations (10/11/12) create tables from live
    ORM metadata with checkfirst=True, so a database bootstrapped through the
    ORM path may already contain this table once the model is registered on
    Base. Only create when absent (same inspector pattern as 25relintake01).
    """
    insp = sa.inspect(op.get_bind())
    if insp.has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("total_chunks", sa.Integer(), nullable=False),
        sa.Column("embedded_chunks", sa.Integer(), nullable=False),
        sa.Column("failed_chunks", sa.Integer(), nullable=False),
        sa.Column("collection_name", sa.String(length=128), nullable=True),
        sa.Column("source_signature", sa.String(length=64), nullable=True),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "phase IN ('started','deleting_old','chunks_persisted',"
            "'embedding','completed','failed')",
            name="ck_chunk_index_journal_phase",
        ),
        sa.CheckConstraint(
            "kind IN ('index','reconcile_repair')",
            name="ck_chunk_index_journal_kind",
        ),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chunk_index_journal_novel_id"), TABLE, ["novel_id"], unique=False
    )
    op.create_index(
        op.f("ix_chunk_index_journal_attempt_id"), TABLE, ["attempt_id"], unique=True
    )
    op.create_index(
        "idx_chunk_index_journal_novel_phase",
        TABLE,
        ["novel_id", "phase"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(TABLE):
        return
    op.drop_index("idx_chunk_index_journal_novel_phase", table_name=TABLE)
    op.drop_index(op.f("ix_chunk_index_journal_attempt_id"), table_name=TABLE)
    op.drop_index(op.f("ix_chunk_index_journal_novel_id"), table_name=TABLE)
    op.drop_table(TABLE)
