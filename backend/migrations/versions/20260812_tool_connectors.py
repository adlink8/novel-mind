"""Create owner-scoped restricted HTTPS Tool connector versions."""

import sqlalchemy as sa
from alembic import op


revision = "20260812_tool_connectors"
down_revision = "20260812_user_preference_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_connectors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_tool_connectors_owner_name"),
    )
    op.create_index("idx_tool_connectors_owner", "tool_connectors", ["owner_id"])
    op.create_table(
        "tool_connector_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("request_schema", sa.JSON(), nullable=False),
        sa.Column("response_schema", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["connector_id"], ["tool_connectors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connector_id", "version", name="uq_tool_connector_versions_version"),
        sa.CheckConstraint("status IN ('draft','validated','active','disabled')", name="ck_tool_connector_versions_status"),
        sa.CheckConstraint("method IN ('GET','POST')", name="ck_tool_connector_versions_method"),
    )
    op.create_index("idx_tool_connector_versions_owner", "tool_connector_versions", ["owner_id", "connector_id", "version"])


def downgrade() -> None:
    op.drop_index("idx_tool_connector_versions_owner", table_name="tool_connector_versions")
    op.drop_table("tool_connector_versions")
    op.drop_index("idx_tool_connectors_owner", table_name="tool_connectors")
    op.drop_table("tool_connectors")
