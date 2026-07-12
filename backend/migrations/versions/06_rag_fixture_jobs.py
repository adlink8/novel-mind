"""rag fixture jobs, source snapshots, and eval cases

Revision ID: f6a0303ragfix
Revises: e5b8c20d4a73
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a0303ragfix"
down_revision: Union[str, Sequence[str], None] = "e5b8c20d4a73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_source_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column(
            "canonicalization_version",
            sa.String(length=64),
            nullable=False,
            server_default="rag-canon.v1",
        ),
        sa.Column("chunks", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="frozen"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "work_id", "snapshot_id", name="uq_rag_source_snapshots_scope"
        ),
        sa.UniqueConstraint("manifest_hash", name="uq_rag_source_snapshots_manifest"),
    )
    op.create_index(
        "idx_rag_source_snapshots_owner", "rag_source_snapshots", ["owner_id"]
    )
    op.create_index(
        "idx_rag_source_snapshots_work", "rag_source_snapshots", ["work_id"]
    )
    op.create_index(
        "idx_rag_source_snapshots_status", "rag_source_snapshots", ["status"]
    )

    op.create_table(
        "rag_fixture_jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_pk", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default="snapshot_ready",
        ),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("case_id", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column(
            "quality_comparable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("generator_lineage", sa.JSON(), nullable=True),
        sa.Column("judge_lineage", sa.JSON(), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_pk"], ["rag_source_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_rag_fixture_jobs_job_id"),
    )
    op.create_index("idx_rag_fixture_jobs_owner", "rag_fixture_jobs", ["owner_id"])
    op.create_index("idx_rag_fixture_jobs_status", "rag_fixture_jobs", ["status"])
    op.create_index(
        "idx_rag_fixture_jobs_snapshot", "rag_fixture_jobs", ["snapshot_pk"]
    )

    op.create_table(
        "rag_eval_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("work_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_pk", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=40),
            nullable=False,
            server_default="rag-quality.v1",
        ),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("case_type", sa.String(length=30), nullable=False),
        sa.Column("claims", sa.JSON(), nullable=False),
        sa.Column("equivalent_evidence_sets", sa.JSON(), nullable=False),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("generator_lineage", sa.JSON(), nullable=True),
        sa.Column("judge_lineage", sa.JSON(), nullable=True),
        sa.Column("judge_fixture_verdict", sa.JSON(), nullable=True),
        sa.Column("deterministic_checks", sa.JSON(), nullable=True),
        sa.Column("fixture_hash", sa.String(length=64), nullable=True),
        sa.Column("signature", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("parent_case_id", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("regeneration_reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["snapshot_pk"], ["rag_source_snapshots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id", "fixture_hash", name="uq_rag_eval_cases_case_hash"
        ),
    )
    op.create_index("idx_rag_eval_cases_case_id", "rag_eval_cases", ["case_id"])
    op.create_index("idx_rag_eval_cases_status", "rag_eval_cases", ["status"])
    op.create_index("idx_rag_eval_cases_snapshot", "rag_eval_cases", ["snapshot_pk"])
    op.create_index("idx_rag_eval_cases_owner", "rag_eval_cases", ["owner_id"])


def downgrade() -> None:
    op.drop_table("rag_eval_cases")
    op.drop_table("rag_fixture_jobs")
    op.drop_table("rag_source_snapshots")
