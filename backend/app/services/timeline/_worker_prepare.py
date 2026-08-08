"""Timeline worker run state & pre-flight preparation.

Responsibilities of this leaf module (refactor split):
- Deterministic pipeline exceptions ``TimelineWorkerError`` /
  ``TimelineCancellationRequested``.
- ``_raise_if_cancel_requested`` checkpoint (shared by extract + reconcile).
- ``_claim_run`` lease acquisition (with_for_update, idempotent on
  completed/cancelled runs) and ``_prepare_run`` which loads the Phase 07
  hierarchy, creates the candidate ``AnalysisVersion`` + budget ledger, and
  snapshots deployment prices (``_prices``).

This module imports ``TimelineWorkerRuntime`` from ``_worker_runtime`` only —
it never imports the worker facade, so no import cycle. Public exception
names are re-exported from ``worker.py`` unchanged.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.analysis import AnalysisBudgetLedger, AnalysisRun, AnalysisVersion
from app.models.chunk_build import ChunkActivePointer, ChunkBuild
from app.models.novel import Chapter
from app.schemas.timeline import TimelineExtraction
from app.services.timeline._worker_runtime import TimelineWorkerRuntime
from app.services.timeline.model_gateway import DependencyPaused, ModelDeployment


class TimelineWorkerError(RuntimeError):
    """A deterministic production pipeline precondition failed."""


class TimelineCancellationRequested(RuntimeError):
    """The durable run was cancelled while the production worker was active."""


async def _claim_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
    lease_id: str,
) -> bool:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None or run.status == "completed" or run.cancel_requested:
            return False
        now = datetime.now(UTC)
        if (
            run.lease_id
            and run.lease_id != lease_id
            and run.lease_expires_at
            and run.lease_expires_at > now
        ):
            return False
        run.lease_id = lease_id
        run.lease_expires_at = now + timedelta(minutes=5)
        run.heartbeat_at = now
        run.status = "running"
        return True


async def _raise_if_cancel_requested(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
) -> None:
    async with sessions() as session:
        cancelled = await session.scalar(
            select(AnalysisRun.cancel_requested).where(
                AnalysisRun.id == run_id,
            )
        )
    if cancelled:
        raise TimelineCancellationRequested


async def _prepare_run(runtime: TimelineWorkerRuntime, run_id: int):
    async with runtime.sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None:
            raise TimelineWorkerError("analysis run does not exist")
        pointer = await session.scalar(
            select(ChunkActivePointer).where(
                ChunkActivePointer.novel_id == run.novel_id,
            )
        )
        if pointer is None:
            raise DependencyPaused("no active Phase 07 hierarchy build")
        build = await session.scalar(
            select(ChunkBuild).where(
                ChunkBuild.novel_id == run.novel_id,
                ChunkBuild.build_id == pointer.build_id,
            )
        )
        if build is None or not build.immutable:
            raise DependencyPaused(
                "active Phase 07 hierarchy is unavailable or mutable"
            )
        if run.version_id is None:
            prompt_hash = hashlib.sha256(runtime.extraction_prompt.encode()).hexdigest()
            schema_hash = hashlib.sha256(
                json.dumps(
                    TimelineExtraction.model_json_schema(),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            version = AnalysisVersion(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_key=uuid.uuid4().hex,
                status="candidate",
                source_snapshot_hash=build.source_snapshot_hash,
                hierarchy_build_id=build.build_id,
                hierarchy_checksum=build.manifest_checksum,
                prompt_hash=prompt_hash,
                schema_hash=schema_hash,
                model_lineage={
                    "chapter_extract": runtime.extraction_deployment.lineage,
                    "cross_chapter_reconcile": runtime.reconciliation_deployment.lineage,
                },
                decoding_hash=hashlib.sha256(
                    b"temperature=0;retries=0;stream=false"
                ).hexdigest(),
                config_hash=hashlib.sha256(b"timeline-worker.v1").hexdigest(),
                price_snapshot={
                    "chapter_extract": _prices(runtime.extraction_deployment),
                    "cross_chapter_reconcile": _prices(
                        runtime.reconciliation_deployment
                    ),
                },
                manifest={},
            )
            session.add(version)
            await session.flush()
            run.version_id = version.id
            session.add(
                AnalysisBudgetLedger(
                    run_id=run.id,
                    max_calls=runtime.budget_policy.max_calls,
                    max_input_tokens=runtime.budget_policy.max_input_tokens,
                    max_output_tokens=runtime.budget_policy.max_output_tokens,
                    max_cost_usd=runtime.budget_policy.max_cost_usd,
                )
            )
        else:
            version = await session.get(AnalysisVersion, run.version_id)
            if version is None:
                raise TimelineWorkerError("run references a missing candidate version")
        chapters = list(
            (
                await session.scalars(
                    select(Chapter)
                    .where(
                        Chapter.novel_id == run.novel_id,
                    )
                    .order_by(Chapter.chapter_number, Chapter.id)
                )
            ).all()
        )
        if not chapters:
            raise DependencyPaused("novel has no chapters to analyze")
        return run, version, build, chapters


def _prices(deployment: ModelDeployment) -> dict[str, str]:
    return {
        "provider": deployment.provider,
        "model_id": deployment.model_id,
        "revision": deployment.revision,
        "input_price_per_million": str(deployment.input_price_per_million),
        "output_price_per_million": str(deployment.output_price_per_million),
    }
