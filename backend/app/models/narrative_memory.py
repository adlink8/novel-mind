"""Candidate-only PostgreSQL authority for hierarchical narrative memory.

This module deliberately contains no execution lifecycle or production selector.
Phase 13 persists immutable candidate identity, content, provenance, seals, and
validation observations; later phases own building and consumption.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import JSONB, Base, TimestampMixin


MEMORY_NODE_KINDS = ("chapter_state", "story_arc", "volume", "global_story")
MEMORY_CLAIM_KINDS = (
    "entity_state",
    "event_fact",
    "relationship_delta",
    "clue_delta",
    "world_state_delta",
    "open_loop_delta",
)
MEMORY_EDGE_TYPES = ("contains", "derives_from")
MEMORY_SOURCE_KINDS = ("hierarchy", "timeline", "relationship", "clue")
MEMORY_UNCERTAINTY_LEVELS = ("certain", "likely", "uncertain", "unknown")
MEMORY_VALIDATION_VERDICTS = ("qualified_candidate", "blocked")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class NarrativeMemoryVersion(TimestampMixin, Base):
    """Immutable candidate identity and frozen input lineage."""

    __tablename__ = "narrative_memory_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "novel_id", "id", name="uq_memory_versions_scope"),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_key",
            name="uq_memory_versions_key",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "parent_version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_versions_parent_scope",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_memory_versions_snapshot_hash",
        ),
        CheckConstraint(
            "length(hierarchy_checksum) = 64",
            name="ck_memory_versions_hierarchy_checksum",
        ),
        CheckConstraint(
            "length(eligibility_report_checksum) = 64",
            name="ck_memory_versions_eligibility_checksum",
        ),
        CheckConstraint("length(prompt_hash) = 64", name="ck_memory_versions_prompt"),
        CheckConstraint("length(schema_hash) = 64", name="ck_memory_versions_schema"),
        CheckConstraint(
            "length(decoding_hash) = 64", name="ck_memory_versions_decoding"
        ),
        CheckConstraint("length(config_hash) = 64", name="ck_memory_versions_config"),
        CheckConstraint("length(policy_hash) = 64", name="ck_memory_versions_policy"),
        Index("idx_memory_versions_scope", "owner_id", "novel_id"),
        Index("idx_memory_versions_hierarchy", "hierarchy_build_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="RESTRICT"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(120), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    eligibility_report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decoding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    optional_source_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NarrativeMemoryNode(TimestampMixin, Base):
    """One immutable memory node in a candidate version."""

    __tablename__ = "narrative_memory_nodes"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "version_id", "id", name="uq_memory_nodes_scope"
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "node_key",
            name="uq_memory_nodes_key",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_nodes_version_scope",
        ),
        CheckConstraint(
            f"node_kind IN ({_quoted(MEMORY_NODE_KINDS)})",
            name="ck_memory_nodes_kind",
        ),
        CheckConstraint(
            "chapter_start > 0 AND chapter_end >= chapter_start",
            name="ck_memory_nodes_range",
        ),
        CheckConstraint(
            "node_kind <> 'chapter_state' OR chapter_start = chapter_end",
            name="ck_memory_nodes_chapter_singleton",
        ),
        CheckConstraint(
            "length(content_checksum) = 64", name="ck_memory_nodes_content_checksum"
        ),
        CheckConstraint(
            "length(model_lineage_checksum) = 64",
            name="ck_memory_nodes_lineage_checksum",
        ),
        Index("idx_memory_nodes_version_kind", "version_id", "node_kind"),
        Index(
            "idx_memory_nodes_version_range",
            "version_id",
            "chapter_start",
            "chapter_end",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    node_key: Mapped[str] = mapped_column(String(160), nullable=False)
    node_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    chapter_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_end: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label: Mapped[str | None] = mapped_column(Text, nullable=True)


class NarrativeMemoryClaim(TimestampMixin, Base):
    """One typed authoritative claim attached to a memory node."""

    __tablename__ = "narrative_memory_claims"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "id",
            name="uq_memory_claims_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "claim_key",
            name="uq_memory_claims_key",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_claims_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "node_id"],
            [
                "narrative_memory_nodes.owner_id",
                "narrative_memory_nodes.novel_id",
                "narrative_memory_nodes.version_id",
                "narrative_memory_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_claims_node_scope",
        ),
        CheckConstraint(
            f"claim_kind IN ({_quoted(MEMORY_CLAIM_KINDS)})",
            name="ck_memory_claims_kind",
        ),
        CheckConstraint(
            f"uncertainty IN ({_quoted(MEMORY_UNCERTAINTY_LEVELS)})",
            name="ck_memory_claims_uncertainty",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_claims_confidence",
        ),
        CheckConstraint("visible_from_chapter > 0", name="ck_memory_claims_visibility"),
        CheckConstraint(
            "length(claim_checksum) = 64", name="ck_memory_claims_checksum"
        ),
        CheckConstraint(
            "length(model_lineage_checksum) = 64",
            name="ck_memory_claims_lineage_checksum",
        ),
        Index("idx_memory_claims_version_node", "version_id", "node_id"),
        Index("idx_memory_claims_version_kind", "version_id", "claim_kind"),
        Index("idx_memory_claims_visibility", "version_id", "visible_from_chapter"),
        Index("idx_memory_claims_checksum", "claim_checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_key: Mapped[str] = mapped_column(String(180), nullable=False)
    claim_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    typed_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    uncertainty: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    visible_from_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeMemoryEdge(TimestampMixin, Base):
    """A scoped containment or derivation edge between memory nodes."""

    __tablename__ = "narrative_memory_edges"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_memory_edges_identity",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_edges_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "source_node_id"],
            [
                "narrative_memory_nodes.owner_id",
                "narrative_memory_nodes.novel_id",
                "narrative_memory_nodes.version_id",
                "narrative_memory_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_edges_source_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "target_node_id"],
            [
                "narrative_memory_nodes.owner_id",
                "narrative_memory_nodes.novel_id",
                "narrative_memory_nodes.version_id",
                "narrative_memory_nodes.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_edges_target_scope",
        ),
        CheckConstraint(
            "source_node_id <> target_node_id", name="ck_memory_edges_distinct"
        ),
        CheckConstraint(
            f"edge_type IN ({_quoted(MEMORY_EDGE_TYPES)})",
            name="ck_memory_edges_type",
        ),
        CheckConstraint("length(edge_checksum) = 64", name="ck_memory_edges_checksum"),
        CheckConstraint(
            "length(model_lineage_checksum) = 64",
            name="ck_memory_edges_lineage_checksum",
        ),
        Index("idx_memory_edges_source", "version_id", "source_node_id"),
        Index("idx_memory_edges_target", "version_id", "target_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_node_id: Mapped[int] = mapped_column(Integer, nullable=False)
    edge_type: Mapped[str] = mapped_column(String(32), nullable=False)
    edge_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeMemorySourceLink(TimestampMixin, Base):
    """Exact claim-to-Phase-07 evidence leaf provenance."""

    __tablename__ = "narrative_memory_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_links_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "claim_id"],
            [
                "narrative_memory_claims.owner_id",
                "narrative_memory_claims.novel_id",
                "narrative_memory_claims.version_id",
                "narrative_memory_claims.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_links_claim_scope",
        ),
        ForeignKeyConstraint(
            ["hierarchy_build_id", "evidence_node_id"],
            ["chunk_hierarchy_nodes.build_id", "chunk_hierarchy_nodes.node_id"],
            ondelete="RESTRICT",
            name="fk_memory_links_evidence_leaf",
        ),
        ForeignKeyConstraint(
            ["chapter_id"],
            ["chapters.id"],
            ondelete="RESTRICT",
            name="fk_memory_links_chapter",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "claim_id",
            "hierarchy_build_id",
            "evidence_node_id",
            "source_start",
            "source_end",
            name="uq_memory_links_identity",
        ),
        CheckConstraint(
            f"source_kind IN ({_quoted(MEMORY_SOURCE_KINDS)})",
            name="ck_memory_links_source_kind",
        ),
        CheckConstraint("chapter_number > 0", name="ck_memory_links_chapter_number"),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_memory_links_offsets",
        ),
        CheckConstraint(
            "length(content_hash) = 64", name="ck_memory_links_content_hash"
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_memory_links_snapshot_hash",
        ),
        CheckConstraint("length(link_checksum) = 64", name="ck_memory_links_checksum"),
        CheckConstraint(
            "length(model_lineage_checksum) = 64",
            name="ck_memory_links_lineage_checksum",
        ),
        Index("idx_memory_links_claim", "version_id", "claim_id"),
        Index("idx_memory_links_evidence", "hierarchy_build_id", "evidence_node_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    optional_source_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    link_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class NarrativeMemoryManifest(TimestampMixin, Base):
    """One immutable database-derived seal per candidate version."""

    __tablename__ = "narrative_memory_manifests"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "version_id", name="uq_memory_manifests_version"
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "manifest_checksum",
            name="uq_memory_manifests_scope_checksum",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_manifests_version_scope",
        ),
        CheckConstraint(
            "length(manifest_checksum) = 64", name="ck_memory_manifests_checksum"
        ),
        Index("idx_memory_manifests_checksum", "manifest_checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    component_counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    component_hashes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NarrativeMemoryValidationReport(TimestampMixin, Base):
    """Append-only structural validation observation bound to a seal."""

    __tablename__ = "narrative_memory_validation_reports"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "report_checksum",
            name="uq_memory_reports_scope_checksum",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_memory_reports_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id", "manifest_checksum"],
            [
                "narrative_memory_manifests.owner_id",
                "narrative_memory_manifests.novel_id",
                "narrative_memory_manifests.version_id",
                "narrative_memory_manifests.manifest_checksum",
            ],
            ondelete="RESTRICT",
            name="fk_memory_reports_manifest_scope",
        ),
        CheckConstraint(
            f"verdict IN ({_quoted(MEMORY_VALIDATION_VERDICTS)})",
            name="ck_memory_reports_verdict",
        ),
        CheckConstraint(
            "length(report_checksum) = 64", name="ck_memory_reports_checksum"
        ),
        Index("idx_memory_reports_version", "version_id"),
        Index("idx_memory_reports_manifest", "manifest_checksum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    validator_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    observed_counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    report_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
