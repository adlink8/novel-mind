"""
PostgreSQL 16 migration matrix against CI-locked postgres:16.10.

Validates (D-05):
- service lock digest present and matches compose (fail closed)
- alembic heads is a single expected head
- empty-DB upgrade / current / check / history
- historical revision upgrade path to heads without loss
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

EXPECTED_HEAD = "20260801_2801"
# Intermediate revision after ownership + import jobs merge path, before eval/narrative.
HISTORICAL_REVISION = "c2860beb647d"  # tsvector restore — mid-chain checkpoint


def test_service_lock_postgres_digest_fail_closed(service_lock):
    """Digest missing/empty must fail closed; compose must pin the same digest."""
    pg = service_lock["postgres"]
    digest = pg["digest"]
    assert digest.startswith("sha256:")
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
    assert pg["tag"] == "16.10"
    assert digest in pg["image_ref"]
    assert "16.10" in pg["image_ref"]


def test_alembic_heads_single(pg_sync_url, require_postgres):
    """Only one migration head is allowed for CI upgrades."""
    result = run_alembic("heads", database_url=pg_sync_url)
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    head_ids = [ln.split()[0] for ln in lines if re.match(r"^[0-9a-f]+", ln.split()[0])]
    assert EXPECTED_HEAD in head_ids
    assert len(head_ids) == 1, f"multiple heads not allowed: {head_ids}"


def test_empty_db_upgrade_current_check_history(empty_postgres, pg_async_url):
    """Empty database: upgrade heads → current → check → history."""
    sync_url = empty_postgres

    up = run_alembic("upgrade", "heads", database_url=sync_url)
    assert up.returncode == 0

    current = run_alembic("current", database_url=sync_url)
    assert EXPECTED_HEAD in current.stdout

    check = run_alembic("check", database_url=sync_url)
    assert check.returncode == 0, (
        f"alembic check failed:\n{check.stdout}\n{check.stderr}"
    )

    history = run_alembic("history", database_url=sync_url)
    assert EXPECTED_HEAD in history.stdout
    assert "3c73d82690cf" in history.stdout  # base revision present

    # Schema smoke: critical tables exist after full upgrade.
    engine = create_engine(sync_url)
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    engine.dispose()
    for required in (
        "users",
        "novels",
        "text_chunks",
        "import_jobs",
        "alembic_version",
        "narrative_index_builds",
        "eval_datasets",
    ):
        assert required in tables, f"missing table after upgrade heads: {required}"


def test_historical_revision_then_heads(empty_postgres):
    """Upgrade to a mid-chain revision, then heads — no revision loss."""
    sync_url = empty_postgres

    run_alembic("upgrade", HISTORICAL_REVISION, database_url=sync_url)
    mid = run_alembic("current", database_url=sync_url)
    assert HISTORICAL_REVISION in mid.stdout

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        # tsvector column + GIN index must exist at this revision.
        row = conn.execute(
            text(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_name = 'text_chunks' AND column_name = 'search_vector'
                """
            )
        ).fetchone()
        assert row is not None
        # PostgreSQL reports USER-DEFINED for tsvector.
        assert row[0] in {"USER-DEFINED", "tsvector"}

        idx = conn.execute(
            text(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'text_chunks' AND indexname = 'idx_text_chunks_search'
                """
            )
        ).fetchone()
        assert idx is not None
    engine.dispose()

    run_alembic("upgrade", "heads", database_url=sync_url)
    final = run_alembic("current", database_url=sync_url)
    assert EXPECTED_HEAD in final.stdout

    engine = create_engine(sync_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "narrative_source_watermarks" in tables
    assert "narrative_refresh_runs" in tables
    assert "narrative_index_builds" in tables


def test_sqlite_not_used_for_postgres_conclusion(pg_sync_url, require_postgres):
    """Guard: CI postgres URL must be real PostgreSQL, never SQLite."""
    assert "sqlite" not in pg_sync_url.lower()
    engine = create_engine(pg_sync_url)
    with engine.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar()
        assert version is not None
        assert str(version).startswith("16."), (
            f"expected PostgreSQL 16.x, got {version}"
        )
    engine.dispose()


def test_reset_schema_is_isolated(empty_postgres):
    """Dedicated CI schema reset leaves only public empty shell before migrate."""
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        count = conn.execute(
            text(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                """
            )
        ).scalar()
        assert count == 0
    engine.dispose()
    # Re-seed so subsequent tests in the same session can rely on migrations if needed.
    reset_public_schema(empty_postgres)
    run_alembic("upgrade", "heads", database_url=empty_postgres)
