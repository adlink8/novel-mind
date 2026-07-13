"""chunk builds, active pointers, hierarchy nodes (Phase 07 PG wiring)

Revision ID: 09chunkhier01
Revises: 08baselinecand01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "09chunkhier01"
down_revision: Union[str, Sequence[str], None] = "08baselinecand01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunk_builds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("build_id", sa.String(length=64), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="built"),
        sa.Column("parent_build_id", sa.String(length=64), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("chunker_name", sa.String(length=128), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.Column("chunker_config_hash", sa.String(length=64), nullable=False),
        sa.Column("collection_name", sa.String(length=128), nullable=False),
        sa.Column("is_candidate", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("immutable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("changed_chapter_ids", sa.JSON(), nullable=False),
        sa.Column("journal", sa.JSON(), nullable=False),
        sa.Column("vector_ids", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("build_id", name="uq_chunk_builds_build_id"),
    )
    op.create_index("idx_chunk_builds_novel", "chunk_builds", ["novel_id"])
    op.create_index(
        "idx_chunk_builds_novel_status", "chunk_builds", ["novel_id", "status"]
    )

    op.create_table(
        "chunk_active_pointers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.String(length=64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("novel_id", name="uq_chunk_active_pointers_novel"),
    )

    op.create_table(
        "chunk_hierarchy_nodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("build_id", sa.String(length=64), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_id", sa.String(length=64), nullable=True),
        sa.Column("child_ids", sa.JSON(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_type", sa.String(length=50), nullable=False, server_default="paragraph"),
        sa.Column("decision_lineage", sa.JSON(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text_chunk_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["text_chunk_id"], ["text_chunks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "build_id", "node_id", name="uq_chunk_hierarchy_nodes_build_node"
        ),
    )
    op.create_index(
        "idx_chunk_hierarchy_novel_build",
        "chunk_hierarchy_nodes",
        ["novel_id", "build_id"],
    )
    op.create_index(
        "idx_chunk_hierarchy_chapter",
        "chunk_hierarchy_nodes",
        ["build_id", "chapter_id"],
    )
    op.create_index(
        "idx_chunk_hierarchy_parent",
        "chunk_hierarchy_nodes",
        ["build_id", "parent_id"],
    )
    op.create_index(
        "idx_chunk_hierarchy_level",
        "chunk_hierarchy_nodes",
        ["build_id", "level"],
    )

    # Optional lineage columns on text_chunks (nullable; legacy rows remain raw)
    op.add_column(
        "text_chunks",
        sa.Column("hierarchy_node_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "text_chunks",
        sa.Column("hierarchy_level", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "text_chunks",
        sa.Column("hierarchy_parent_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "text_chunks",
        sa.Column("hierarchy_build_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "text_chunks",
        sa.Column("source_start", sa.Integer(), nullable=True),
    )
    op.add_column(
        "text_chunks",
        sa.Column("source_end", sa.Integer(), nullable=True),
    )
    op.create_index(
        "idx_text_chunks_hierarchy_node",
        "text_chunks",
        ["hierarchy_build_id", "hierarchy_node_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_text_chunks_hierarchy_node", table_name="text_chunks")
    op.drop_column("text_chunks", "source_end")
    op.drop_column("text_chunks", "source_start")
    op.drop_column("text_chunks", "hierarchy_build_id")
    op.drop_column("text_chunks", "hierarchy_parent_id")
    op.drop_column("text_chunks", "hierarchy_level")
    op.drop_column("text_chunks", "hierarchy_node_id")

    op.drop_index("idx_chunk_hierarchy_level", table_name="chunk_hierarchy_nodes")
    op.drop_index("idx_chunk_hierarchy_parent", table_name="chunk_hierarchy_nodes")
    op.drop_index("idx_chunk_hierarchy_chapter", table_name="chunk_hierarchy_nodes")
    op.drop_index("idx_chunk_hierarchy_novel_build", table_name="chunk_hierarchy_nodes")
    op.drop_table("chunk_hierarchy_nodes")

    op.drop_table("chunk_active_pointers")

    op.drop_index("idx_chunk_builds_novel_status", table_name="chunk_builds")
    op.drop_index("idx_chunk_builds_novel", table_name="chunk_builds")
    op.drop_table("chunk_builds")
