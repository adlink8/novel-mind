"""PostgreSQL migration + checkpoint/cost/progress + crash/concurrency tests.

Phase 28-01 (REQ-NM-01): durable terminal states, idempotent resume without
whole-book restart, ledger auditability, and reversible single-head migration
whose old builder rows stay readable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildCheckpoint,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import ReasonCode, TerminalState
from app.services.narrative_memory.builder_repository import (
    BuilderRepository,
    BuilderRepositoryError,
)
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


async def _run_chapter_stages(factory, run_id: int) -> list[NarrativeMemoryBuildStage]:
    async with factory() as session:
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        return list(stages)


# ---------------------------------------------------------------------------
# Migration: old rows stay readable and are normalised to terminal states
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_normalises_old_rows(empty_postgres: str, pg_async_url: str):
    """Upgrade to 2703, seed legacy rows, upgrade to head, verify readability."""
    run_alembic("upgrade", "20260801_2703", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, _chapters, _report = await _seed(session)
            # Legacy rows inserted with only the Phase-27 schema columns.
            await session.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_runs (
                        owner_id,novel_id,version_id,eligibility_report_checksum,
                        eligibility_policy_version,status,progress,run_policy
                    ) VALUES (:o,:n,:v,:h,'p','running','{}','{}') RETURNING id
                    """
                ),
                {
                    "o": user.id,
                    "n": novel.id,
                    "v": version.id,
                    "h": version.eligibility_report_checksum,
                },
            )
            run_id = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM narrative_memory_build_runs
                        WHERE owner_id=:o AND novel_id=:n AND version_id=:v
                        """
                    ),
                    {"o": user.id, "n": novel.id, "v": version.id},
                )
            ).scalar_one()
            await session.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_stages (
                        owner_id,novel_id,version_id,run_id,stage_key,stage_kind,
                        status,checkpoint,dependency_keys
                    ) VALUES (
                        :o,:n,:v,:r,'chapter_state:1','chapter_state',
                        'completed','{}','[]'
                    )
                    """
                ),
                {"o": user.id, "n": novel.id, "v": version.id, "r": int(run_id)},
            )
            await session.commit()
        await engine.dispose()

        # Upgrade to head: old rows must survive and be normalised.
        run_alembic("upgrade", "head", database_url=empty_postgres)
        engine = create_async_engine(pg_async_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            stage = await session.scalar(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.stage_key == "chapter_state:1"
                )
            )
            assert stage is not None
            assert stage.status == "completed"
            assert stage.terminal_state == TerminalState.COMPLETED.value
            assert stage.reason_code is None
            # Run row readable and carries defaulted new columns.
            run = await session.scalar(select(NarrativeMemoryBuildRun))
            assert run is not None
            assert int(run.resume_count or 0) == 0
            assert run.last_error_code is None
            # Checkpoint table exists and is empty.
            count = await session.scalar(
                select(text("count(*)")).select_from(
                    NarrativeMemoryBuildCheckpoint
                )
            )
            assert int(count or 0) == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Worker records durable checkpoint/cost/progress fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_records_checkpoint_cost_and_progress(builder_env) -> None:
    transport = ControlledTransport()
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
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    # Chapters-only policy: all chapter stages complete; arc/global stages fail
    # because the controlled transport only speaks chapter payloads.
    assert result.status == "partial"
    assert len(result.completed_stages) >= 3
    stages = await _run_chapter_stages(builder_env["factory"], run_id)
    assert len(stages) == 3
    assert all(s.status == "completed" for s in stages)
    async with builder_env["factory"]() as session:
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        assert run.source_snapshot_hash is not None
        assert int(run.resume_count or 0) >= 1
        assert sorted((run.progress or {}).get("chapter_numbers") or []) == [1, 2, 3]

        stages = await _run_chapter_stages(builder_env["factory"], run_id)
        assert len(stages) == 3
        for stage in stages:
            assert stage.terminal_state == TerminalState.COMPLETED.value
            assert stage.reason_code == ReasonCode.COMPLETED_CANDIDATE.value
            assert stage.idempotency_key == f"{run_id}:{stage.stage_key}:1"
            assert stage.source_checksum == run.source_snapshot_hash
            assert stage.model_lineage
            assert stage.checkpoint.get("chapter_id")

        ledger = await session.scalar(
            select(NarrativeMemoryBuildBudgetLedger).where(
                NarrativeMemoryBuildBudgetLedger.run_id == run_id
            )
        )
        assert ledger is not None
        assert int(ledger.settled_calls) >= 3
        assert int(ledger.settled_input_tokens) >= 3
        assert ledger.settled_cost_usd > 0

        # Immutable checkpoint journal carries the terminal transitions.
        checkpoints = (
            await session.scalars(
                select(NarrativeMemoryBuildCheckpoint).where(
                    NarrativeMemoryBuildCheckpoint.run_id == run_id
                )
            )
        ).all()
        assert len(checkpoints) >= 3
        chapter_keys = {s.stage_key for s in stages}
        chapter_checkpoints = [c for c in checkpoints if c.stage_key in chapter_keys]
        assert chapter_checkpoints
        assert all(
            c.terminal_state == TerminalState.COMPLETED.value
            for c in chapter_checkpoints
        )

    # Resume must not re-run confirmed stages (no transport churn).
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = await _run_chapter_stages(builder_env["factory"], run_id)
        assert all(s.status == "completed" for s in stages)


