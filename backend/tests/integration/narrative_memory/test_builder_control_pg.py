"""PostgreSQL control-plane schema tests for Phase 14 builder tables."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import run_alembic


pytestmark = pytest.mark.integration

BUILDER_TABLES = {
    "narrative_memory_build_runs",
    "narrative_memory_build_stages",
    "narrative_memory_build_budget_ledgers",
    "narrative_memory_build_budget_reservations",
    "narrative_memory_build_model_call_attempts",
    "narrative_memory_build_reports",
}

FORBIDDEN_FRAGMENTS = {
    "active_pointer",
    "promotion",
    "rollback",
    "current_version",
}


def test_migration_is_revision_frozen_and_single_head(empty_postgres: str) -> None:
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "migrations"
        / "versions"
        / "14_narrative_memory_builder_control.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "from app.models" not in source
    assert "Base.metadata" not in source
    assert 'revision = "14membuild01"' in source
    assert 'down_revision = "13memoryauth01"' in source

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert BUILDER_TABLES.issubset(tables)
        for name in tables:
            if name.startswith("narrative_memory_build"):
                for frag in FORBIDDEN_FRAGMENTS:
                    assert frag not in name
        with engine.connect() as conn:
            heads = (
                conn.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
        # Single live head after full upgrade (Phase 28-01 is tip; Phases 14/17 mid-chain).
        assert heads == ["20260801_2801"]
    finally:
        engine.dispose()

    run_alembic("downgrade", "13memoryauth01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        assert BUILDER_TABLES.isdisjoint(tables)
        assert "narrative_memory_versions" in tables
    finally:
        engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)


def test_completed_stage_and_attempt_immutability(empty_postgres: str) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    hex64 = "a" * 64
    try:
        with engine.begin() as conn:
            owner_id = conn.execute(
                text(
                    "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                    "VALUES ('bowner','b@example.com','x',true,false) RETURNING id"
                )
            ).scalar_one()
            novel_id = conn.execute(
                text(
                    "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                    "VALUES (:o,'N','ready',1,1) RETURNING id"
                ),
                {"o": owner_id},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO chunk_builds (
                        build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                        chunker_name,chunker_version,chunker_config_hash,collection_name,
                        is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                    ) VALUES (
                        'b1',:n,'committed',:h,:h,'semantic','1',:h,'c',false,true,
                        '[]','[]','[]'
                    )
                    """
                ),
                {"n": novel_id, "h": hex64},
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
                        :o,:n,'v1',:h,'b1',:h,'p',:h,:h,:h,'{}',:h,:h,:h,'{}'
                    ) RETURNING id
                    """
                ),
                {"o": owner_id, "n": novel_id, "h": hex64},
            ).scalar_one()
            run_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_runs (
                        owner_id,novel_id,version_id,eligibility_report_checksum,
                        eligibility_policy_version,status,progress,run_policy
                    ) VALUES (:o,:n,:v,:h,'p','running','{}','{}') RETURNING id
                    """
                ),
                {"o": owner_id, "n": novel_id, "v": version_id, "h": hex64},
            ).scalar_one()
            stage_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_stages (
                        owner_id,novel_id,version_id,run_id,stage_key,stage_kind,
                        status,artifact_checksum,checkpoint,dependency_keys
                    ) VALUES (
                        :o,:n,:v,:r,'chapter_state:1','chapter_state',
                        'completed',:h,'{}','[]'
                    ) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "r": run_id,
                    "h": hex64,
                },
            ).scalar_one()
            attempt_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_model_call_attempts (
                        run_id,stage_key,attempt_number,status,request_hash,
                        deployment_lineage,usage
                    ) VALUES (:r,'chapter_state:1',1,'succeeded',:h,'{}','{}')
                    RETURNING id
                    """
                ),
                {"r": run_id, "h": hex64},
            ).scalar_one()

        with engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "UPDATE narrative_memory_build_stages "
                        "SET artifact_checksum=:h WHERE id=:id"
                    ),
                    {"h": "b" * 64, "id": stage_id},
                )
                conn.commit()

        with engine.begin() as conn:
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "UPDATE narrative_memory_build_model_call_attempts "
                        "SET status='failed' WHERE id=:id"
                    ),
                    {"id": attempt_id},
                )
                conn.commit()

        with engine.begin() as conn:
            # one live run per version
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO narrative_memory_build_runs (
                            owner_id,novel_id,version_id,eligibility_report_checksum,
                            eligibility_policy_version,status,progress,run_policy
                        ) VALUES (:o,:n,:v,:h,'p','pending','{}','{}')
                        """
                    ),
                    {
                        "o": owner_id,
                        "n": novel_id,
                        "v": version_id,
                        "h": hex64,
                    },
                )
    finally:
        engine.dispose()
