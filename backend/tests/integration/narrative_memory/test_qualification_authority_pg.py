"""PostgreSQL authority tests for Phase 17 qualification tables."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

QUAL_TABLES = {
    "narrative_memory_qualification_runs",
    "narrative_memory_qualification_case_results",
    "narrative_memory_qualification_reports",
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
        / "17_narrative_memory_qualification.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "from app.models" not in source
    assert "Base.metadata" not in source
    assert 'revision = "17memqual01"' in source
    assert 'down_revision = "16memrebuild01"' in source

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        assert QUAL_TABLES.issubset(tables)
        for name in tables:
            if "qualification" in name:
                for frag in FORBIDDEN_FRAGMENTS:
                    assert frag not in name
        with engine.connect() as conn:
            heads = (
                conn.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
        assert heads == ["20260801_2801"]
    finally:
        engine.dispose()

    run_alembic("downgrade", "16memrebuild01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        tables = set(inspect(engine).get_table_names())
        assert QUAL_TABLES.isdisjoint(tables)
        assert "narrative_memory_rebuild_plans" in tables
    finally:
        engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)


def _seed(conn) -> tuple[int, int, int]:
    owner_id = conn.execute(
        text(
            "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
            "VALUES ('qau','qau@example.com','x',true,false) RETURNING id"
        )
    ).scalar_one()
    novel_id = conn.execute(
        text(
            "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
            "VALUES (:o,'Q','ready',1,1) RETURNING id"
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
                'hb-q',:n,'committed',:h,:h,'semantic','1',:h,'c',false,true,
                '[]','[]','[]'
            )
            """
        ),
        {"n": novel_id, "h": HEX},
    )
    vid = conn.execute(
        text(
            """
            INSERT INTO narrative_memory_versions (
                owner_id,novel_id,version_key,source_snapshot_hash,
                hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                decoding_hash,config_hash,policy_hash,optional_source_lineage
            ) VALUES (
                :o,:n,'v1',:h,'hb-q',:h,'p',:h,:h,:h,'{}',:h,:h,:h,'{}'
            ) RETURNING id
            """
        ),
        {"o": owner_id, "n": novel_id, "h": HEX},
    ).scalar_one()
    conn.commit()
    return owner_id, novel_id, vid


def test_append_only_and_scope(empty_postgres: str) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    try:
        with engine.connect() as conn:
            o, n, v = _seed(conn)
            run_id = conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_qualification_runs (
                        owner_id,novel_id,version_id,status,fixture_checksum,
                        policy_checksum,source_snapshot_hash,hierarchy_build_id,
                        hierarchy_checksum,candidate_manifest_checksum,
                        generator_lineage,judge_lineage,pricing_checksum,
                        budget_checksum,pointer_before_digest,lineage
                    ) VALUES (
                        :o,:n,:v,'running',:h,:h,:h,'hb-q',:h,:h,
                        '{}','{}',:h,:h,:h,'{}'
                    ) RETURNING id
                    """
                ),
                {"o": o, "n": n, "v": v, "h": HEX},
            ).scalar_one()
            conn.commit()

            # UPDATE blocked
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "UPDATE narrative_memory_qualification_runs "
                        "SET status='completed' WHERE id=:id"
                    ),
                    {"id": run_id},
                )
                conn.commit()
            conn.rollback()

            # DELETE blocked
            with pytest.raises(Exception):
                conn.execute(
                    text(
                        "DELETE FROM narrative_memory_qualification_runs WHERE id=:id"
                    ),
                    {"id": run_id},
                )
                conn.commit()
            conn.rollback()

            # case insert
            conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_qualification_case_results (
                        owner_id,novel_id,run_id,case_key,strategy,bucket,
                        artifact_checksum,usage_checksum,sanitized_reasons,artifact
                    ) VALUES (
                        :o,:n,:r,'c1','hierarchical_candidate','local',
                        :h,:h,'[]','{}'
                    )
                    """
                ),
                {"o": o, "n": n, "r": run_id, "h": HEX},
            )
            conn.commit()

            # report seal
            conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_qualification_reports (
                        owner_id,novel_id,run_id,qualification_kind,verdict,
                        reason_codes,metric_payload_checksum,verifier_checksum,
                        pointer_after_digest,command_payload_checksum,output_digest,
                        disclaimer,report_body
                    ) VALUES (
                        :o,:n,:r,'single_book_candidate','blocked',
                        '["x"]',:h,:h,:h,:h,:h,'disclaimer','{}'
                    )
                    """
                ),
                {"o": o, "n": n, "r": run_id, "h": HEX},
            )
            conn.commit()

            # illegal verdict
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO narrative_memory_qualification_reports (
                            owner_id,novel_id,run_id,qualification_kind,verdict,
                            reason_codes,metric_payload_checksum,verifier_checksum,
                            pointer_after_digest,command_payload_checksum,output_digest,
                            disclaimer,report_body
                        ) VALUES (
                            :o,:n,:r,'single_book_candidate','promoted',
                            '[]',:h,:h,:h,:h,:h,'d','{}'
                        )
                        """
                    ),
                    {"o": o, "n": n, "r": run_id, "h": HEX_B},
                )
                conn.commit()
            conn.rollback()
    finally:
        engine.dispose()
