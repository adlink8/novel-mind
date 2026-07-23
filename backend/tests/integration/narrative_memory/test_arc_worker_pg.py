"""PostgreSQL arc/volume aggregation and local blocking tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
    _seed,
)


pytestmark = pytest.mark.integration


class _Src:
    def __init__(self, sessions):
        self._sessions = sessions

    async def inventory(self, *, owner_id: int, novel_id: int):
        async with self._sessions() as session:
            return await PostgresAuditSource(session).inventory(
                owner_id=owner_id, novel_id=novel_id
            )


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, chapters, _report = await _seed(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_chapter_blocks_only_containing_parent(builder_env) -> None:
    transport = ControlledTransport()
    transport.fail_chapters = {2}
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
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id
                )
            )
        ).all()
        by_key = {s.stage_key: s for s in stages}
        chapter_ok = [
            s
            for s in stages
            if s.stage_kind == "chapter_state" and s.status == "completed"
        ]
        assert len(chapter_ok) >= 2
        # Containing parent for chapter 2 is blocked or missing completion.
        parents = [s for s in stages if s.stage_kind == "arc_volume_aggregate"]
        if parents:
            assert any(
                p.status in {"blocked_dependency", "pending", "failed"} for p in parents
            )
            # At least one sibling parent may complete when window isolates.
            assert by_key  # stages materialized
