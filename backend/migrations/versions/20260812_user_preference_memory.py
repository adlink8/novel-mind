"""Create owner-scoped user preference memories."""

import sqlalchemy as sa
from alembic import op


revision: str = "20260812_user_preference_memory"
down_revision = "20260812_agent_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preference_memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explicit", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["reader_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_user_preference_memories_confidence",
        ),
    )
    op.create_index(
        "ix_user_preference_memories_source_message_id",
        "user_preference_memories",
        ["source_message_id"],
    )
    op.create_index(
        "idx_user_preference_memories_owner_active",
        "user_preference_memories",
        ["owner_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_user_preference_memories_owner_active",
        table_name="user_preference_memories",
    )
    op.drop_index(
        "ix_user_preference_memories_source_message_id",
        table_name="user_preference_memories",
    )
    op.drop_table("user_preference_memories")
