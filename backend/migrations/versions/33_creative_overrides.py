"""Add explicit owner-scoped creative deviation records.

Revision ID: 33creative01
Revises: 32creative01
"""

from alembic import op
import sqlalchemy as sa


revision = "33creative01"
down_revision = "32creative01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fanfiction_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fanfiction_id", sa.Integer(), nullable=False),
        sa.Column("override_key", sa.String(length=160), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("original_evidence_key", sa.String(length=160), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["fanfiction_id"], ["fan_fictions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fanfiction_id", "override_key", name="uq_fanfiction_override_key"),
    )
    op.create_index("ix_fanfiction_overrides_fanfiction_id", "fanfiction_overrides", ["fanfiction_id"])
    op.create_index(
        "ix_fanfiction_overrides_project",
        "fanfiction_overrides",
        ["fanfiction_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fanfiction_overrides_project", table_name="fanfiction_overrides")
    op.drop_index("ix_fanfiction_overrides_fanfiction_id", table_name="fanfiction_overrides")
    op.drop_table("fanfiction_overrides")