# ---------------------------------------------------------------------------
# Crash recovery: resume re-runs only the affected stage (no restart-all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_recovery_resumes_only_affected_stage(builder_env) -> None:
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
    # Chapter 2 failed → isolated; siblings completed.
    async with builder_env["factory"]() as session:
        stages = await _run_chapter_stages(builder_env["factory"], run_id)
        by_key = {s.stage_key: s for s in stages}
        assert by_key["chapter_state:2"].status == "failed"
        assert (
            by_key["chapter_state:2"].terminal_state == TerminalState.ISOLATED.value
        )
        assert (
            by_key["chapter_state:2"].reason_code
            == ReasonCode.INTERNAL_ERROR.value
        )
        assert by_key["chapter_state:1"].status == "completed"

    def _chapter_calls() -> int:
        return sum(1 for c in transport.calls if "chapter_number" in c["payload"])

    chapter_calls_after_run1 = _chapter_calls()

    # Operator requeues the isolated chapter (idempotency key advances).
    async with builder_env["factory"]() as session:
        repo = BuilderRepository(session)
        stage2 = await session.scalar(
            select(NarrativeMemoryBuildStage).where(
                NarrativeMemoryBuildStage.run_id == run_id,
                NarrativeMemoryBuildStage.stage_key == "chapter_state:2",
            )
        )
        assert stage2 is not None
        await repo.mark_stage(stage2, status="pending")
        await session.commit()

    # Clear the failure injection and resume.
    transport.fail_chapters = set()
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = await _run_chapter_stages(builder_env["factory"], run_id)
        by_key = {s.stage_key: s for s in stages}
        # The requeued chapter recovered to completed; siblings untouched.
        assert by_key["chapter_state:2"].status == "completed"
        assert (
            by_key["chapter_state:2"].terminal_state == TerminalState.COMPLETED.value
        )
        assert by_key["chapter_state:1"].status == "completed"
        assert by_key["chapter_state:3"].status == "completed"
        # Only the requeued chapter re-ran on resume (no whole-book restart).
        assert _chapter_calls() == chapter_calls_after_run1 + 1


# ---------------------------------------------------------------------------
# D-03: chapter failure blocks only dependents, never whole-book restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chapter_failure_blocks_dependents_not_whole_book(builder_env) -> None:
    async with builder_env["factory"]() as session:
        repo = BuilderRepository(session)
        run = await repo.create_run(
            owner_id=builder_env["owner_id"],
            novel_id=builder_env["novel_id"],
            version_id=builder_env["version_id"],
            eligibility_report_checksum="a" * 64,
            eligibility_policy_version="v1",
            run_policy=_policy(),
        )
        await repo.ensure_stages(
            run,
            [
                {
                    "stage_key": "chapter_state:1",
                    "stage_kind": "chapter_state",
                    "chapter_start": 1,
                    "chapter_end": 1,
                    "dependency_keys": [],
                },
                {
                    "stage_key": "chapter_state:2",
                    "stage_kind": "chapter_state",
                    "chapter_start": 2,
                    "chapter_end": 2,
                    "dependency_keys": [],
                },
                {
                    "stage_key": "arc_volume_aggregate:arc",
                    "stage_kind": "arc_volume_aggregate",
                    "chapter_start": 1,
                    "chapter_end": 2,
                    "dependency_keys": ["chapter_state:1", "chapter_state:2"],
                },
                {
                    "stage_key": "global_story:book",
                    "stage_kind": "global_aggregate",
                    "chapter_start": 1,
                    "chapter_end": 2,
                    "dependency_keys": ["arc_volume_aggregate:arc"],
                },
            ],
        )
        stages = await repo.list_stages(run.id)
        by_key = {s.stage_key: s for s in stages}
        await repo.mark_stage(
            by_key["chapter_state:1"],
            status="completed",
            reason_code=ReasonCode.COMPLETED_CANDIDATE,
        )
        await repo.isolate_stage(
            by_key["chapter_state:2"],
            exc=RuntimeError("injected_chapter_failure"),
        )
        await repo.block_dependents(run.id, "chapter_state:2")
        await session.commit()

        stages = await repo.list_stages(run.id)
        by_key = {s.stage_key: s for s in stages}
        assert by_key["arc_volume_aggregate:arc"].status == "blocked_dependency"
        assert by_key["global_story:book"].status == "blocked_dependency"
        assert (
            by_key["arc_volume_aggregate:arc"].reason_code
            == ReasonCode.DEPENDENCY_FAILED.value
        )
        assert (
            by_key["arc_volume_aggregate:arc"].terminal_state
            == TerminalState.BLOCKED.value
        )
        # Completed sibling is never rewound.
        assert by_key["chapter_state:1"].status == "completed"
        assert (
            by_key["chapter_state:1"].terminal_state == TerminalState.COMPLETED.value
        )


# ---------------------------------------------------------------------------
# Concurrency: lease conflict is detected (no double processing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_worker_lease_conflict(builder_env) -> None:
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=ControlledTransport(),
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    # Worker A commits its lease claim.
    async with builder_env["factory"]() as session:
        repo = BuilderRepository(session)
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        await repo.claim_run_lease(run, lease_id="worker-A")
        await session.commit()

    # Worker B with a different lease_id must be rejected.
    with pytest.raises(BuilderRepositoryError):
        await worker.process_run(
            owner_id=builder_env["owner_id"],
            novel_id=builder_env["novel_id"],
            version_id=builder_env["version_id"],
            lease_id="worker-B",
        )
