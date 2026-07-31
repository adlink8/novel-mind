"""Persist images generated from Reader Chat selections."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "26readerimages01"
down_revision: Union[str, Sequence[str], None] = "19bookmarkmerge01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    if "generated_images" not in table_names:
        op.create_table(
            "generated_images",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("chapter_id", sa.Integer(), nullable=True),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("prompt_cn", sa.Text(), nullable=False),
            sa.Column("prompt_en", sa.Text(), nullable=False),
            sa.Column("source_start", sa.Integer(), nullable=True),
            sa.Column("source_end", sa.Integer(), nullable=True),
            sa.Column("selected_text", sa.Text(), nullable=True),
            sa.Column("file_path", sa.String(length=1000), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("width", sa.Integer(), nullable=False),
            sa.Column("height", sa.Integer(), nullable=False),
            sa.Column("model_used", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["conversation_id"], ["reader_conversations.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("generated_images")}
    if "idx_generated_images_scope" not in indexes:
        op.create_index(
            "idx_generated_images_scope",
            "generated_images",
            ["owner_id", "novel_id", "conversation_id"],
        )
    if "idx_generated_images_chapter" not in indexes:
        op.create_index(
            "idx_generated_images_chapter",
            "generated_images",
            ["novel_id", "chapter_id"],
        )

    columns = {column["name"] for column in inspector.get_columns("reader_messages")}
    if "message_type" not in columns:
        op.add_column(
            "reader_messages",
            sa.Column("message_type", sa.String(length=16), server_default="text", nullable=False),
        )
    checks = {
        check["name"] for check in inspector.get_check_constraints("reader_messages")
    }
    if "ck_reader_messages_type" not in checks:
        op.create_check_constraint(
            "ck_reader_messages_type",
            "reader_messages",
            "message_type IN ('text','image')",
        )
    if "image_generation_id" not in columns:
        op.add_column(
            "reader_messages",
            sa.Column("image_generation_id", sa.Integer(), nullable=True),
        )
    foreign_keys = {
        foreign_key["name"]
        for foreign_key in inspector.get_foreign_keys("reader_messages")
    }
    if "fk_reader_messages_image_generation" not in foreign_keys:
        op.create_foreign_key(
            "fk_reader_messages_image_generation",
            "reader_messages",
            "generated_images",
            ["image_generation_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_reader_messages_image_generation",
        "reader_messages",
        type_="foreignkey",
    )
    op.drop_column("reader_messages", "image_generation_id")
    op.drop_constraint("ck_reader_messages_type", "reader_messages", type_="check")
    op.drop_column("reader_messages", "message_type")
    op.drop_index("idx_generated_images_chapter", table_name="generated_images")
    op.drop_index("idx_generated_images_scope", table_name="generated_images")
    op.drop_table("generated_images")
