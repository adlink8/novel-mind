"""quality_runs durable repository + five-tuple lineage

Revision ID: 07qualityruns01
Revises: f6a0303ragfix
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "07qualityruns01"
down_revision: Union[str, Sequence[str], None] = "f6a0303ragfix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quality_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("lease_id", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("stage_cache", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("report_signature", sa.String(length=128), nullable=True),
        sa.Column("chunker_name", sa.String(length=128), nullable=True),
        sa.Column("chunker_version", sa.String(length=64), nullable=True),
        sa.Column("chunker_config_hash", sa.String(length=64), nullable=True),
        sa.Column("chunk_manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "quality_comparable", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("incomparable_reason", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["novels.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_quality_runs_job_id"),
        sa.CheckConstraint(
            "(quality_comparable = false) OR ("
            "chunker_name IS NOT NULL AND length(trim(chunker_name)) > 0 AND "
            "chunker_version IS NOT NULL AND length(trim(chunker_version)) > 0 AND "
            "chunker_config_hash IS NOT NULL AND length(chunker_config_hash) = 64 AND "
            "chunk_manifest_hash IS NOT NULL AND length(chunk_manifest_hash) = 64 AND "
            "source_snapshot_hash IS NOT NULL AND length(source_snapshot_hash) = 64"
            ")",
            name="ck_quality_runs_comparable_requires_lineage",
        ),
    )
    op.create_index("idx_quality_runs_owner", "quality_runs", ["owner_id"], unique=False)
    op.create_index("idx_quality_runs_status", "quality_runs", ["status"], unique=False)
    op.create_index(
        "idx_quality_runs_owner_status",
        "quality_runs",
        ["owner_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_quality_runs_lease_expires",
        "quality_runs",
        ["lease_expires_at"],
        unique=False,
    )
    # Fail-closed: do not invent lineage for any pre-existing conceptual jobs.
    # quality_runs is a new table; any future backfill must set
    # quality_comparable=false and incomparable_reason='legacy_incomparable'.


def downgrade() -> None:
    op.drop_index("idx_quality_runs_lease_expires", table_name="quality_runs")
    op.drop_index("idx_quality_runs_owner_status", table_name="quality_runs")
    op.drop_index("idx_quality_runs_status", table_name="quality_runs")
    op.drop_index("idx_quality_runs_owner", table_name="quality_runs")
    op.drop_table("quality_runs")
