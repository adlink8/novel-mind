"""Static/dynamic proof of chat exclusion and no narrative-memory pointer."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.narrative_memory.builder_worker import (
    scan_builder_package_for_forbidden_capabilities,
)
from tests.integration.conftest import run_alembic


pytestmark = pytest.mark.integration


def test_static_scan_excludes_reader_chat_and_promotion() -> None:
    hits = scan_builder_package_for_forbidden_capabilities()
    assert not any("import" in h and "reader_chat" in h for h in hits)
    root = Path(__file__).resolve().parents[3] / "app" / "services" / "narrative_memory"
    # Phase 28-04 split: the worker's call sites live in _worker_*.py mixins;
    # cover them with the same static text checks as builder_*.py.
    for path in sorted(
        list(root.glob("builder_*.py")) + list(root.glob("_worker_*.py"))
    ):
        source = path.read_text(encoding="utf-8")
        assert "from app.models.reader_chat" not in source
        assert "from app.services.reader_chat" not in source
        # Scanner may list forbidden names; runtime call sites are still banned.
        if path.name != "builder_worker.py":
            assert "set_active_pointer(" not in source
            assert "promote_timeline(" not in source
        assert "def set_active_pointer" not in source
        assert "def promote_timeline" not in source


@pytest.mark.asyncio
async def test_schema_has_no_narrative_memory_pointer_table(
    empty_postgres: str, pg_async_url: str
) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            names = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert "narrative_memory_active_pointers" not in names
        assert any(n.startswith("narrative_memory_build_") for n in names)
    finally:
        await engine.dispose()
