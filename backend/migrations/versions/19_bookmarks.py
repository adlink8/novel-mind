"""Add owner-scoped reader selection bookmarks."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "19bookmarks01"
down_revision: Union[str, Sequence[str], None] = "18appsetting1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False),
        sa.Column("source_end", sa.Integer(), nullable=False),
        sa.Column("selected_text", sa.Text(), nullable=False),
        sa.Column("selection_text_hash", sa.String(length=64), nullable=False),
        sa.Column("chapter_content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source_start >= 0", name="ck_bookmarks_source_start"),
        sa.CheckConstraint("source_end > source_start", name="ck_bookmarks_source_range"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookmarks_owner_novel", "bookmarks", ["owner_id", "novel_id"])
    op.create_index("ix_bookmarks_chapter", "bookmarks", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("ix_bookmarks_chapter", table_name="bookmarks")
    op.drop_index("ix_bookmarks_owner_novel", table_name="bookmarks")
    op.drop_table("bookmarks")
