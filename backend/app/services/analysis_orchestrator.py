"""Sequential, durable orchestration for the one-click full analysis flow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_factory
from app.models.analysis import AnalysisRun
from app.models.chunk_build import ChunkActivePointer
from app.models.clue import ClueAnalysisRun
from app.models.full_analysis import FullAnalysisRun
from app.models.relationship import RelationshipBuildRun
from app.models.text_chunk import TextChunk
from app.services.indexing_service import indexing_service

logger = logging.getLogger(__name__)

FULL_ANALYSIS_STAGES = (
    "indexing",
    "timeline_extract",
    "timeline_reconcile",
    "relationship_judgment",
    "clue_judgment",
    "nm_chapter_state",
    "nm_arc_plan",
    "nm_aggregate",
)
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "paused_dependency",
    "paused_budget",
}


class FullAnalysisError(RuntimeError):
    """A stage failed and the full pipeline cannot continue."""


class FullAnalysisCancelled(FullAnalysisError):
    pass


def _stage_index(stage: str) -> int:
    try:
        return FULL_ANALYSIS_STAGES.index(stage) + 1
    except ValueError:
        return 0


async def _update_progress(
    run_id: int,
    *,
    stage: str,
    completed: int,
    total: int,
    status: str = "running",
    detail: str | None = None,
) -> None:
    """Persist the small public progress contract used by the frontend."""

    payload: dict[str, Any] = {
        "stage": stage,
        "progress": f"{max(0, completed)}/{max(0, total)}",
        "status": status,
        "stage_index": _stage_index(stage),
        "stage_total": len(FULL_ANALYSIS_STAGES),
    }
    if detail:
        payload["detail"] = detail[:500]
    async with async_session_factory() as session:
        async with session.begin():
            row = await session.get(FullAnalysisRun, run_id, with_for_update=True)
            if row is None:
                return
            row.current_stage = stage
            row.progress = payload


async def _set_run_status(
    run_id: int, status: str, *, reason: str | None = None
) -> None:
    async with async_session_factory() as session:
        async with session.begin():
            row = await session.get(FullAnalysisRun, run_id, with_for_update=True)
            if row is None:
                return
            row.status = status
            row.status_reason = reason[:2000] if reason else None
            if status in {"completed", "failed", "cancelled"}:
                row.active_key = None
            current = dict(row.progress or {})
            current["status"] = status
            row.progress = current


async def _check_cancelled(run_id: int) -> None:
    async with async_session_factory() as session:
        requested = await session.scalar(
            select(FullAnalysisRun.cancel_requested).where(
                FullAnalysisRun.id == run_id
            )
        )
    if requested:
        raise FullAnalysisCancelled("full analysis cancel requested")


async def _index_and_prepare_hierarchy(run_id: int, novel_id: int) -> None:
    from app.models.novel import Novel
    from app.services.analysis_service import ensure_hierarchy

    async with async_session_factory() as session:
        pointer = await session.scalar(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id)
        )
        chunk_total = int(
            await session.scalar(
                select(func.count()).select_from(TextChunk).where(
                    TextChunk.novel_id == novel_id
                )
            )
            or 0
        )
        pending_total = int(
            await session.scalar(
                select(func.count()).select_from(TextChunk).where(
                    TextChunk.novel_id == novel_id,
                    TextChunk.embedding_status == "pending",
                )
            )
            or 0
        )
        failed_total = int(
            await session.scalar(
                select(func.count()).select_from(TextChunk).where(
                    TextChunk.novel_id == novel_id,
                    TextChunk.embedding_status == "failed",
                )
            )
            or 0
        )

        async def indexing_progress(
            _novel_id: int, completed: int, total: int, status: str
        ) -> None:
            await _update_progress(
                run_id,
                stage="indexing",
                completed=completed,
                total=total,
                detail=status,
            )

        if pointer is None or chunk_total == 0:
            await indexing_service.index_novel(
                session, novel_id=novel_id, progress_callback=indexing_progress
            )
        elif pending_total:
            # Resume the exact interrupted embedding batches; do not recreate
            # chunks or delete already indexed vectors.
            await indexing_service.resume_pending_embeddings(
                session, novel_id=novel_id, progress_callback=indexing_progress
            )
        elif failed_total:
            # A failed block means the index is incomplete. Rebuild through the
            # existing idempotent index service so hierarchy and vectors align.
            await indexing_service.index_novel(
                session, novel_id=novel_id, progress_callback=indexing_progress
            )

        novel = await session.get(Novel, novel_id)
        if novel is None:
            raise FullAnalysisError("小说不存在")
        build_id = await ensure_hierarchy(session, novel, force=False)
        if not build_id:
            raise FullAnalysisError("无法准备分块层级：小说没有可分析的章节")
        await session.commit()

    await _update_progress(
        run_id, stage="indexing", completed=1, total=1, detail="hierarchy_ready"
    )


async def _ensure_timeline_run(owner_id: int, novel_id: int) -> tuple[int, str, int | None]:
    from app.services.analysis_service import ensure_hierarchy
    from app.models.novel import Novel

    async with async_session_factory() as session:
        novel = await session.get(Novel, novel_id)
        if novel is None:
            raise FullAnalysisError("小说不存在")
        if not await ensure_hierarchy(session, novel, force=False):
            raise FullAnalysisError("时间线依赖的层级尚未就绪")
        row = await session.scalar(
            select(AnalysisRun)
            .where(
                AnalysisRun.owner_id == owner_id,
                AnalysisRun.novel_id == novel_id,
                AnalysisRun.active_key == "active",
            )
            .with_for_update()
        )
        if row is None:
            row = await session.scalar(
                select(AnalysisRun)
                .where(
                    AnalysisRun.owner_id == owner_id,
                    AnalysisRun.novel_id == novel_id,
                    AnalysisRun.status == "completed",
                    AnalysisRun.version_id.is_not(None),
                )
                .order_by(AnalysisRun.id.desc())
                .limit(1)
            )
        if row is None:
            row = AnalysisRun(
                owner_id=owner_id,
                novel_id=novel_id,
                active_key="active",
                status="pending",
                checkpoint={"orchestrator_managed": True},
                progress={},
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                raise FullAnalysisError("已有时间线任务正在初始化，请稍后重试")
        elif row.status != "completed":
            row.checkpoint = {
                **(row.checkpoint or {}),
                "orchestrator_managed": True,
            }
            if row.status in {
                "failed",
                "cancelled",
                "paused_dependency",
                "paused_budget",
            }:
                row.status = "pending"
                row.cancel_requested = False
                row.status_reason = None
        await session.commit()
        return row.id, row.status, row.version_id


async def _wait_timeline(run_id: int, full_run_id: int, novel_id: int) -> int:
    from app.models.novel import Chapter
    from app.services.timeline.worker import dispatch_timeline_run

    total = 1
    async with async_session_factory() as session:
        total = int(
            await session.scalar(
                select(func.count()).select_from(Chapter).where(
                    Chapter.novel_id == novel_id
                )
            )
            or 1
        )
        domain = await session.get(AnalysisRun, run_id)
    worker_task = None
    if domain is not None and domain.status != "completed":
        worker_task = asyncio.create_task(dispatch_timeline_run(run_id))

    while True:
        await _check_cancelled(full_run_id)
        async with async_session_factory() as session:
            domain = await session.get(AnalysisRun, run_id)
        if domain is None:
            raise FullAnalysisError("时间线任务丢失")
        domain_progress = dict(domain.progress or {})
        completed = int(
            domain_progress.get("completed_chapters")
            or domain_progress.get("chapters_completed")
            or 0
        )
        domain_stage = str(domain_progress.get("stage") or "extracting")
        if "reconcile" in domain_stage:
            await _update_progress(
                full_run_id,
                stage="timeline_reconcile",
                completed=0,
                total=1,
                detail=domain_stage,
            )
        else:
            await _update_progress(
                full_run_id,
                stage="timeline_extract",
                completed=completed,
                total=total,
                detail=domain_stage,
            )
        if domain.status in {"failed", "paused_dependency", "paused_budget", "cancelled"}:
            if worker_task is not None:
                await worker_task
            raise FullAnalysisError(domain.status_reason or f"时间线任务{domain.status}")
        if domain.status == "completed":
            if worker_task is not None:
                try:
                    await worker_task
                except Exception:
                    logger.exception("timeline worker raised after completion")
            if domain.version_id is None:
                raise FullAnalysisError("时间线完成但没有候选版本")
            await _update_progress(
                full_run_id,
                stage="timeline_extract",
                completed=total,
                total=total,
                detail="completed",
            )
            await _update_progress(
                full_run_id,
                stage="timeline_reconcile",
                completed=1,
                total=1,
                detail="completed",
            )
            return int(domain.version_id)
        await asyncio.sleep(0.8)


async def _ensure_relationship_run(
    owner_id: int, novel_id: int, analysis_version_id: int
) -> tuple[int, str]:
    from app.services.relationships.worker import relationship_observation_worker

    async with async_session_factory() as session:
        row = await session.scalar(
            select(RelationshipBuildRun)
            .where(
                RelationshipBuildRun.owner_id == owner_id,
                RelationshipBuildRun.novel_id == novel_id,
                RelationshipBuildRun.analysis_version_id == analysis_version_id,
            )
            .order_by(RelationshipBuildRun.id.desc())
            .limit(1)
        )
        if row is None or row.status in {"failed", "cancelled"}:
            row = await relationship_observation_worker._ensure_build_run(  # noqa: SLF001
                session,
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
                build_run_id=None,
            )
        elif row.status in {"paused_dependency", "paused_budget"}:
            row.status = "pending"
            row.status_reason = None
        await session.commit()
        return int(row.id), row.status


async def _dispatch_relationship_run(
    run_id: int, owner_id: int, novel_id: int, analysis_version_id: int
) -> None:
    from app.services.relationships.worker import relationship_observation_worker

    try:
        async with async_session_factory() as session:
            await relationship_observation_worker.run(
                session,
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
                build_run_id=run_id,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("full analysis relationship worker failed")
        async with async_session_factory() as session:
            async with session.begin():
                row = await session.get(RelationshipBuildRun, run_id, with_for_update=True)
                if row is not None:
                    row.status = "failed"
                    row.status_reason = str(exc)[:160]


async def _wait_relationship(
    full_run_id: int,
    run_id: int,
    owner_id: int,
    novel_id: int,
    analysis_version_id: int,
) -> None:
    async with async_session_factory() as session:
        row = await session.get(RelationshipBuildRun, run_id)
    worker_task = None
    if row is not None and row.status == "pending":
        worker_task = asyncio.create_task(
            _dispatch_relationship_run(run_id, owner_id, novel_id, analysis_version_id)
        )
    while True:
        await _check_cancelled(full_run_id)
        async with async_session_factory() as session:
            row = await session.get(RelationshipBuildRun, run_id)
        if row is None:
            raise FullAnalysisError("人物关系任务丢失")
        completed = int(row.judgment_count or 0)
        total = int(row.candidate_count or 0)
        await _update_progress(
            full_run_id,
            stage="relationship_judgment",
            completed=completed if total else 0,
            total=total or 1,
            detail=row.checkpoint.get("phase") if row.checkpoint else None,
        )
        if row.status in {"failed", "cancelled", "paused_dependency", "paused_budget"}:
            if worker_task is not None:
                await worker_task
            raise FullAnalysisError(row.status_reason or f"人物关系任务{row.status}")
        if row.status == "completed":
            if worker_task is not None:
                await worker_task
            await _update_progress(
                full_run_id, stage="relationship_judgment", completed=1, total=1
            )
            return
        await asyncio.sleep(0.8)


async def _ensure_clue_run(owner_id: int, novel_id: int) -> tuple[int, str]:
    async with async_session_factory() as session:
        row = await session.scalar(
            select(ClueAnalysisRun)
            .where(
                ClueAnalysisRun.owner_id == owner_id,
                ClueAnalysisRun.novel_id == novel_id,
                ClueAnalysisRun.active_key == "active",
            )
            .with_for_update()
        )
        if row is None:
            row = ClueAnalysisRun(
                owner_id=owner_id,
                novel_id=novel_id,
                active_key="active",
                status="pending",
                progress={},
            )
            session.add(row)
            await session.flush()
        elif row.status in {"failed", "cancelled", "paused_dependency", "paused_budget"}:
            row.status = "pending"
            row.cancel_requested = False
            row.status_reason = None
        await session.commit()
        return int(row.id), row.status


async def _wait_clues(full_run_id: int, run_id: int) -> None:
    from app.services.clues.worker import dispatch_clue_run

    async with async_session_factory() as session:
        row = await session.get(ClueAnalysisRun, run_id)
    worker_task = None
    if row is not None and row.status == "pending":
        worker_task = asyncio.create_task(dispatch_clue_run(run_id))
    while True:
        await _check_cancelled(full_run_id)
        async with async_session_factory() as session:
            row = await session.get(ClueAnalysisRun, run_id)
        if row is None:
            raise FullAnalysisError("线索任务丢失")
        progress = dict(row.progress or {})
        completed = int(progress.get("completed_candidates") or 0)
        total = int(progress.get("total_candidates") or 0)
        await _update_progress(
            full_run_id,
            stage="clue_judgment",
            completed=completed,
            total=total or 1,
            detail=str(progress.get("stage") or "judging"),
        )
        if row.status in {"failed", "cancelled", "paused_dependency", "paused_budget"}:
            if worker_task is not None:
                try:
                    await worker_task
                except Exception:
                    pass
            raise FullAnalysisError(row.status_reason or f"线索任务{row.status}")
        if row.status == "completed":
            if worker_task is not None:
                try:
                    await worker_task
                except Exception:
                    logger.exception("clue worker raised after completion")
            await _update_progress(full_run_id, stage="clue_judgment", completed=1, total=1)
            return
        await asyncio.sleep(0.8)


async def _run_nm(full_run_id: int, owner_id: int, novel_id: int) -> None:
    from scripts.run_narrative_memory_build import run_narrative_memory_build

    async def callback(stage: str, completed: int, total: int, status: str) -> None:
        await _check_cancelled(full_run_id)
        await _update_progress(
            full_run_id,
            stage=stage,
            completed=completed,
            total=total or 1,
            status="running",
            detail=status,
        )

    result = await run_narrative_memory_build(
        owner_id=owner_id,
        novel_id=novel_id,
        progress_callback=callback,
    )
    if result.get("status") != "completed":
        raise FullAnalysisError(
            result.get("status_reason") or f"叙事记忆任务{result.get('status')}"
        )
    await _update_progress(
        full_run_id, stage="nm_aggregate", completed=1, total=1, detail="candidate_ready"
    )


async def run_full_analysis(run_id: int) -> None:
    """BackgroundTasks entrypoint for the sequential full-analysis pipeline."""

    async with async_session_factory() as session:
        async with session.begin():
            row = await session.get(FullAnalysisRun, run_id, with_for_update=True)
            if row is None or row.status in {"completed", "cancelled"}:
                return
            row.status = "running"
            owner_id = int(row.owner_id)
            novel_id = int(row.novel_id)
    try:
        await _check_cancelled(run_id)
        await _update_progress(run_id, stage="indexing", completed=0, total=1)
        await _index_and_prepare_hierarchy(run_id, novel_id)

        await _check_cancelled(run_id)
        timeline_run_id, timeline_status, timeline_version_id = await _ensure_timeline_run(
            owner_id, novel_id
        )
        if timeline_status == "completed" and timeline_version_id is not None:
            await _update_progress(run_id, stage="timeline_extract", completed=1, total=1)
            await _update_progress(run_id, stage="timeline_reconcile", completed=1, total=1)
        else:
            timeline_version_id = await _wait_timeline(
                timeline_run_id, run_id, novel_id
            )

        await _check_cancelled(run_id)
        rel_run_id, rel_status = await _ensure_relationship_run(
            owner_id, novel_id, int(timeline_version_id)
        )
        if rel_status == "completed":
            await _update_progress(run_id, stage="relationship_judgment", completed=1, total=1)
        else:
            await _wait_relationship(
                run_id,
                rel_run_id,
                owner_id,
                novel_id,
                int(timeline_version_id),
            )

        await _check_cancelled(run_id)
        clue_run_id, clue_status = await _ensure_clue_run(owner_id, novel_id)
        if clue_status == "completed":
            await _update_progress(run_id, stage="clue_judgment", completed=1, total=1)
        else:
            await _wait_clues(run_id, clue_run_id)

        await _check_cancelled(run_id)
        await _update_progress(run_id, stage="nm_chapter_state", completed=0, total=1)
        await _run_nm(run_id, owner_id, novel_id)
        await _set_run_status(run_id, "completed")
    except FullAnalysisCancelled as exc:
        await _set_run_status(run_id, "cancelled", reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("full analysis failed run=%s", run_id)
        await _set_run_status(run_id, "failed", reason=f"{type(exc).__name__}: {exc}")
