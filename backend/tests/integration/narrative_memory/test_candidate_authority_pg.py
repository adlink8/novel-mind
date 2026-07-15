"""Phase 13 candidate-only narrative-memory PostgreSQL authority."""

from __future__ import annotations

import pytest
from sqlalchemy import ForeignKeyConstraint

from app import models


pytestmark = pytest.mark.integration


AUTHORITY_TABLES = {
    "narrative_memory_versions",
    "narrative_memory_nodes",
    "narrative_memory_claims",
    "narrative_memory_edges",
    "narrative_memory_source_links",
    "narrative_memory_manifests",
    "narrative_memory_validation_reports",
}

FORBIDDEN_TABLE_FRAGMENTS = {
    "run",
    "stage",
    "checkpoint",
    "active_pointer",
    "promotion",
    "rollback",
    "provider",
}


def test_candidate_authority_metadata_exports_exactly_seven_sidecar_tables():
    metadata_names = {
        name for name in models.Base.metadata.tables if name.startswith("narrative_memory_")
    }

    assert metadata_names == AUTHORITY_TABLES
    assert all(
        hasattr(models, exported)
        for exported in (
            "NarrativeMemoryVersion",
            "NarrativeMemoryNode",
            "NarrativeMemoryClaim",
            "NarrativeMemoryEdge",
            "NarrativeMemorySourceLink",
            "NarrativeMemoryManifest",
            "NarrativeMemoryValidationReport",
        )
    )
    assert not any(
        fragment in table_name
        for table_name in metadata_names
        for fragment in FORBIDDEN_TABLE_FRAGMENTS
    )


def test_every_content_table_repeats_owner_novel_and_version_scope():
    for table_name in AUTHORITY_TABLES - {"narrative_memory_versions"}:
        table = models.Base.metadata.tables[table_name]
        columns = table.c
        assert {"owner_id", "novel_id", "version_id"} <= set(columns.keys())

        scoped_version_fks = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint)
            and [column.name for column in constraint.columns]
            == ["owner_id", "novel_id", "version_id"]
            and [element.target_fullname for element in constraint.elements]
            == [
                "narrative_memory_versions.owner_id",
                "narrative_memory_versions.novel_id",
                "narrative_memory_versions.id",
            ]
        ]

        assert len(scoped_version_fks) == 1
        assert scoped_version_fks[0].ondelete == "RESTRICT"


def test_version_has_frozen_lineage_and_no_mutable_lifecycle_status():
    columns = models.Base.metadata.tables["narrative_memory_versions"].c

    assert {
        "source_snapshot_hash",
        "hierarchy_build_id",
        "hierarchy_checksum",
        "eligibility_policy_version",
        "eligibility_report_checksum",
        "prompt_hash",
        "schema_hash",
        "model_lineage",
        "decoding_hash",
        "config_hash",
        "policy_hash",
    } <= set(columns.keys())
    assert "status" not in columns
    assert "is_active" not in columns
    assert "published" not in columns
