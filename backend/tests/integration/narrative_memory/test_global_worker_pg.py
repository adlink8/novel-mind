"""PostgreSQL Global stage gating tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
    _seed,
)
from tests.integration.narrative_memory.test_arc_worker_pg import _Src


pytestmark = pytest.mark.integration


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, _chapters, _report = await _seed(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_global_blocked_when_parent_missing(builder_env) -> None:
    transport = ControlledTransport()
    transport.fail_chapters = {1, 2, 3}
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
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
        global_stage = await session.scalar(
            select(NarrativeMemoryBuildStage).where(
                NarrativeMemoryBuildStage.run_id == run_id,
                NarrativeMemoryBuildStage.stage_kind == "global_aggregate",
            )
        )
        # Either not created yet or blocked / not completed without parents.
        if global_stage is not None:
            assert global_stage.status != "completed"
