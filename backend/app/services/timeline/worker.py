"""Durable production orchestration for versioned timeline analysis.

This module owns the orchestration core — run lease + dispatch
(``run_timeline_worker`` / ``dispatch_timeline_run``), the chapter-extraction
stage (``_extract_and_persist``), promotion (``_validate_and_promote``),
progress/status finalization (``_update_progress`` / ``_finish_run``) and
dependent analysis dispatch (``_dispatch_dependent_analysis``).

拆分说明（refactor split）：runtime/transport 构造拆到 ``_worker_runtime.py``
（``TimelineWorkerRuntime`` / ``_LiteLLMTransport`` / ``_VertexTransport`` /
``production_runtime`` / ``_load_prompt``），run 状态与 pre-flight 拆到
``_worker_prepare.py``（``TimelineWorkerError`` /
``TimelineCancellationRequested`` / ``_claim_run`` / ``_raise_if_cancel_requested`` /
``_prepare_run`` / ``_prices``），章节持久化拆到 ``_worker_persist.py``
（``_persist_chapter`` + character FK fail-soft），跨章调和拆到
``_worker_reconcile.py``（``_load_persisted_candidates`` /
``_reconcile_and_persist``）。所有名字在本模块 re-export ——
``TimelineWorkerRuntime`` / ``dispatch_timeline_run`` / ``production_runtime`` /
``run_timeline_worker`` 及全部私有 helper 的 import surface 不变。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.analysis import AnalysisChapterStage, AnalysisRun, AnalysisVersion
from app.models.chunk_build import ChunkHierarchyNode
from app.models.novel import Novel
from app.models.timeline import TimelineActivePointer
from app.schemas.timeline import TimelineExtraction
from app.services.timeline.budget import BudgetExceeded, BudgetGate
from app.services.timeline.evidence import (
    EvidencePackage,
    EvidenceUnit,
    rebind_extraction_to_package,
    validate_extraction,
)
from app.services.timeline.extraction import ExactCacheKey, load_persistent_exact_cache
from app.services.timeline.model_gateway import DependencyPaused, ModelCallFailed
from app.services.timeline.promotion import promote_version, snapshot_manifest
from app.services.timeline._worker_prepare import (
    TimelineCancellationRequested,
    TimelineWorkerError,
    _claim_run,
    _prepare_run,
    _prices,
    _raise_if_cancel_requested,
)
from app.services.timeline._worker_persist import (
    _load_character_ids,
    _load_character_registry,
    _persist_chapter,
    _sanitize_participant_entity_ids,
)
from app.services.timeline._worker_reconcile import (
    _load_persisted_candidates,
    _reconcile_and_persist,
)
from app.services.timeline._worker_runtime import (
    TimelineWorkerRuntime,
    _LiteLLMTransport,
    _VertexTransport,
    _load_prompt,
    production_runtime,
)

# Full public import surface — unchanged by the split. No star-import
# consumers; __all__ documents parity and marks re-exports for ruff F401.
__all__ = [
    # orchestration entrypoints
    "dispatch_timeline_run",
    "run_timeline_worker",
    # runtime construction (re-exported from _worker_runtime)
    "TimelineWorkerRuntime",
    "_LiteLLMTransport",
    "_VertexTransport",
    "production_runtime",
    "_load_prompt",
    # run state & pre-flight (re-exported from _worker_prepare)
    "TimelineWorkerError",
    "TimelineCancellationRequested",
    "_claim_run",
    "_raise_if_cancel_requested",
    "_prepare_run",
    "_prices",
    # chapter persistence (re-exported from _worker_persist)
    "_persist_chapter",
    "_load_character_ids",
    "_load_character_registry",
    "_sanitize_participant_entity_ids",
    # cross-chapter reconcile (re-exported from _worker_reconcile)
    "_load_persisted_candidates",
    "_reconcile_and_persist",
    # orchestration stages owned by this module
    "_extract_and_persist",
    "_validate_and_promote",
    "_dispatch_dependent_analysis",
    "_update_progress",
    "_finish_run",
    "_clip_status_reason",
]


async def dispatch_timeline_run(run_id: int) -> None:
    """BackgroundTasks entrypoint; durable checkpoints make repeated dispatch safe."""
    await run_timeline_worker(run_id, runtime=production_runtime())


async def run_timeline_worker(run_id: int, *, runtime: TimelineWorkerRuntime) -> None:
    lease_id = uuid.uuid4().hex
    if not await _claim_run(runtime.sessions, run_id, lease_id):
        return
    try:
        run, version, build, chapters = await _prepare_run(runtime, run_id)
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        budget = BudgetGate(runtime.budget_policy)
        for completed, chapter in enumerate(chapters, start=1):
            await _extract_and_persist(runtime, budget, run, version, build, chapter)
            await _raise_if_cancel_requested(runtime.sessions, run_id)
            await _update_progress(
                runtime.sessions, run.id, completed, len(chapters), "extracting"
            )
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        await _reconcile_and_persist(runtime, budget, run, version)
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        await _validate_and_promote(runtime.sessions, run, version)
    except TimelineCancellationRequested:
        await _finish_run(runtime.sessions, run_id, "cancelled", "cancel requested")
        return
    except DependencyPaused as exc:
        await _finish_run(runtime.sessions, run_id, "paused_dependency", str(exc))
        return
    except ModelCallFailed as exc:
        await _finish_run(runtime.sessions, run_id, "paused_dependency", str(exc))
        return
    except BudgetExceeded as exc:
        await _finish_run(runtime.sessions, run_id, "paused_budget", str(exc))
        return
    except Exception as exc:
        await _finish_run(runtime.sessions, run_id, "failed", type(exc).__name__)
        raise


async def _extract_and_persist(runtime, budget, run, version, build, chapter) -> None:
    stage_key = f"chapter_extract:{chapter.id}"
    async with runtime.sessions() as session:
        stage = await session.scalar(
            select(AnalysisChapterStage).where(
                AnalysisChapterStage.run_id == run.id,
                AnalysisChapterStage.stage_key == stage_key,
                AnalysisChapterStage.status == "completed",
            )
        )
        if stage is not None:
            return
        nodes = list(
            (
                await session.scalars(
                    select(ChunkHierarchyNode)
                    .where(
                        ChunkHierarchyNode.build_id == build.build_id,
                        ChunkHierarchyNode.novel_id == run.novel_id,
                        ChunkHierarchyNode.chapter_id == chapter.id,
                        ChunkHierarchyNode.level == "evidence",
                    )
                    .order_by(
                        ChunkHierarchyNode.order_index, ChunkHierarchyNode.node_id
                    )
                )
            ).all()
        )
    if not nodes:
        raise DependencyPaused(f"chapter {chapter.id} has no Phase 07 evidence")
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    character_registry = await _load_character_registry(runtime.sessions, run.novel_id)
    package = EvidencePackage.create(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        chapter_id=chapter.id,
        unit_id=f"chapter:{chapter.id}",
        source_snapshot_hash=build.source_snapshot_hash,
        hierarchy_build_id=build.build_id,
        hierarchy_checksum=build.manifest_checksum,
        units=[
            EvidenceUnit(
                node.node_id,
                node.source_start,
                node.source_end,
                node.content,
                node.content_hash,
            )
            for node in nodes
        ],
    )
    cache_key = ExactCacheKey.for_package(
        package,
        stage="chapter_extract",
        prompt_hash=version.prompt_hash,
        schema_hash=version.schema_hash,
        model_provider=runtime.extraction_deployment.provider,
        model_id=runtime.extraction_deployment.model_id,
        model_revision=runtime.extraction_deployment.revision,
        decoding_hash=version.decoding_hash,
        config_hash=version.config_hash,
    )
    output = None
    if runtime.gateway.persistence is not None:
        cached = await load_persistent_exact_cache(runtime.sessions, cache_key.digest)
        if cached is not None:
            output = TimelineExtraction.model_validate(
                cached.gateway_output, strict=True
            )
            validate_extraction(package, output)
            await runtime.gateway.persistence.record_cache_hit(
                run_id=run.id,
                stage_key=stage_key,
                cache_key=cache_key.digest,
                source_attempt_id=cached.source_attempt_id,
                artifact_checksum=cached.artifact_checksum,
            )
    if output is None:
        result = await runtime.gateway.generate(
            deployment=runtime.extraction_deployment,
            schema=TimelineExtraction,
            messages=[
                {"role": "system", "content": runtime.extraction_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "scope": {
                                "owner_id": run.owner_id,
                                "novel_id": run.novel_id,
                                "chapter_id": chapter.id,
                                "unit_id": package.unit_id,
                            },
                            "lineage": {
                                "source_snapshot_hash": package.source_snapshot_hash,
                                "hierarchy_build_id": package.hierarchy_build_id,
                                "hierarchy_checksum": package.hierarchy_checksum,
                                "evidence_package_hash": package.package_hash,
                            },
                            "characters": character_registry,
                            "evidence": [unit.__dict__ for unit in package.units],
                        },
                        sort_keys=True,
                    ),
                },
            ],
            budget=budget,
            run_id=run.id,
            stage_key=stage_key,
            cache_key=cache_key.digest,
            # 证据全文 + JSON schema/system 开销；实测单章可到 60k+ prompt tokens
            max_input_tokens=min(
                200_000,
                max(
                    128_000,
                    sum(len(unit.text) for unit in package.units) + 80_000,
                ),
            ),
            max_output_tokens=16_384,
            business_validator=lambda candidate: validate_extraction(
                package, rebind_extraction_to_package(package, candidate)
            ),
        )
        # 脚本权威：offsets/hash/chapter_id 一律以 Phase 07 证据包为准
        output = rebind_extraction_to_package(package, result.output)
        validate_extraction(package, output)
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    await _persist_chapter(runtime.sessions, run, version, chapter, stage_key, output)


async def _validate_and_promote(sessions, run, version) -> None:
    async with sessions.begin() as session:
        current = await session.get(AnalysisVersion, version.id, with_for_update=True)
        manifest, checksum = await snapshot_manifest(session, version.id)
        if not manifest["events"] or not manifest["evidence"]:
            raise TimelineWorkerError("candidate graph is incomplete")
        current.manifest = manifest
        current.manifest_checksum = checksum
        current.validated_at = datetime.now(UTC)
        current.status = "candidate"
    async with sessions() as session:
        pointer = await session.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == run.owner_id,
                TimelineActivePointer.novel_id == run.novel_id,
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
    await _dispatch_dependent_analysis(sessions, run, version.id)


async def _dispatch_dependent_analysis(sessions, run, version_id: int) -> None:
    """After timeline promote: always enqueue relationship + clue workers.

    Product contract: 开始分析 → 时间线主链路；完成后并行关系与线索。
    Clue may have been started earlier in parallel (FE); re-queue failed/paused runs.
    """
    from app.models.clue import ClueAnalysisRun
    from app.services.clues.worker import dispatch_clue_run
    from app.services.relationships.worker import dispatch_relationship_build

    clue_run_id = None
    async with sessions.begin() as session:
        clue_run = await session.scalar(
            select(ClueAnalysisRun)
            .where(
                ClueAnalysisRun.owner_id == run.owner_id,
                ClueAnalysisRun.novel_id == run.novel_id,
                ClueAnalysisRun.active_key == "active",
            )
            .with_for_update()
        )
        if clue_run is None:
            clue_run = ClueAnalysisRun(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                active_key="active",
                status="pending",
                progress={},
            )
            session.add(clue_run)
            await session.flush()
        elif clue_run.status in (
            "paused_dependency",
            "paused_budget",
            "failed",
            "cancelled",
            "pending",
        ):
            clue_run.status = "pending"
            clue_run.status_reason = None
            clue_run.cancel_requested = False
        # completed clue stays completed (no force re-run here)
        if clue_run.status != "completed":
            clue_run_id = clue_run.id

    # Schedule on the running loop so uvicorn keeps the task after response.
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(
            dispatch_relationship_build(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                analysis_version_id=version_id,
            )
        )
        if clue_run_id is not None:
            loop.create_task(dispatch_clue_run(clue_run_id))
    except RuntimeError:
        # No running loop (CLI tooling): run sequentially.
        await dispatch_relationship_build(
            owner_id=run.owner_id,
            novel_id=run.novel_id,
            analysis_version_id=version_id,
        )
        if clue_run_id is not None:
            await dispatch_clue_run(clue_run_id)


async def _update_progress(sessions, run_id, completed, total, stage) -> None:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        progress = dict(run.progress or {})
        if completed is not None:
            progress["completed_chapters"] = completed
        if total is not None:
            progress["total_chapters"] = total
        progress["stage"] = stage
        run.progress = progress
        now = datetime.now(UTC)
        run.heartbeat_at = now
        if run.status == "running" and run.lease_id:
            run.lease_expires_at = now + timedelta(minutes=5)


def _clip_status_reason(reason: str | None, *, limit: int = 128) -> str | None:
    """analysis_runs.status_reason is VARCHAR(128); never let long provider errors fail flush."""
    if reason is None:
        return None
    text = str(reason).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


async def _finish_run(sessions, run_id: int, status: str, reason: str | None) -> None:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None:
            return
        run.status = status
        run.status_reason = _clip_status_reason(reason)
        run.lease_id = None
        run.lease_expires_at = None
        run.heartbeat_at = datetime.now(UTC)
        if status == "completed":
            run.progress = {**(run.progress or {}), "stage": "completed"}
        # 书架状态与时间线任务对齐（Phase 08 产品面）
        novel = await session.get(Novel, run.novel_id)
        if novel is not None:
            if status == "completed":
                novel.status = "analyzed"
            elif status in ("running", "pending", "partial"):
                novel.status = "analyzing"
            elif (
                status in ("paused_dependency", "paused_budget", "failed")
                and novel.status == "analyzing"
            ):
                # 保留 analyzing 或回 ready 都不理想；失败时标 ready 便于重试入口
                novel.status = "ready"
