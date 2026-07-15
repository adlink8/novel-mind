"""Phase 13 candidate-only narrative-memory PostgreSQL authority."""

from __future__ import annotations

import pytest
from sqlalchemy import ForeignKeyConstraint, create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import models
from tests.integration.conftest import run_alembic


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

HEX_A = "a" * 64
HEX_B = "b" * 64


def _seed_candidate(engine, *, seal: bool = False) -> dict[str, int | str]:
    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('memory-owner','memory@example.com','x',true,false) RETURNING id"
            )
        ).scalar_one()
        other_owner_id = conn.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('memory-other','other-memory@example.com','x',true,false) "
                "RETURNING id"
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:owner,'Memory Novel','ready',1,10) RETURNING id"
            ),
            {"owner": owner_id},
        ).scalar_one()
        other_novel_id = conn.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:owner,'Other Novel','ready',0,0) RETURNING id"
            ),
            {"owner": other_owner_id},
        ).scalar_one()
        chapter_id = conn.execute(
            text(
                "INSERT INTO chapters (novel_id,chapter_number,title,content,word_count) "
                "VALUES (:novel,1,'One','abcdefghij',10) RETURNING id"
            ),
            {"novel": novel_id},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO chunk_builds (
                    build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                    chunker_name,chunker_version,chunker_config_hash,collection_name,
                    is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                ) VALUES (
                    'memory-build-1',:novel,'committed',:hash,:hash,
                    'semantic','1',:hash,'memory',false,true,'[]','[]','[]'
                )
                """
            ),
            {"novel": novel_id, "hash": HEX_A},
        )
        conn.execute(
            text(
                """
                INSERT INTO chunk_hierarchy_nodes (
                    build_id,novel_id,node_id,level,chapter_id,chapter_number,
                    parent_id,child_ids,content,content_hash,source_start,source_end,
                    chunk_type,decision_lineage,order_index
                ) VALUES
                    ('memory-build-1',:novel,'leaf-1','evidence',:chapter,1,
                     NULL,'[]','abc',:hash,0,3,'paragraph','[]',0),
                    ('memory-build-1',:novel,'scene-1','scene',:chapter,1,
                     NULL,'[]','abcdefghij',:hash,0,10,'scene','[]',1)
                """
            ),
            {"novel": novel_id, "chapter": chapter_id, "hash": HEX_A},
        )
        version_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_versions (
                    owner_id,novel_id,version_key,source_snapshot_hash,
                    hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                    eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                    decoding_hash,config_hash,policy_hash,optional_source_lineage
                ) VALUES (
                    :owner,:novel,'memory-v1',:hash,'memory-build-1',:hash,'audit-v1',
                    :hash,:hash,:hash,'{}',:hash,:hash,:hash,'{}'
                ) RETURNING id
                """
            ),
            {"owner": owner_id, "novel": novel_id, "hash": HEX_A},
        ).scalar_one()

        def add_node(key: str, kind: str, start: int, end: int) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_nodes (
                        owner_id,novel_id,version_id,node_key,node_kind,
                        chapter_start,chapter_end,schema_version,content_checksum,
                        model_lineage_checksum
                    ) VALUES (
                        :owner,:novel,:version,:key,:kind,:start,:end,'v1',:hash,:hash
                    ) RETURNING id
                    """
                ),
                {
                    "owner": owner_id,
                    "novel": novel_id,
                    "version": version_id,
                    "key": key,
                    "kind": kind,
                    "start": start,
                    "end": end,
                    "hash": HEX_A,
                },
            ).scalar_one()

        global_id = add_node("global", "global_story", 1, 3)
        arc_id = add_node("arc", "story_arc", 1, 3)
        arc_peer_id = add_node("arc-peer", "volume", 1, 3)
        chapter_node_id = add_node("chapter-1", "chapter_state", 1, 1)
        claim_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_claims (
                    owner_id,novel_id,version_id,node_id,claim_key,claim_kind,
                    schema_version,typed_payload,uncertainty,confidence,
                    visible_from_chapter,claim_checksum,model_lineage_checksum
                ) VALUES (
                    :owner,:novel,:version,:node,'claim-1','event_fact','v1','{}',
                    'certain',1.0,1,:hash,:hash
                ) RETURNING id
                """
            ),
            {
                "owner": owner_id,
                "novel": novel_id,
                "version": version_id,
                "node": chapter_node_id,
                "hash": HEX_A,
            },
        ).scalar_one()
        edge_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_edges (
                    owner_id,novel_id,version_id,source_node_id,target_node_id,
                    edge_type,edge_checksum,model_lineage_checksum
                ) VALUES (:owner,:novel,:version,:source,:target,'contains',:hash,:hash)
                RETURNING id
                """
            ),
            {
                "owner": owner_id,
                "novel": novel_id,
                "version": version_id,
                "source": global_id,
                "target": arc_id,
                "hash": HEX_A,
            },
        ).scalar_one()
        link_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_source_links (
                    owner_id,novel_id,version_id,claim_id,source_kind,
                    hierarchy_build_id,evidence_node_id,chapter_id,chapter_number,
                    source_start,source_end,content_hash,source_snapshot_hash,
                    link_checksum,model_lineage_checksum
                ) VALUES (
                    :owner,:novel,:version,:claim,'hierarchy','memory-build-1','leaf-1',
                    :chapter,1,0,3,:hash,:hash,:hash,:hash
                ) RETURNING id
                """
            ),
            {
                "owner": owner_id,
                "novel": novel_id,
                "version": version_id,
                "claim": claim_id,
                "chapter": chapter_id,
                "hash": HEX_A,
            },
        ).scalar_one()
        manifest_id = report_id = None
        if seal:
            manifest_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_manifests (
                        owner_id,novel_id,version_id,manifest_schema_version,
                        component_counts,component_hashes,manifest_checksum
                    ) VALUES (:owner,:novel,:version,'v1','{}','{}',:hash)
                    RETURNING id
                    """
                ),
                {
                    "owner": owner_id,
                    "novel": novel_id,
                    "version": version_id,
                    "hash": HEX_A,
                },
            ).scalar_one()
            report_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_validation_reports (
                        owner_id,novel_id,version_id,manifest_checksum,
                        validator_version,policy_version,verdict,reason_codes,
                        observed_counts,report_checksum
                    ) VALUES (
                        :owner,:novel,:version,:hash,'validator-v1','policy-v1',
                        'qualified_candidate','[]','{}',:hash
                    ) RETURNING id
                    """
                ),
                {
                    "owner": owner_id,
                    "novel": novel_id,
                    "version": version_id,
                    "hash": HEX_A,
                },
            ).scalar_one()
    return {
        "owner_id": owner_id,
        "other_owner_id": other_owner_id,
        "novel_id": novel_id,
        "other_novel_id": other_novel_id,
        "chapter_id": chapter_id,
        "version_id": version_id,
        "global_id": global_id,
        "arc_id": arc_id,
        "arc_peer_id": arc_peer_id,
        "chapter_node_id": chapter_node_id,
        "claim_id": claim_id,
        "edge_id": edge_id,
        "link_id": link_id,
        "manifest_id": manifest_id or 0,
        "report_id": report_id or 0,
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


def test_migration_from_clue_head_creates_only_candidate_authority(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "11cluetrack01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        assert not (AUTHORITY_TABLES & set(inspect(conn).get_table_names()))
    engine.dispose()


def test_migration_roundtrip_removes_functions_triggers_and_tables(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    run_alembic("downgrade", "11cluetrack01", database_url=empty_postgres)

    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        assert not (AUTHORITY_TABLES & set(inspect(conn).get_table_names()))
        functions = conn.execute(
            text(
                "SELECT proname FROM pg_proc "
                "WHERE proname LIKE 'narrative_memory_%_guard'"
            )
        ).scalars()
        assert list(functions) == []
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    assert "13memoryauth01" in (current.stdout + current.stderr)


def test_scope_fks_version_owner_and_source_leaf_closure_fail_closed(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_candidate(engine)

    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_versions (
                    owner_id,novel_id,version_key,source_snapshot_hash,
                    hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                    eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                    decoding_hash,config_hash,policy_hash,optional_source_lineage
                ) VALUES (
                    :owner,:novel,'wrong-owner',:hash,'memory-build-1',:hash,'v1',
                    :hash,:hash,:hash,'{}',:hash,:hash,:hash,'{}'
                )
                """
            ),
            {
                "owner": ids["other_owner_id"],
                "novel": ids["novel_id"],
                "hash": HEX_A,
            },
        )
    assert "memory_version_scope_mismatch" in str(exc.value)

    with engine.begin() as conn, pytest.raises((IntegrityError, DBAPIError)):
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_nodes (
                    owner_id,novel_id,version_id,node_key,node_kind,chapter_start,
                    chapter_end,schema_version,content_checksum,model_lineage_checksum
                ) VALUES (:owner,:novel,:version,'cross-novel','chapter_state',1,1,
                          'v1',:hash,:hash)
                """
            ),
            {
                "owner": ids["owner_id"],
                "novel": ids["other_novel_id"],
                "version": ids["version_id"],
                "hash": HEX_A,
            },
        )

    link_sql = """
        INSERT INTO narrative_memory_source_links (
            owner_id,novel_id,version_id,claim_id,source_kind,hierarchy_build_id,
            evidence_node_id,chapter_id,chapter_number,source_start,source_end,
            content_hash,source_snapshot_hash,link_checksum,model_lineage_checksum
        ) VALUES (
            :owner,:novel,:version,:claim,'hierarchy','memory-build-1',:node,
            :chapter,1,:start,:end,:hash,:snapshot,:hash,:hash
        )
    """
    base = {
        "owner": ids["owner_id"],
        "novel": ids["novel_id"],
        "version": ids["version_id"],
        "claim": ids["claim_id"],
        "chapter": ids["chapter_id"],
        "node": "scene-1",
        "start": 0,
        "end": 10,
        "hash": HEX_A,
        "snapshot": HEX_A,
    }
    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(text(link_sql), base)
    assert "source_link_scope_or_snapshot_mismatch" in str(exc.value)

    wrong_snapshot = {**base, "node": "leaf-1", "end": 3, "snapshot": HEX_B}
    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(text(link_sql), wrong_snapshot)
    assert "source_link_scope_or_snapshot_mismatch" in str(exc.value)
    engine.dispose()


def test_graph_trigger_rejects_transition_range_and_cycle(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_candidate(engine)
    edge_sql = """
        INSERT INTO narrative_memory_edges (
            owner_id,novel_id,version_id,source_node_id,target_node_id,
            edge_type,edge_checksum,model_lineage_checksum
        ) VALUES (:owner,:novel,:version,:source,:target,:kind,:hash,:hash)
    """

    def edge_params(source: int, target: int, kind: str = "contains") -> dict:
        return {
            "owner": ids["owner_id"],
            "novel": ids["novel_id"],
            "version": ids["version_id"],
            "source": source,
            "target": target,
            "kind": kind,
            "hash": HEX_A,
        }

    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(edge_sql), edge_params(ids["global_id"], ids["chapter_node_id"])
        )
    assert "memory_edge_transition_violation" in str(exc.value)

    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(edge_sql),
            edge_params(ids["chapter_node_id"], ids["arc_id"], "derives_from"),
        )
    assert "memory_edge_range_violation" in str(exc.value)

    with engine.begin() as conn:
        conn.execute(
            text(edge_sql),
            edge_params(ids["arc_id"], ids["arc_peer_id"], "derives_from"),
        )
    with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
        conn.execute(
            text(edge_sql),
            edge_params(ids["arc_peer_id"], ids["arc_id"], "derives_from"),
        )
    assert "memory_edge_cycle_violation" in str(exc.value)

    with engine.connect() as conn:
        trigger = conn.execute(
            text(
                "SELECT tgdeferrable FROM pg_trigger "
                "WHERE tgname = 'trg_narrative_memory_edges_graph'"
            )
        ).scalar_one()
        assert trigger is True
    engine.dispose()


def test_all_authority_is_append_only_and_seal_blocks_late_content(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_candidate(engine, seal=True)
    row_ids = {
        "narrative_memory_versions": ids["version_id"],
        "narrative_memory_nodes": ids["global_id"],
        "narrative_memory_claims": ids["claim_id"],
        "narrative_memory_edges": ids["edge_id"],
        "narrative_memory_source_links": ids["link_id"],
        "narrative_memory_manifests": ids["manifest_id"],
        "narrative_memory_validation_reports": ids["report_id"],
    }
    for table_name, row_id in row_ids.items():
        with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
            conn.execute(
                text(f"UPDATE {table_name} SET updated_at = updated_at WHERE id = :id"),
                {"id": row_id},
            )
        assert "append_only_violation" in str(exc.value)
        with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
            conn.execute(text(f"DELETE FROM {table_name} WHERE id = :id"), {"id": row_id})
        assert "append_only_violation" in str(exc.value)

    late_inserts = (
        (
            "narrative_memory_nodes",
            """(owner_id,novel_id,version_id,node_key,node_kind,chapter_start,
                 chapter_end,schema_version,content_checksum,model_lineage_checksum)
                VALUES (:owner,:novel,:version,'late-node','chapter_state',2,2,
                        'v1',:hash,:hash)""",
        ),
        (
            "narrative_memory_claims",
            """(owner_id,novel_id,version_id,node_id,claim_key,claim_kind,
                 schema_version,typed_payload,uncertainty,confidence,
                 visible_from_chapter,claim_checksum,model_lineage_checksum)
                VALUES (:owner,:novel,:version,:node,'late-claim','event_fact',
                        'v1','{}','certain',1,1,:hash,:hash)""",
        ),
        (
            "narrative_memory_edges",
            """(owner_id,novel_id,version_id,source_node_id,target_node_id,
                 edge_type,edge_checksum,model_lineage_checksum)
                VALUES (:owner,:novel,:version,:arc,:node,'contains',:hash,:hash)""",
        ),
        (
            "narrative_memory_source_links",
            """(owner_id,novel_id,version_id,claim_id,source_kind,
                 hierarchy_build_id,evidence_node_id,chapter_id,chapter_number,
                 source_start,source_end,content_hash,source_snapshot_hash,
                 link_checksum,model_lineage_checksum)
                VALUES (:owner,:novel,:version,:claim,'hierarchy','memory-build-1',
                        'leaf-1',:chapter,1,0,3,:hash,:hash,:hash,:hash)""",
        ),
    )
    params = {
        "owner": ids["owner_id"],
        "novel": ids["novel_id"],
        "version": ids["version_id"],
        "node": ids["chapter_node_id"],
        "arc": ids["arc_id"],
        "claim": ids["claim_id"],
        "chapter": ids["chapter_id"],
        "hash": HEX_A,
    }
    for table_name, statement in late_inserts:
        with engine.begin() as conn, pytest.raises(DBAPIError) as exc:
            conn.execute(text(f"INSERT INTO {table_name} {statement}"), params)
        assert "sealed_candidate_violation" in str(exc.value)
    engine.dispose()


def test_database_schema_has_no_memory_control_plane_or_pointer(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = {
            name
            for name in inspect(conn).get_table_names()
            if name.startswith("narrative_memory_")
        }
        assert names == AUTHORITY_TABLES
        assert not any(
            fragment in name
            for name in names
            for fragment in FORBIDDEN_TABLE_FRAGMENTS
        )
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    assert "13memoryauth01" in (current.stdout + current.stderr)

    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        table_names = set(inspect(conn).get_table_names())
        assert AUTHORITY_TABLES <= table_names
        assert "narrative_memory_active_pointers" not in table_names
        for table_name in AUTHORITY_TABLES:
            assert conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() == 0
    engine.dispose()
