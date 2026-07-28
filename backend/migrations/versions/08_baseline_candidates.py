"""baseline_candidates prepare/commit + active_baselines pointer

Revision ID: 08baselinecand01
Revises: 07qualityruns01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "08baselinecand01"
down_revision: Union[str, Sequence[str], None] = "07qualityruns01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "baseline_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("quality_run_id", sa.Integer(), nullable=False),
        sa.Column("quality_run_job_id", sa.String(length=128), nullable=False),
        sa.Column("prepare_token", sa.String(length=64), nullable=False),
        sa.Column("prepare_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="prepared"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("chunker_name", sa.String(length=128), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.Column("chunker_config_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("run_status", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("report_signature", sa.String(length=128), nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=False),
        sa.Column("prepare_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("journal", sa.JSON(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["quality_run_id"], ["quality_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prepare_token", name="uq_baseline_candidates_prepare_token"),
    )
    op.create_index(
        "idx_baseline_candidates_owner", "baseline_candidates", ["owner_id"], unique=False
    )
    op.create_index(
        "idx_baseline_candidates_run",
        "baseline_candidates",
        ["quality_run_id"],
        unique=False,
    )
    op.create_index(
        "idx_baseline_candidates_owner_state",
        "baseline_candidates",
        ["owner_id", "state"],
        unique=False,
    )
    op.create_index(
        "idx_baseline_candidates_snapshot",
        "baseline_candidates",
        ["owner_id", "source_snapshot_hash"],
        unique=False,
    )

    op.create_table(
        "active_baselines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("quality_run_id", sa.Integer(), nullable=False),
        sa.Column("metrics_snapshot", sa.JSON(), nullable=False),
        sa.Column("chunker_name", sa.String(length=128), nullable=False),
        sa.Column("chunker_version", sa.String(length=64), nullable=False),
        sa.Column("chunker_config_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
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
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["baseline_candidates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["quality_run_id"], ["quality_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", name="uq_active_baselines_owner"),
    )
    op.create_index(
        "idx_active_baselines_candidate",
        "active_baselines",
        ["candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_active_baselines_candidate", table_name="active_baselines")
    op.drop_table("active_baselines")
    op.drop_index("idx_baseline_candidates_snapshot", table_name="baseline_candidates")
    op.drop_index("idx_baseline_candidates_owner_state", table_name="baseline_candidates")
    op.drop_index("idx_baseline_candidates_run", table_name="baseline_candidates")
    op.drop_index("idx_baseline_candidates_owner", table_name="baseline_candidates")
    op.drop_table("baseline_candidates")
