"""create narrative unit source-of-truth tables

Revision ID: d4a7f19c2b61
Revises: 7bbf6b6c0d24
Create Date: 2026-07-11 14:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a7f19c2b61"
down_revision: Union[str, Sequence[str], None] = "7bbf6b6c0d24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    """Create immutable snapshots and narrative publication truth tables."""

    # Scoped unique keys are the PostgreSQL targets for every composite
    # owner/work lineage FK below.
    op.create_index(
        "uq_knowledge_judgments_owner_novel_id",
        "knowledge_relation_judgments",
        ["owner_id", "novel_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_knowledge_candidates_owner_novel_id",
        "knowledge_relation_candidates",
        ["owner_id", "novel_id", "id"],
        unique=True,
    )
    op.create_index(
        "uq_knowledge_evidence_owner_novel_id",
        "knowledge_evidence_refs",
        ["owner_id", "novel_id", "id"],
        unique=True,
    )

    op.create_table(
        "narrative_source_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(length=20), nullable=False),
        sa.Column("ontology_profile", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_watermark", sa.String(length=160), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "item_count > 0", name="ck_narrative_snapshots_nonempty"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "novel_id",
            "domain_profile",
            "manifest_checksum",
            name="uq_narrative_snapshots_manifest",
        ),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "id", name="uq_narrative_snapshots_scope"
        ),
    )
    op.create_index(
        "idx_narrative_snapshots_owner_id",
        "narrative_source_snapshots",
        ["owner_id"],
    )
    op.create_index(
        "idx_narrative_snapshots_novel_id",
        "narrative_source_snapshots",
        ["novel_id"],
    )
    op.create_index(
        "idx_narrative_snapshots_status",
        "narrative_source_snapshots",
        ["status"],
    )
    op.create_index(
        "idx_narrative_snapshots_owner_novel_domain",
        "narrative_source_snapshots",
        ["owner_id", "novel_id", "domain_profile"],
    )
    op.create_index(
        "idx_narrative_snapshots_manifest",
        "narrative_source_snapshots",
        ["manifest_checksum"],
    )

    op.create_table(
        "narrative_source_snapshot_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("source_judgment_id", sa.Integer(), nullable=False),
        sa.Column("source_candidate_id", sa.Integer(), nullable=False),
        sa.Column("judgment_content_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_content_hash", sa.String(length=64), nullable=False),
        sa.Column("item_content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_manifest", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            name="fk_snapshot_items_snapshot_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_judgment_id"],
            [
                "knowledge_relation_judgments.owner_id",
                "knowledge_relation_judgments.novel_id",
                "knowledge_relation_judgments.id",
            ],
            name="fk_snapshot_items_judgment_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_candidate_id"],
            [
                "knowledge_relation_candidates.owner_id",
                "knowledge_relation_candidates.novel_id",
                "knowledge_relation_candidates.id",
            ],
            name="fk_snapshot_items_candidate_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "source_judgment_id",
            name="uq_narrative_snapshot_judgment",
        ),
    )
    op.create_index(
        "idx_narrative_snapshot_items_snapshot_id",
        "narrative_source_snapshot_items",
        ["snapshot_id"],
    )
    op.create_index(
        "idx_narrative_snapshot_items_judgment_id",
        "narrative_source_snapshot_items",
        ["source_judgment_id"],
    )

    op.create_table(
        "narrative_units",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("source_judgment_id", sa.Integer(), nullable=False),
        sa.Column("source_candidate_id", sa.Integer(), nullable=False),
        sa.Column("primary_evidence_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(length=20), nullable=False),
        sa.Column("ontology_profile", sa.String(length=100), nullable=False),
        sa.Column("unit_stage", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=30), nullable=False),
        sa.Column("canonical_id", sa.String(length=96), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject_key", sa.String(length=240), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence_manifest_checksum", sa.String(length=64), nullable=False
        ),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("schema_hash", sa.String(length=64), nullable=True),
        sa.Column("model_hash", sa.String(length=64), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_narrative_units_confidence",
        ),
        sa.CheckConstraint(
            "evidence_count > 0", name="ck_narrative_units_evidence_nonempty"
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_narrative_units_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            name="fk_narrative_units_snapshot_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_judgment_id"],
            [
                "knowledge_relation_judgments.owner_id",
                "knowledge_relation_judgments.novel_id",
                "knowledge_relation_judgments.id",
            ],
            name="fk_narrative_units_judgment_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_candidate_id"],
            [
                "knowledge_relation_candidates.owner_id",
                "knowledge_relation_candidates.novel_id",
                "knowledge_relation_candidates.id",
            ],
            name="fk_narrative_units_candidate_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "primary_evidence_id"],
            [
                "knowledge_evidence_refs.owner_id",
                "knowledge_evidence_refs.novel_id",
                "knowledge_evidence_refs.id",
            ],
            name="fk_narrative_units_primary_evidence_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "id", name="uq_narrative_units_scope"
        ),
        sa.UniqueConstraint(
            "owner_id",
            "novel_id",
            "canonical_id",
            "version",
            name="uq_narrative_units_canonical_version",
        ),
    )
    for name, columns in (
        ("idx_narrative_units_owner_id", ["owner_id"]),
        ("idx_narrative_units_novel_id", ["novel_id"]),
        ("idx_narrative_units_snapshot_id", ["source_snapshot_id"]),
        ("idx_narrative_units_status", ["status"]),
        ("idx_narrative_units_lifecycle", ["lifecycle_status"]),
        ("idx_narrative_units_canonical_id", ["canonical_id"]),
        (
            "idx_narrative_units_owner_novel_status",
            ["owner_id", "novel_id", "status"],
        ),
    ):
        op.create_index(name, "narrative_units", columns)

    op.create_table(
        "narrative_unit_evidence_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=False),
        sa.Column("source_evidence_id", sa.Integer(), nullable=False),
        sa.Column("ref_key", sa.String(length=120), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "unit_id"],
            [
                "narrative_units.owner_id",
                "narrative_units.novel_id",
                "narrative_units.id",
            ],
            name="fk_unit_evidence_unit_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_evidence_id"],
            [
                "knowledge_evidence_refs.owner_id",
                "knowledge_evidence_refs.novel_id",
                "knowledge_evidence_refs.id",
            ],
            name="fk_unit_evidence_source_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "unit_id", "source_evidence_id", name="uq_narrative_unit_evidence"
        ),
    )
    op.create_index(
        "idx_narrative_unit_evidence_unit_id",
        "narrative_unit_evidence_links",
        ["unit_id"],
    )
    op.create_index(
        "idx_narrative_unit_evidence_source_id",
        "narrative_unit_evidence_links",
        ["source_evidence_id"],
    )

    op.create_table(
        "narrative_index_builds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("source_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(length=20), nullable=False),
        sa.Column("build_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("collection_name", sa.String(length=200), nullable=True),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("config_checksum", sa.String(length=64), nullable=False),
        sa.Column("unit_count", sa.Integer(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "unit_count >= 0", name="ck_narrative_builds_unit_count"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_snapshot_id"],
            [
                "narrative_source_snapshots.owner_id",
                "narrative_source_snapshots.novel_id",
                "narrative_source_snapshots.id",
            ],
            name="fk_narrative_builds_snapshot_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "id", name="uq_narrative_builds_scope"
        ),
        sa.UniqueConstraint(
            "owner_id", "novel_id", "build_key", name="uq_narrative_builds_key"
        ),
    )
    for name, columns in (
        ("idx_narrative_builds_owner_id", ["owner_id"]),
        ("idx_narrative_builds_novel_id", ["novel_id"]),
        ("idx_narrative_builds_status", ["status"]),
        ("idx_narrative_builds_manifest", ["manifest_checksum"]),
        (
            "idx_narrative_builds_owner_novel_status",
            ["owner_id", "novel_id", "status"],
        ),
    ):
        op.create_index(name, "narrative_index_builds", columns)

    op.create_table(
        "narrative_active_pointers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(length=20), nullable=False),
        sa.Column("build_id", sa.Integer(), nullable=False),
        sa.Column("pointer_version", sa.Integer(), nullable=False),
        sa.Column("active_manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "pointer_version > 0", name="ck_narrative_pointer_version"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            name="fk_narrative_active_pointer_build_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "novel_id",
            "domain_profile",
            name="uq_narrative_active_pointer_scope",
        ),
    )
    op.create_index(
        "idx_narrative_active_pointers_owner_id",
        "narrative_active_pointers",
        ["owner_id"],
    )
    op.create_index(
        "idx_narrative_active_pointers_novel_id",
        "narrative_active_pointers",
        ["novel_id"],
    )
    op.create_index(
        "idx_narrative_active_pointers_build_id",
        "narrative_active_pointers",
        ["build_id"],
    )

    op.create_table(
        "narrative_promotion_journals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("domain_profile", sa.String(length=20), nullable=False),
        sa.Column("transaction_key", sa.String(length=120), nullable=False),
        sa.Column("candidate_build_id", sa.Integer(), nullable=False),
        sa.Column("previous_build_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("candidate_checksum", sa.String(length=64), nullable=False),
        sa.Column("previous_checksum", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "candidate_build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            name="fk_narrative_promotion_candidate_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "novel_id", "previous_build_id"],
            [
                "narrative_index_builds.owner_id",
                "narrative_index_builds.novel_id",
                "narrative_index_builds.id",
            ],
            name="fk_narrative_promotion_previous_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_key", name="uq_narrative_promotion_transaction"
        ),
    )
    op.create_index(
        "idx_narrative_promotion_owner_id",
        "narrative_promotion_journals",
        ["owner_id"],
    )
    op.create_index(
        "idx_narrative_promotion_novel_id",
        "narrative_promotion_journals",
        ["novel_id"],
    )
    op.create_index(
        "idx_narrative_promotion_status",
        "narrative_promotion_journals",
        ["status"],
    )
    op.create_index(
        "idx_narrative_promotion_candidate_id",
        "narrative_promotion_journals",
        ["candidate_build_id"],
    )


def downgrade() -> None:
    """Drop narrative publication truth tables in dependency order."""

    op.drop_table("narrative_promotion_journals")
    op.drop_table("narrative_active_pointers")
    op.drop_table("narrative_index_builds")
    op.drop_table("narrative_unit_evidence_links")
    op.drop_table("narrative_units")
    op.drop_table("narrative_source_snapshot_items")
    op.drop_table("narrative_source_snapshots")
    op.drop_index(
        "uq_knowledge_evidence_owner_novel_id",
        table_name="knowledge_evidence_refs",
    )
    op.drop_index(
        "uq_knowledge_candidates_owner_novel_id",
        table_name="knowledge_relation_candidates",
    )
    op.drop_index(
        "uq_knowledge_judgments_owner_novel_id",
        table_name="knowledge_relation_judgments",
    )
