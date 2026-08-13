"""Persist whether a SkillVersion is backed by a builtin or DB manifest."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260813_skillmode"
down_revision = "20260812_tool_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "skill_versions",
        sa.Column(
            "execution_mode",
            sa.String(length=20),
            nullable=False,
            server_default="declarative_only",
        ),
    )


def downgrade() -> None:
    op.drop_column("skill_versions", "execution_mode")
