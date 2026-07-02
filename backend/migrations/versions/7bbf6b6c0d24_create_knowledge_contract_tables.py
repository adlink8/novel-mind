"""create knowledge graph contract tables

Revision ID: 7bbf6b6c0d24
Revises: 518675fa18f8
Create Date: 2026-07-02 06:55:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7bbf6b6c0d24"
down_revision: Union[str, Sequence[str], None] = "518675fa18f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create source-of-truth knowledge graph audit tables."""

    op.create_table(
        "knowledge_extraction_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_name", sa.String(length=200), nullable=False),
        sa.Column(
            "domain_profile",
            sa.String(length=20),
            nullable=False,
            server_default="fiction",
        ),
        sa.Column(
            "ontology_profile",
            sa.String(length=100),
            nullable=False,
            server_default="fiction.v1",
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="pending"
        ),
        sa.Column("prompt_version", sa.String(length=100), nullable=True),
        sa.Column(
            "candidate_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("judgment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_prompt_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_completion_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("latency_ms_p50", sa.Float(), nullable=True),
        sa.Column("latency_ms_p95", sa.Float(), nullable=True),
        sa.Column(
            "config_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "metrics_summary",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_runs_owner_id", "knowledge_extraction_runs", ["owner_id"]
    )
    op.create_index(
        "idx_knowledge_runs_novel_id", "knowledge_extraction_runs", ["novel_id"]
    )
    op.create_index(
        "idx_knowledge_runs_status", "knowledge_extraction_runs", ["status"]
    )
    op.create_index(
        "idx_knowledge_runs_domain_profile",
        "knowledge_extraction_runs",
        ["domain_profile"],
    )

    op.create_table(
        "knowledge_evidence_refs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("ref_key", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("text_chunk_id", sa.Integer(), nullable=True),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column("accepted_relation_id", sa.Integer(), nullable=True),
        sa.Column(
            "source_locator",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["text_chunk_id"], ["text_chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_evidence_owner_id", "knowledge_evidence_refs", ["owner_id"]
    )
    op.create_index(
        "idx_knowledge_evidence_novel_id", "knowledge_evidence_refs", ["novel_id"]
    )
    op.create_index(
        "idx_knowledge_evidence_run_id", "knowledge_evidence_refs", ["run_id"]
    )
    op.create_index(
        "idx_knowledge_evidence_ref_key",
        "knowledge_evidence_refs",
        ["run_id", "ref_key"],
        unique=True,
    )
    op.create_index(
        "idx_knowledge_evidence_source_type",
        "knowledge_evidence_refs",
        ["source_type"],
    )

    op.create_table(
        "knowledge_entity_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column(
            "aliases", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column(
            "domain_profile",
            sa.String(length=20),
            nullable=False,
            server_default="fiction",
        ),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column(
            "evidence_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "source_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="candidate"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_entities_owner_id",
        "knowledge_entity_candidates",
        ["owner_id"],
    )
    op.create_index(
        "idx_knowledge_entities_novel_id",
        "knowledge_entity_candidates",
        ["novel_id"],
    )
    op.create_index(
        "idx_knowledge_entities_run_id", "knowledge_entity_candidates", ["run_id"]
    )
    op.create_index(
        "idx_knowledge_entities_status", "knowledge_entity_candidates", ["status"]
    )
    op.create_index(
        "idx_knowledge_entities_domain_profile",
        "knowledge_entity_candidates",
        ["domain_profile"],
    )
    op.create_index(
        "idx_knowledge_entities_entity_type",
        "knowledge_entity_candidates",
        ["entity_type"],
    )

    op.create_table(
        "knowledge_event_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "domain_profile",
            sa.String(length=20),
            nullable=False,
            server_default="fiction",
        ),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column(
            "time_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "location_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "participant_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "evidence_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "source_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="candidate"
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_events_owner_id", "knowledge_event_candidates", ["owner_id"]
    )
    op.create_index(
        "idx_knowledge_events_novel_id", "knowledge_event_candidates", ["novel_id"]
    )
    op.create_index(
        "idx_knowledge_events_run_id", "knowledge_event_candidates", ["run_id"]
    )
    op.create_index(
        "idx_knowledge_events_status", "knowledge_event_candidates", ["status"]
    )
    op.create_index(
        "idx_knowledge_events_domain_profile",
        "knowledge_event_candidates",
        ["domain_profile"],
    )
    op.create_index(
        "idx_knowledge_events_event_type",
        "knowledge_event_candidates",
        ["event_type"],
    )

    op.create_table(
        "knowledge_relation_candidates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column(
            "domain_profile",
            sa.String(length=20),
            nullable=False,
            server_default="fiction",
        ),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column(
            "recall_signals",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "package_snapshot",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "evidence_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="candidate"
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_rel_candidates_owner_id",
        "knowledge_relation_candidates",
        ["owner_id"],
    )
    op.create_index(
        "idx_knowledge_rel_candidates_novel_id",
        "knowledge_relation_candidates",
        ["novel_id"],
    )
    op.create_index(
        "idx_knowledge_rel_candidates_run_id",
        "knowledge_relation_candidates",
        ["run_id"],
    )
    op.create_index(
        "idx_knowledge_rel_candidates_status",
        "knowledge_relation_candidates",
        ["status"],
    )
    op.create_index(
        "idx_knowledge_rel_candidates_relation_type",
        "knowledge_relation_candidates",
        ["relation_type"],
    )
    op.create_index(
        "idx_knowledge_rel_candidates_domain_profile",
        "knowledge_relation_candidates",
        ["domain_profile"],
    )

    op.create_table(
        "knowledge_relation_judgments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("relation_candidate_id", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "evidence_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "risk_flags",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "raw_output",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "structured_output",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "status", sa.String(length=30), nullable=False, server_default="pending"
        ),
        sa.Column(
            "gate_status",
            sa.String(length=40),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "gate_failures",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "needs_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["relation_candidate_id"],
            ["knowledge_relation_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_judgments_owner_id",
        "knowledge_relation_judgments",
        ["owner_id"],
    )
    op.create_index(
        "idx_knowledge_judgments_novel_id",
        "knowledge_relation_judgments",
        ["novel_id"],
    )
    op.create_index(
        "idx_knowledge_judgments_run_id", "knowledge_relation_judgments", ["run_id"]
    )
    op.create_index(
        "idx_knowledge_judgments_candidate_id",
        "knowledge_relation_judgments",
        ["relation_candidate_id"],
    )
    op.create_index(
        "idx_knowledge_judgments_status", "knowledge_relation_judgments", ["status"]
    )
    op.create_index(
        "idx_knowledge_judgments_gate_status",
        "knowledge_relation_judgments",
        ["gate_status"],
    )
    op.create_index(
        "idx_knowledge_judgments_relation_type",
        "knowledge_relation_judgments",
        ["relation_type"],
    )

    op.create_table(
        "knowledge_review_queue",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("relation_candidate_id", sa.Integer(), nullable=True),
        sa.Column("judgment_id", sa.Integer(), nullable=True),
        sa.Column("review_type", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="open"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence_refs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("resolution", sa.String(length=60), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("assigned_to", sa.String(length=120), nullable=True),
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
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["knowledge_extraction_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["relation_candidate_id"],
            ["knowledge_relation_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["judgment_id"], ["knowledge_relation_judgments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_knowledge_review_owner_id", "knowledge_review_queue", ["owner_id"]
    )
    op.create_index(
        "idx_knowledge_review_novel_id", "knowledge_review_queue", ["novel_id"]
    )
    op.create_index(
        "idx_knowledge_review_run_id", "knowledge_review_queue", ["run_id"]
    )
    op.create_index(
        "idx_knowledge_review_status", "knowledge_review_queue", ["status"]
    )
    op.create_index(
        "idx_knowledge_review_priority", "knowledge_review_queue", ["priority"]
    )


def downgrade() -> None:
    """Drop source-of-truth knowledge graph audit tables."""

    op.drop_table("knowledge_review_queue")
    op.drop_table("knowledge_relation_judgments")
    op.drop_table("knowledge_relation_candidates")
    op.drop_table("knowledge_event_candidates")
    op.drop_table("knowledge_entity_candidates")
    op.drop_table("knowledge_evidence_refs")
    op.drop_table("knowledge_extraction_runs")
