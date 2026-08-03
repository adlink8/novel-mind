"""PostgreSQL authority tests for Phase 16 rebuild plan tables."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

REBUILD_TABLES = {
    "narrative_memory_rebuild_plans",
    "narrative_memory_rebuild_items",
    "narrative_memory_reuse_reports",
}

FORBIDDEN_FRAGMENTS = {
    "active_pointer",
    "promotion",
    "rollback",
    "current_version",
    "selector",
}

HEX = "a" * 64
HEX_B = "b" * 64


def test_migration_round_trip_and_single_head(empty_postgres: str) -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "versions"
        / "16_narrative_memory_rebuild_authority.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "from app.models" not in source
    assert "Base.metadata" not in source
    assert 'revision = "16memrebuild01"' in source
    assert 'down_revision = "14membuild01"' in source

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        assert REBUILD_TABLES.issubset(tables)
        for name in tables:
            if "rebuild" in name or "reuse_report" in name:
                for frag in FORBIDDEN_FRAGMENTS:
                    assert frag not in name
        with engine.connect() as conn:
            heads = (
                conn.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
        # Single live head after full upgrade (Phase 27-03 is tip; Phase 16 is mid-chain).
        assert heads == ["20260801_2703"]
    finally:
        engine.dispose()

    run_alembic("downgrade", "14membuild01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        assert REBUILD_TABLES.isdisjoint(tables)
        assert "narrative_memory_build_runs" in tables
        assert "narrative_memory_versions" in tables
    finally:
        engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)


def _seed_versions(conn) -> tuple[int, int, int, int]:
    owner_id = conn.execute(
        text(
            "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
            "VALUES ('ro','ro@example.com','x',true,false) RETURNING id"
        )
    ).scalar_one()
    novel_id = conn.execute(
        text(
            "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
            "VALUES (:o,'N','ready',1,1) RETURNING id"
        ),
        {"o": owner_id},
    ).scalar_one()
    for build_id in ("hb-parent", "hb-target"):
        conn.execute(
            text(
                """
                INSERT INTO chunk_builds (
                    build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                    chunker_name,chunker_version,chunker_config_hash,collection_name,
                    is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                ) VALUES (
                    :b,:n,'committed',:h,:h,'semantic','1',:h,'c',false,true,
                    '[]','[]','[]'
                )
                """
            ),
            {
                "b": build_id,
                "n": novel_id,
                "h": HEX if build_id == "hb-parent" else HEX_B,
            },
        )
    parent_vid = conn.execute(
        text(
            """
            INSERT INTO narrative_memory_versions (
                owner_id,novel_id,version_key,source_snapshot_hash,
                hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                decoding_hash,config_hash,policy_hash,optional_source_lineage
            ) VALUES (
                :o,:n,'parent',:h,'hb-parent',:h,'p',:h,:h,:h,'{}',:h,:h,:h,'{}'
            ) RETURNING id
            """
        ),
        {"o": owner_id, "n": novel_id, "h": HEX},
    ).scalar_one()
    target_vid = conn.execute(
        text(
            """
            INSERT INTO narrative_memory_versions (
                owner_id,novel_id,version_key,source_snapshot_hash,
                hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                decoding_hash,config_hash,policy_hash,optional_source_lineage
            ) VALUES (
                :o,:n,'target',:hb,'hb-target',:hb,'p',:hb,:h,:h,'{}',:h,:h,:h,'{}'
            ) RETURNING id
            """
        ),
        {"o": owner_id, "n": novel_id, "h": HEX, "hb": HEX_B},
    ).scalar_one()
    return owner_id, novel_id, parent_vid, target_vid


def test_append_only_and_scope_constraints(empty_postgres: str) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        with engine.begin() as conn:
            owner_id, novel_id, parent_vid, target_vid = _seed_versions(conn)
            plan_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_rebuild_plans (
                        owner_id,novel_id,parent_version_id,target_version_id,
                        old_source_snapshot_hash,new_source_snapshot_hash,
                        old_hierarchy_build_id,new_hierarchy_build_id,
                        old_hierarchy_checksum,new_hierarchy_checksum,
                        boundary_plan,boundary_plan_checksum,
                        oracle_policy_version,oracle_policy_checksum,
                        compatibility_policy_checksum,graph_checksum,plan_checksum,
                        change_summary,eligibility_report_checksum
                    ) VALUES (
                        :o,:n,:p,:t,:h,:hb,'hb-parent','hb-target',:h,:hb,
                        '{}',:h,'rebuild-oracle.v1',:h,:h,:h,:h,'{}',:h
                    ) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "p": parent_vid,
                    "t": target_vid,
                    "h": HEX,
                    "hb": HEX_B,
                },
            ).scalar_one()
            item_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_rebuild_items (
                        owner_id,novel_id,plan_id,asset_key,asset_kind,
                        decision,direct_reasons,propagated_reasons,predecessor_keys
                    ) VALUES (
                        :o,:n,:plan,'chapter_state:1','chapter_state',
                        'dirty','["chapter_edited"]','[]','[]'
                    ) RETURNING id
                    """
                ),
                {"o": owner_id, "n": novel_id, "plan": plan_id},
            ).scalar_one()
            report_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_reuse_reports (
                        owner_id,novel_id,plan_id,parent_version_id,target_version_id,
                        plan_checksum,report_checksum,body
                    ) VALUES (:o,:n,:plan,:p,:t,:h,:hb,'{}')
                    RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "plan": plan_id,
                    "p": parent_vid,
                    "t": target_vid,
                    "h": HEX,
                    "hb": HEX_B,
                },
            ).scalar_one()

        with engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "UPDATE narrative_memory_rebuild_plans "
                        "SET plan_checksum=:h WHERE id=:id"
                    ),
                    {"h": "c" * 64, "id": plan_id},
                )
                conn.commit()

        with engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "UPDATE narrative_memory_rebuild_items "
                        "SET decision='carried' WHERE id=:id"
                    ),
                    {"id": item_id},
                )
                conn.commit()

        with engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text("DELETE FROM narrative_memory_reuse_reports WHERE id=:id"),
                    {"id": report_id},
                )
                conn.commit()

        with engine.begin() as conn:
            # same parent/target pair uniqueness
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO narrative_memory_rebuild_plans (
                            owner_id,novel_id,parent_version_id,target_version_id,
                            old_source_snapshot_hash,new_source_snapshot_hash,
                            old_hierarchy_build_id,new_hierarchy_build_id,
                            old_hierarchy_checksum,new_hierarchy_checksum,
                            boundary_plan,boundary_plan_checksum,
                            oracle_policy_version,oracle_policy_checksum,
                            compatibility_policy_checksum,graph_checksum,plan_checksum,
                            change_summary,eligibility_report_checksum
                        ) VALUES (
                            :o,:n,:p,:t,:h,:hb,'hb-parent','hb-target',:h,:hb,
                            '{}',:h,'rebuild-oracle.v1',:h,:h,:h,:other,'{}',:h
                        )
                        """
                    ),
                    {
                        "o": owner_id,
                        "n": novel_id,
                        "p": parent_vid,
                        "t": target_vid,
                        "h": HEX,
                        "hb": HEX_B,
                        "other": "c" * 64,
                    },
                )

        with engine.begin() as conn:
            # invalid decision rejected
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO narrative_memory_rebuild_items (
                            owner_id,novel_id,plan_id,asset_key,asset_kind,decision
                        ) VALUES (:o,:n,:plan,'x','chapter_state','promoted')
                        """
                    ),
                    {"o": owner_id, "n": novel_id, "plan": plan_id},
                )

        with engine.begin() as conn:
            # same parent=target rejected
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO narrative_memory_rebuild_plans (
                            owner_id,novel_id,parent_version_id,target_version_id,
                            old_source_snapshot_hash,new_source_snapshot_hash,
                            old_hierarchy_build_id,new_hierarchy_build_id,
                            old_hierarchy_checksum,new_hierarchy_checksum,
                            boundary_plan,boundary_plan_checksum,
                            oracle_policy_version,oracle_policy_checksum,
                            compatibility_policy_checksum,graph_checksum,plan_checksum,
                            change_summary,eligibility_report_checksum
                        ) VALUES (
                            :o,:n,:p,:p,:h,:h,'hb-parent','hb-parent',:h,:h,
                            '{}',:h,'rebuild-oracle.v1',:h,:h,:h,:pc,'{}',:h
                        )
                        """
                    ),
                    {
                        "o": owner_id,
                        "n": novel_id,
                        "p": parent_vid,
                        "h": HEX,
                        "pc": "d" * 64,
                    },
                )
    finally:
        engine.dispose()


def test_forbidden_schema_inventory(empty_postgres: str) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        rebuild = {t for t in tables if "rebuild" in t or t.endswith("reuse_reports")}
        assert rebuild == REBUILD_TABLES
        for t in rebuild:
            cols = {c["name"] for c in inspect(engine).get_columns(t)}
            for frag in FORBIDDEN_FRAGMENTS:
                assert not any(frag in c for c in cols)
    finally:
        engine.dispose()
