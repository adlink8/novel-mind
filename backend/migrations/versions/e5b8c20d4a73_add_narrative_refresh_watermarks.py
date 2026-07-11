"""add narrative refresh watermarks

Revision ID: e5b8c20d4a73
Revises: d4a7f19c2b61
"""

from alembic import op
import sqlalchemy as sa

revision = "e5b8c20d4a73"
down_revision = "d4a7f19c2b61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "narrative_source_watermarks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(20), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("build_id", sa.Integer(), nullable=False),
        sa.Column("source_watermark", sa.String(160), nullable=False),
        sa.Column("manifest_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_id", "novel_id", "domain_profile", name="uq_narrative_watermark_scope"),
        sa.ForeignKeyConstraint(["owner_id", "novel_id", "snapshot_id"], ["narrative_source_snapshots.owner_id", "narrative_source_snapshots.novel_id", "narrative_source_snapshots.id"], ondelete="RESTRICT", name="fk_narrative_watermark_snapshot_scope"),
        sa.ForeignKeyConstraint(["owner_id", "novel_id", "build_id"], ["narrative_index_builds.owner_id", "narrative_index_builds.novel_id", "narrative_index_builds.id"], ondelete="RESTRICT", name="fk_narrative_watermark_build_scope"),
    )
    op.create_index("idx_narrative_watermark_build_id", "narrative_source_watermarks", ["build_id"])
    op.create_table(
        "narrative_refresh_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(20), nullable=False),
        sa.Column("run_key", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("before_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("after_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("candidate_build_id", sa.Integer(), nullable=True),
        sa.Column("delta_manifest", sa.JSON(), nullable=False),
        sa.Column("affected_subjects", sa.JSON(), nullable=False),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_key", name="uq_narrative_refresh_run_key"),
    )
    op.create_index("idx_narrative_refresh_owner_novel", "narrative_refresh_runs", ["owner_id", "novel_id"])
    op.create_index("idx_narrative_refresh_status", "narrative_refresh_runs", ["status"])


def downgrade() -> None:
    op.drop_table("narrative_refresh_runs")
    op.drop_table("narrative_source_watermarks")
