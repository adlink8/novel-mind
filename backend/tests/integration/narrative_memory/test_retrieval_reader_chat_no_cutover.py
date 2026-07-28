"""Fresh-observer proof: offline retrieval leaves Reader Chat and pointers untouched."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.narrative_memory.experiments import (
    experiment_request_from_fixture,
    run_retrieval_experiment,
)
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_retrieval_candidates_pg import (
    HEX_A,
    _seed_eligible_candidate,
)


pytestmark = pytest.mark.integration


def _openapi_reader_chat_paths() -> list[str]:
    """Collect Reader Chat path keys from the FastAPI app without starting a server."""

    from app.main import app

    paths = sorted(
        p
        for p in app.openapi().get("paths", {})
        if "reader" in p or "conversation" in p or "chat" in p
    )
    return paths


def _pointer_snapshot_sql() -> str:
    return """
    SELECT table_name FROM information_schema.tables
    WHERE table_schema='public'
      AND (
        table_name LIKE '%active_pointer%'
        OR table_name LIKE '%_pointers'
        OR table_name = 'chunk_active_pointers'
        OR table_name = 'timeline_active_pointers'
      )
    ORDER BY table_name
    """


async def _row_checksums(session: AsyncSession, tables: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for table in tables:
        # count + ordered id hash when id exists
        try:
            count = (
                await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            ).scalar_one()
            cols = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=:t
                        ORDER BY ordinal_position
                        """
                        ),
                        {"t": table},
                    )
                )
                .scalars()
                .all()
            )
            if "id" in cols:
                ids = (
                    (await session.execute(text(f"SELECT id FROM {table} ORDER BY id")))
                    .scalars()
                    .all()
                )
                payload = json.dumps(
                    {"count": count, "ids": list(ids)},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            else:
                payload = json.dumps(
                    {"count": count, "cols": list(cols)},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            out[table] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except Exception:
            await session.rollback()
            out[table] = "error"
    return out


@pytest.fixture
async def no_cutover_pg(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    sync = create_engine(empty_postgres)
    try:
        seed = _seed_eligible_candidate(sync)
    finally:
        sync.dispose()
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session, seed
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_experiment_leaves_reader_chat_routes_and_pointers_unchanged(
    no_cutover_pg,
):
    session, seed = no_cutover_pg

    routes_before = _openapi_reader_chat_paths()
    tables = list(
        (await session.execute(text(_pointer_snapshot_sql()))).scalars().all()
    )
    # also pin narrative memory table set (no new pointer tables)
    nm_tables_before = list(
        (
            await session.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name LIKE 'narrative_memory%'
                    ORDER BY table_name
                    """
                )
            )
        )
        .scalars()
        .all()
    )
    ptr_before = await _row_checksums(session, tables)

    req = experiment_request_from_fixture(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        raw_question="角色在哪里",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        expected_manifest_checksum=HEX_A,
    )
    result = await run_retrieval_experiment(session, req, enabled=True)
    assert result.exit_code in (0, 2)

    routes_after = _openapi_reader_chat_paths()
    assert routes_before == routes_after

    nm_tables_after = list(
        (
            await session.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public'
                      AND table_name LIKE 'narrative_memory%'
                    ORDER BY table_name
                    """
                )
            )
        )
        .scalars()
        .all()
    )
    assert nm_tables_before == nm_tables_after
    assert not any("pointer" in t for t in nm_tables_after)
    assert not any("promotion" in t for t in nm_tables_after)

    ptr_after = await _row_checksums(session, tables)
    assert ptr_before == ptr_after


def test_phase15_modules_do_not_import_reader_chat_runtime():
    root = Path(__file__).resolve().parents[3] / "app" / "services" / "narrative_memory"
    names = [
        "retrieval_contracts.py",
        "routing.py",
        "candidate_reader.py",
        "descent.py",
        "citations.py",
        "retrieval_manifests.py",
        "experiments.py",
    ]
    for name in names:
        source = (root / name).read_text(encoding="utf-8")
        assert "app.services.reader_chat" not in source
        assert "app.models.reader_chat" not in source
        assert "app.api.reader_chat" not in source
