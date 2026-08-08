"""Durable candidate clue analysis orchestration.

Claims leases, freezes lineage, reserves budget before each model call,
persists stage checkpoints, qualifies a complete version, and moves the
active pointer only via CAS. Failed candidates never move active.

拆分说明（refactor split）：逐候选判断/持久化 seam 拆到 ``_worker_judge.py``，
标题生成纯函数拆到 ``_worker_titles.py``，运行时契约/异常/成本/哈希原语下沉到
叶模块 ``_worker_primitives.py``。本模块保留编排核心（claim/prepare/candidates/
promote/finish + ``production_runtime`` / ``dispatch_clue_run`` /
``run_clue_worker``）并 re-export 全部原顶层符号——``app.services.clues.worker``
的 import surface 不变。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueBudgetLedger,
)
from app.models.novel import Chapter
from app.models.timeline import MachineTimelineEvent, TimelineActivePointer
from app.services.clues.budget import (
    BudgetExceeded,
    BudgetGate,
    ClueCallRepository,
    UnknownPricing,
)
from app.services.clues.candidates import (
    CandidateRecallConfig,
    ClueCandidateDraft,
    HierarchyEvidenceNode,
    TimelineEventRef,
)
from app.services.clues.gates import policy_hash
from app.services.clues.llm_judge import ClueLLMJudgeService
from app.services.clues.versions import promote_version, snapshot_manifest

from ._worker_judge import (
    _exact_cache_key,
    _judge_and_persist,
    _mark_stage_completed,
    _persist_decision,
    _stage_completed,
    _unit_to_evidence_dict,
)
from ._worker_primitives import (
    CONFIG_HASH,
    DECODING_HASH,
    COST_REASON_UNKNOWN_PRICING,
    ClueCancellationRequested,
    ClueModelDeployment,
    ClueWorkerError,
    ClueWorkerRuntime,
    DependencyPaused,
    compute_actual_cost_usd,
)
from ._worker_titles import (
    MAX_SHORT_TITLE_LEN,
    TITLE_SOURCE_JUDGE_SHORT_TITLE,
    TITLE_SOURCE_RATIONALE_OR_STEM,
    _clean_short_title,
    _clean_title_stem,
    build_machine_clue_title,
    resolve_machine_clue_title,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CONFIG_HASH",
    "DECODING_HASH",
    "COST_REASON_UNKNOWN_PRICING",
    "MAX_SHORT_TITLE_LEN",
    "TITLE_SOURCE_JUDGE_SHORT_TITLE",
    "TITLE_SOURCE_RATIONALE_OR_STEM",
    "ClueCancellationRequested",
    "ClueModelDeployment",
    "ClueWorkerError",
    "ClueWorkerRuntime",
    "DependencyPaused",
    "build_machine_clue_title",
    "compute_actual_cost_usd",
    "dispatch_clue_run",
    "production_runtime",
    "resolve_machine_clue_title",
    "run_clue_worker",
    "_build_candidates",
    "_claim_run",
    "_clean_short_title",
    "_clean_title_stem",
    "_exact_cache_key",
    "_finish_run",
    "_judge_and_persist",
    "_mark_stage_completed",
    "_persist_decision",
    "_prepare_run",
    "_raise_if_cancel_requested",
    "_stage_completed",
    "_unit_to_evidence_dict",
    "_update_progress",
    "_validate_and_promote",
]


def production_runtime() -> ClueWorkerRuntime:
    """Build production runtime with judge model frozen to the same deployment.

    Budget/lineage used vertex while the judge previously fell back to
    ``ai_router.route_task("extraction")`` → ``openai/gpt-4o-mini`` (no key),
    producing ``provider_error:AuthenticationError`` and 0 clues. Wire the
    judge to the selected provider/model explicitly (same pattern as reader_chat).
    """
    from app.config import settings

    provider = (settings.chat_provider or "vertex_google").strip().lower()
    use_vertex = (
        provider
        in (
            "vertex_google",
            "vertex",
            "vertex_ai",
            "gcp",
            "google_cloud",
        )
        or not (settings.openai_api_key or "").strip()
    )
    if use_vertex:
        model_id = (settings.vertex_model or "gemini-3.5-flash-lite").strip()
        for prefix in (
            "vertex_google/",
            "vertex_ai/",
            "vertex/",
            "gcp/",
            "google/",
        ):
            if model_id.lower().startswith(prefix):
                model_id = model_id[len(prefix) :]
                break
        model_id = model_id or "gemini-3.5-flash-lite"
        deployment = ClueModelDeployment(
            "vertex_google",
            model_id,
            model_id,
            Decimal("0.10"),
            Decimal("0.40"),
        )
        judge_model = f"vertex_google/{model_id}"
    else:
        model_id = "gpt-4o-mini-2024-07-18"
        deployment = ClueModelDeployment(
            "openai", model_id, model_id, Decimal("0.15"), Decimal("0.60")
        )
        judge_model = f"openai/{model_id}"
    sessions = async_session_factory
    return ClueWorkerRuntime(
        sessions=sessions,
        call_repo=ClueCallRepository(sessions),
        deployment=deployment,
        judge=ClueLLMJudgeService(model_name=judge_model),
    )


async def dispatch_clue_run(run_id: int) -> None:
    await run_clue_worker(run_id, runtime=production_runtime())


async def run_clue_worker(run_id: int, *, runtime: ClueWorkerRuntime) -> None:
    lease_id = uuid.uuid4().hex
    if not await _claim_run(runtime.sessions, run_id, lease_id):
        return
    try:
        run, version, build = await _prepare_run(runtime, run_id)
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        budget = BudgetGate(runtime.budget_policy)
        drafts = await _build_candidates(runtime, run, version, build)
        await _update_progress(
            runtime.sessions,
            run.id,
            completed=0,
            total=len(drafts),
            stage="judging",
        )
        for index, draft in enumerate(drafts, start=1):
            await _raise_if_cancel_requested(runtime.sessions, run_id)
            await _judge_and_persist(runtime, budget, run, version, draft)
            await _update_progress(
                runtime.sessions,
                run.id,
                completed=index,
                total=len(drafts),
                stage="judging",
            )
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        await _validate_and_promote(runtime.sessions, run, version)
    except ClueCancellationRequested:
        await _finish_run(runtime.sessions, run_id, "cancelled", "cancel requested")
        return
    except DependencyPaused as exc:
        await _finish_run(runtime.sessions, run_id, "paused_dependency", str(exc)[:160])
        return
    except (BudgetExceeded, UnknownPricing) as exc:
        await _finish_run(runtime.sessions, run_id, "paused_budget", str(exc)[:160])
        return
    except Exception as exc:
        # Keep type + message so operators see e.g. ClueEvidenceScopeError: hierarchy...
        detail = f"{type(exc).__name__}: {exc}"[:160]
        await _finish_run(runtime.sessions, run_id, "failed", detail)
        raise


async def _claim_run(
    sessions: async_sessionmaker[AsyncSession], run_id: int, lease_id: str
) -> bool:
    async with sessions.begin() as session:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
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
    sessions: async_sessionmaker[AsyncSession], run_id: int
) -> None:
    async with sessions() as session:
        cancelled = await session.scalar(
            select(ClueAnalysisRun.cancel_requested).where(ClueAnalysisRun.id == run_id)
        )
    if cancelled:
        raise ClueCancellationRequested


async def _prepare_run(runtime: ClueWorkerRuntime, run_id: int):
    async with runtime.sessions.begin() as session:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        if run is None:
            raise ClueWorkerError("clue run does not exist")
        pointer = await session.scalar(
            select(ChunkActivePointer).where(
                ChunkActivePointer.novel_id == run.novel_id
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

        timeline_version_id = None
        timeline_checksum = None
        tl_pointer = await session.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == run.owner_id,
                TimelineActivePointer.novel_id == run.novel_id,
            )
        )
        if tl_pointer is not None:
            timeline_version_id = tl_pointer.version_id
            timeline_checksum = tl_pointer.manifest_checksum

        if run.version_id is None:
            prompt_hash = runtime.judge.prompt_hash
            schema_hash = runtime.judge.schema_hash
            version = ClueAnalysisVersion(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_key=uuid.uuid4().hex,
                status="candidate",
                source_snapshot_hash=build.source_snapshot_hash,
                hierarchy_build_id=build.build_id,
                hierarchy_checksum=build.manifest_checksum,
                timeline_version_id=timeline_version_id,
                timeline_checksum=timeline_checksum,
                prompt_hash=prompt_hash,
                schema_hash=schema_hash,
                decoding_hash=DECODING_HASH,
                config_hash=CONFIG_HASH,
                policy_hash=policy_hash(),
                model_lineage={"clue_semantic_judge": runtime.deployment.lineage},
                price_snapshot={
                    "clue_semantic_judge": {
                        "provider": runtime.deployment.provider,
                        "model_id": runtime.deployment.model_id,
                        "revision": runtime.deployment.revision,
                        "input_price_per_million": (
                            str(runtime.deployment.input_price_per_million)
                            if runtime.deployment.input_price_per_million is not None
                            else None
                        ),
                        "output_price_per_million": (
                            str(runtime.deployment.output_price_per_million)
                            if runtime.deployment.output_price_per_million is not None
                            else None
                        ),
                    }
                },
                manifest={},
            )
            session.add(version)
            await session.flush()
            run.version_id = version.id
            session.add(
                ClueBudgetLedger(
                    run_id=run.id,
                    max_calls=runtime.budget_policy.max_calls,
                    max_input_tokens=runtime.budget_policy.max_input_tokens,
                    max_output_tokens=runtime.budget_policy.max_output_tokens,
                    max_cost_usd=runtime.budget_policy.max_cost_usd,
                )
            )
        else:
            version = await session.get(ClueAnalysisVersion, run.version_id)
            if version is None:
                raise ClueWorkerError("run references a missing candidate version")
        # Detach simple values for use outside the session.
        return run, version, build


async def _build_candidates(
    runtime: ClueWorkerRuntime,
    run: ClueAnalysisRun,
    version: ClueAnalysisVersion,
    build: ChunkBuild,
) -> list[ClueCandidateDraft]:
    async with runtime.sessions() as session:
        nodes_orm = list(
            (
                await session.scalars(
                    select(ChunkHierarchyNode)
                    .where(
                        ChunkHierarchyNode.build_id == build.build_id,
                        ChunkHierarchyNode.novel_id == run.novel_id,
                        ChunkHierarchyNode.level == "evidence",
                    )
                    .order_by(
                        ChunkHierarchyNode.order_index, ChunkHierarchyNode.node_id
                    )
                )
            ).all()
        )
        if not nodes_orm:
            raise DependencyPaused("hierarchy has no Phase 07 evidence nodes")
        chapters = {
            c.id: c
            for c in (
                await session.scalars(
                    select(Chapter).where(Chapter.novel_id == run.novel_id)
                )
            ).all()
        }
        timeline_events: list[TimelineEventRef] = []
        if version.timeline_version_id is not None:
            events = list(
                (
                    await session.scalars(
                        select(MachineTimelineEvent).where(
                            MachineTimelineEvent.version_id
                            == version.timeline_version_id,
                            MachineTimelineEvent.owner_id == run.owner_id,
                            MachineTimelineEvent.novel_id == run.novel_id,
                        )
                    )
                ).all()
            )
            for ev in events:
                timeline_events.append(
                    TimelineEventRef(
                        event_id=ev.id,
                        chapter_id=0,
                        narrative_chapter_number=ev.narrative_chapter_number,
                        source_start=0,
                        title=ev.title or "",
                    )
                )
        nodes = [
            HierarchyEvidenceNode(
                node_id=n.node_id,
                chapter_id=int(n.chapter_id or 0),
                narrative_chapter_number=int(
                    n.chapter_number
                    or (
                        chapters[n.chapter_id].chapter_number
                        if n.chapter_id in chapters
                        else 1
                    )
                ),
                source_start=int(n.source_start or 0),
                source_end=int(n.source_end or 0),
                content_hash=n.content_hash,
                content=n.content or "",
                order_index=int(n.order_index or 0),
            )
            for n in nodes_orm
            if n.chapter_id
        ]
    result = await runtime.recall.build_candidates_from_nodes(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        nodes=nodes,
        source_snapshot_hash=version.source_snapshot_hash,
        hierarchy_build_id=version.hierarchy_build_id,
        hierarchy_checksum=version.hierarchy_checksum,
        timeline_events=timeline_events,
        timeline_version_id=version.timeline_version_id,
        timeline_checksum=version.timeline_checksum,
        config=CandidateRecallConfig(max_candidates=32),
    )
    return list(result.drafts)


async def _validate_and_promote(
    sessions: async_sessionmaker[AsyncSession],
    run: ClueAnalysisRun,
    version: ClueAnalysisVersion,
) -> None:
    async with sessions.begin() as session:
        current = await session.get(
            ClueAnalysisVersion, version.id, with_for_update=True
        )
        if current is None:
            raise ClueWorkerError("version missing at qualify")
        manifest, checksum = await snapshot_manifest(session, version.id)
        # Qualification: version may have zero accepted clues; still valid complete.
        current.manifest = manifest
        current.manifest_checksum = checksum
        current.validated_at = datetime.now(UTC)
        current.status = "validated"
    async with sessions() as session:
        pointer = await session.scalar(
            select(ClueActivePointer).where(
                ClueActivePointer.owner_id == run.owner_id,
                ClueActivePointer.novel_id == run.novel_id,
            )
        )
        expected_revision = pointer.revision if pointer else 0
        await promote_version(
            session,
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            candidate_version_id=version.id,
            expected_revision=expected_revision,
        )
    await _update_progress(sessions, run.id, None, None, "completed")
    await _finish_run(sessions, run.id, "completed", None)


async def _update_progress(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
    completed: int | None,
    total: int | None,
    stage: str,
) -> None:
    async with sessions.begin() as session:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        if run is None:
            return
        progress = dict(run.progress or {})
        if completed is not None:
            progress["completed_candidates"] = completed
        if total is not None:
            progress["total_candidates"] = total
        progress["stage"] = stage
        run.progress = progress
        now = datetime.now(UTC)
        run.heartbeat_at = now
        if run.status == "running" and run.lease_id:
            run.lease_expires_at = now + timedelta(minutes=5)


async def _finish_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
    status: str,
    reason: str | None,
) -> None:
    async with sessions.begin() as session:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        if run is None:
            return
        # Do not overwrite completed with a later failure race.
        if run.status == "completed" and status != "completed":
            return
        run.status = status
        run.status_reason = reason
        if status in {"completed", "cancelled", "failed"}:
            run.active_key = None
        progress = dict(run.progress or {})
        progress["stage"] = status
        run.progress = progress
