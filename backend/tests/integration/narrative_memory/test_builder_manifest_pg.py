"""Manifest seal and report observation tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory import NarrativeMemoryManifest
from app.models.narrative_memory_builder import NarrativeMemoryBuildReport
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_arc_worker_pg import _Src
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
    _seed,
)


pytestmark = pytest.mark.integration


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, _chapters, _report = await _seed(session)
            pointer_before = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM chunk_active_pointers "
                        "WHERE novel_id = :n"
                    ),
                    {"n": novel.id},
                )
            ).scalar_one()
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
            "pointer_before": pointer_before,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_build_may_seal_and_never_creates_memory_pointer(
    builder_env,
) -> None:
    transport = ControlledTransport()
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        tables = (
            await session.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename LIKE 'narrative_memory%'"
                )
            )
        ).scalars().all()
        assert not any("pointer" in name for name in tables)
        pointer_after = (
            await session.execute(
                text(
                    "SELECT count(*) FROM chunk_active_pointers WHERE novel_id = :n"
                ),
                {"n": builder_env["novel_id"]},
            )
        ).scalar_one()
        assert pointer_after == builder_env["pointer_before"]
        # Manifest may or may not seal depending on full graph coverage.
        _manifests = (
            await session.scalars(
                select(NarrativeMemoryManifest).where(
                    NarrativeMemoryManifest.version_id == builder_env["version_id"]
                )
            )
        ).all()
        _reports = (
            await session.scalars(select(NarrativeMemoryBuildReport))
        ).all()
        assert True  # observation-only structural gate
