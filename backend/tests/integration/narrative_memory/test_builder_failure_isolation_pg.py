"""Adversarial failure isolation and resume tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory import NarrativeMemoryNode
from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
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
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resume_preserves_completed_sibling_artifacts(builder_env) -> None:
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
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        completed = {
            s.stage_key: s.artifact_checksum for s in stages if s.status == "completed"
        }
        assert completed
        nodes_before = (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.version_id == builder_env["version_id"],
                    NarrativeMemoryNode.node_kind == "chapter_state",
                )
            )
        ).all()
        node_checksums = {n.node_key: n.content_checksum for n in nodes_before}

    # Clear failure injection and resume.
    transport.fail_chapters = set()
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        for key, checksum in completed.items():
            row = next(s for s in stages if s.stage_key == key)
            assert row.artifact_checksum == checksum
        nodes_after = (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.version_id == builder_env["version_id"],
                    NarrativeMemoryNode.node_kind == "chapter_state",
                )
            )
        ).all()
        for key, checksum in node_checksums.items():
            row = next(n for n in nodes_after if n.node_key == key)
            assert row.content_checksum == checksum
