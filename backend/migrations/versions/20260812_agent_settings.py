"""Create owner-scoped Agent settings and task model bindings."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_agent_settings"
down_revision = "085fffd58ee9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_settings",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("auto_deep_analysis", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("memory_retention_days", sa.Integer(), nullable=True),
        sa.Column("show_analysis_progress", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notify_analysis_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_create_candidate_artifacts", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_id"),
        sa.CheckConstraint(
            "memory_retention_days IS NULL OR memory_retention_days >= 1",
            name="ck_agent_settings_retention_days",
        ),
    )
    op.create_table(
        "agent_task_model_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["ai_model_configs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "task", name="uq_agent_task_binding_owner_task"),
        sa.CheckConstraint(
            "task IN ('qa','deep_analysis','continuation','illustration','rag_eval','embedding')",
            name="ck_agent_task_binding_task",
        ),
    )
    op.create_index("ix_agent_task_model_bindings_owner_id", "agent_task_model_bindings", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_task_model_bindings_owner_id", table_name="agent_task_model_bindings")
    op.drop_table("agent_task_model_bindings")
    op.drop_table("agent_settings")
