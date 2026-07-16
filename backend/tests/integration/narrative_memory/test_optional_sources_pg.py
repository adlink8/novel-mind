"""Optional source adapter PostgreSQL matrix."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.narrative_memory.builder_contracts import SourceStatus
from app.services.narrative_memory.optional_sources import load_optional_signals
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_chapter_state_worker_pg import _seed


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_optional_sources_return_explicit_statuses(
    empty_postgres: str, pg_async_url: str
) -> None:
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            _user, _novel, version, _chapters, _report = await _seed(session)
            signals = await load_optional_signals(
                session,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version=version,
                chapter_number=1,
            )
            assert {s.source_kind for s in signals} == {
                "timeline",
                "relationship",
                "clue",
            }
            for signal in signals:
                assert signal.status in SourceStatus
    finally:
        await engine.dispose()
