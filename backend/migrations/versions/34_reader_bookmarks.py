"""Add owner-scoped reader bookmarks.

Revision ID: 34readerbookmark
Revises: 33creative01
"""

from alembic import op
import sqlalchemy as sa

revision = "34readerbookmark"
down_revision = "33creative01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reader_bookmarks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("position_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_reader_bookmarks_owner_novel", "reader_bookmarks", ["owner_id", "novel_id"])
    op.create_index("idx_reader_bookmarks_chapter", "reader_bookmarks", ["chapter_id"])


def downgrade() -> None:
    op.drop_index("idx_reader_bookmarks_chapter", table_name="reader_bookmarks")
    op.drop_index("idx_reader_bookmarks_owner_novel", table_name="reader_bookmarks")
    op.drop_table("reader_bookmarks")
