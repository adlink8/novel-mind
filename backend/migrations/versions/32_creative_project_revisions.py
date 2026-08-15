"""Add editable creative project revision history."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "32creative01"
down_revision: Union[str, Sequence[str], None] = "31canonspace01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "fanfiction_revisions"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("fanfiction_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("editor_kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["fanfiction_id"], ["fan_fictions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["fanfiction_chapters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fanfiction_revisions_fanfiction_id", TABLE, ["fanfiction_id"], unique=False)
    op.create_index("ix_fanfiction_revisions_chapter_id", TABLE, ["chapter_id"], unique=False)
    op.create_index("ix_fanfiction_revisions_project_created", TABLE, ["fanfiction_id", "created_at"], unique=False)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(TABLE):
        return
    op.drop_index("ix_fanfiction_revisions_project_created", table_name=TABLE)
    op.drop_index("ix_fanfiction_revisions_chapter_id", table_name=TABLE)
    op.drop_index("ix_fanfiction_revisions_fanfiction_id", table_name=TABLE)
    op.drop_table(TABLE)
