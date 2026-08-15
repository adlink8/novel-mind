"""Create the three knowledge-space artifact contract."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "31canonspace01"
down_revision: Union[str, Sequence[str], None] = "24idxjournal1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "canon_space_artifacts"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("space", sa.String(length=32), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("version_key", sa.String(length=128), nullable=False),
        sa.Column("authority", sa.String(length=32), nullable=False),
        sa.Column("citation_policy", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_chapter_id", sa.Integer(), nullable=True),
        sa.Column("source_text_chunk_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "space IN ('original_canon','user_interpretation','fanfiction_canon')",
            name="ck_canon_space_artifact_space",
        ),
        sa.CheckConstraint(
            "authority IN ('source_text','user_assertion','creative_draft')",
            name="ck_canon_space_artifact_authority",
        ),
        sa.CheckConstraint(
            "citation_policy IN ('original_leaf','interpretation_with_original_refs','fanfiction_only')",
            name="ck_canon_space_artifact_citation",
        ),
        sa.CheckConstraint(
            "status IN ('draft','accepted','rejected','archived')",
            name="ck_canon_space_artifact_status",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_text_chunk_id"], ["text_chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "space", "namespace", "version_key",
            name="uq_canon_space_artifact_version",
        ),
    )
    op.create_index("ix_canon_space_artifacts_owner_id", TABLE, ["owner_id"], unique=False)
    op.create_index("ix_canon_space_artifacts_novel_id", TABLE, ["novel_id"], unique=False)
    op.create_index("ix_canon_space_artifacts_scope", TABLE, ["owner_id", "novel_id", "space"], unique=False)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table(TABLE):
        return
    op.drop_index("ix_canon_space_artifacts_scope", table_name=TABLE)
    op.drop_index("ix_canon_space_artifacts_novel_id", table_name=TABLE)
    op.drop_index("ix_canon_space_artifacts_owner_id", table_name=TABLE)
    op.drop_table(TABLE)
