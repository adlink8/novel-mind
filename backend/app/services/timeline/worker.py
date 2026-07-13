"""Durable production orchestration for versioned timeline analysis."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.analysis import (
    AnalysisBudgetLedger,
    AnalysisChapterStage,
    AnalysisRun,
    AnalysisVersion,
)
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.schemas.timeline import EventCandidate, EvidenceRef, Participant, StoryTime, TimelineExtraction
from app.services.timeline.budget import BudgetExceeded, BudgetGate, BudgetPolicy
from app.services.timeline.evidence import EvidencePackage, EvidenceUnit, validate_extraction
from app.services.timeline.extraction import ExactCacheKey, load_persistent_exact_cache
from app.services.timeline.model_gateway import (
    DependencyPaused,
    ModelCallFailed,
    ModelDeployment,
    PostgresCallRepository,
    TimelineModelGateway,
)
from app.services.timeline.promotion import promote_version, snapshot_manifest
from app.services.timeline.reconcile import (
    RECONCILIATION_PROMPT,
    ReconciliationOutputModel,
    TimelineReconciler,
    reconciliation_contract_hashes,
)


class TimelineWorkerError(RuntimeError):
    """A deterministic production pipeline precondition failed."""


class TimelineCancellationRequested(RuntimeError):
    """The durable run was cancelled while the production worker was active."""


@dataclass(frozen=True)
class TimelineWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    gateway: TimelineModelGateway
    extraction_deployment: ModelDeployment
    reconciliation_deployment: ModelDeployment
    extraction_prompt: str = "Extract only evidence-backed timeline events from the supplied package."
    budget_policy: BudgetPolicy = field(default_factory=lambda: BudgetPolicy(
        max_calls=500,
        max_input_tokens=2_000_000,
        max_output_tokens=500_000,
        max_cost_usd=Decimal("25"),
    ))


class _LiteLLMTransport:
    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        import litellm

        response = await litellm.acompletion(**kwargs)
        usage = getattr(response, "usage", {})
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        message = response.choices[0].message
        return {
            "id": getattr(response, "id", None),
            "content": message.content,
            "usage": usage,
        }


def production_runtime() -> TimelineWorkerRuntime:
    """Construct the frozen no-fallback Phase 08 deployment pair."""
    import litellm

    extraction_model = "gpt-4o-mini-2024-07-18"
    reconciliation_model = "gpt-4o-2024-08-06"
    return TimelineWorkerRuntime(
        sessions=async_session_factory,
        gateway=TimelineModelGateway(
            _LiteLLMTransport(), persistence=PostgresCallRepository(async_session_factory),
        ),
        extraction_deployment=ModelDeployment(
            "openai", extraction_model, extraction_model,
            bool(litellm.supports_response_schema(extraction_model, custom_llm_provider="openai")),
            Decimal("0.15"), Decimal("0.60"),
        ),
        reconciliation_deployment=ModelDeployment(
            "openai", reconciliation_model, reconciliation_model,
            bool(litellm.supports_response_schema(reconciliation_model, custom_llm_provider="openai")),
            Decimal("2.50"), Decimal("10.00"),
        ),
        extraction_prompt=_load_prompt(),
    )


def _load_prompt() -> str:
    path = Path(__file__).resolve().parents[3] / "prompts" / "timeline_chapter_extract.v1.txt"
    return path.read_text(encoding="utf-8")


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
            await _update_progress(runtime.sessions, run.id, completed, len(chapters), "extracting")
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


async def _claim_run(
    sessions: async_sessionmaker[AsyncSession], run_id: int, lease_id: str,
) -> bool:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None or run.status == "completed" or run.cancel_requested:
            return False
        now = datetime.now(UTC)
        if run.lease_id and run.lease_id != lease_id and run.lease_expires_at and run.lease_expires_at > now:
            return False
        run.lease_id = lease_id
        run.lease_expires_at = now + timedelta(minutes=5)
        run.heartbeat_at = now
        run.status = "running"
        return True


async def _raise_if_cancel_requested(
    sessions: async_sessionmaker[AsyncSession], run_id: int,
) -> None:
    async with sessions() as session:
        cancelled = await session.scalar(select(AnalysisRun.cancel_requested).where(
            AnalysisRun.id == run_id,
        ))
    if cancelled:
        raise TimelineCancellationRequested


async def _prepare_run(runtime: TimelineWorkerRuntime, run_id: int):
    async with runtime.sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None:
            raise TimelineWorkerError("analysis run does not exist")
        pointer = await session.scalar(select(ChunkActivePointer).where(
            ChunkActivePointer.novel_id == run.novel_id,
        ))
        if pointer is None:
            raise DependencyPaused("no active Phase 07 hierarchy build")
        build = await session.scalar(select(ChunkBuild).where(
            ChunkBuild.novel_id == run.novel_id,
            ChunkBuild.build_id == pointer.build_id,
        ))
        if build is None or not build.immutable:
            raise DependencyPaused("active Phase 07 hierarchy is unavailable or mutable")
        if run.version_id is None:
            prompt_hash = hashlib.sha256(runtime.extraction_prompt.encode()).hexdigest()
            schema_hash = hashlib.sha256(json.dumps(
                TimelineExtraction.model_json_schema(), sort_keys=True,
            ).encode()).hexdigest()
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
                decoding_hash=hashlib.sha256(b"temperature=0;retries=0;stream=false").hexdigest(),
                config_hash=hashlib.sha256(b"timeline-worker.v1").hexdigest(),
                price_snapshot={
                    "chapter_extract": _prices(runtime.extraction_deployment),
                    "cross_chapter_reconcile": _prices(runtime.reconciliation_deployment),
                },
                manifest={},
            )
            session.add(version)
            await session.flush()
            run.version_id = version.id
            session.add(AnalysisBudgetLedger(
                run_id=run.id,
                max_calls=runtime.budget_policy.max_calls,
                max_input_tokens=runtime.budget_policy.max_input_tokens,
                max_output_tokens=runtime.budget_policy.max_output_tokens,
                max_cost_usd=runtime.budget_policy.max_cost_usd,
            ))
        else:
            version = await session.get(AnalysisVersion, run.version_id)
            if version is None:
                raise TimelineWorkerError("run references a missing candidate version")
        chapters = list((await session.scalars(select(Chapter).where(
            Chapter.novel_id == run.novel_id,
        ).order_by(Chapter.chapter_number, Chapter.id))).all())
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


async def _extract_and_persist(runtime, budget, run, version, build, chapter) -> None:
    stage_key = f"chapter_extract:{chapter.id}"
    async with runtime.sessions() as session:
        stage = await session.scalar(select(AnalysisChapterStage).where(
            AnalysisChapterStage.run_id == run.id,
            AnalysisChapterStage.stage_key == stage_key,
            AnalysisChapterStage.status == "completed",
        ))
        if stage is not None:
            return
        nodes = list((await session.scalars(select(ChunkHierarchyNode).where(
            ChunkHierarchyNode.build_id == build.build_id,
            ChunkHierarchyNode.novel_id == run.novel_id,
            ChunkHierarchyNode.chapter_id == chapter.id,
            ChunkHierarchyNode.level == "evidence",
        ).order_by(ChunkHierarchyNode.order_index, ChunkHierarchyNode.node_id))).all())
    if not nodes:
        raise DependencyPaused(f"chapter {chapter.id} has no Phase 07 evidence")
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    package = EvidencePackage.create(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        chapter_id=chapter.id,
        unit_id=f"chapter:{chapter.id}",
        source_snapshot_hash=build.source_snapshot_hash,
        hierarchy_build_id=build.build_id,
        hierarchy_checksum=build.manifest_checksum,
        units=[EvidenceUnit(
            node.node_id, node.source_start, node.source_end, node.content, node.content_hash,
        ) for node in nodes],
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
            output = TimelineExtraction.model_validate(cached.gateway_output, strict=True)
            validate_extraction(package, output)
            await runtime.gateway.persistence.record_cache_hit(
                run_id=run.id, stage_key=stage_key, cache_key=cache_key.digest,
                source_attempt_id=cached.source_attempt_id,
                artifact_checksum=cached.artifact_checksum,
            )
    if output is None:
        result = await runtime.gateway.generate(
            deployment=runtime.extraction_deployment,
            schema=TimelineExtraction,
            messages=[
                {"role": "system", "content": runtime.extraction_prompt},
                {"role": "user", "content": json.dumps({
                    "scope": {"owner_id": run.owner_id, "novel_id": run.novel_id,
                              "chapter_id": chapter.id, "unit_id": package.unit_id},
                    "lineage": {"source_snapshot_hash": package.source_snapshot_hash,
                                "hierarchy_build_id": package.hierarchy_build_id,
                                "hierarchy_checksum": package.hierarchy_checksum,
                                "evidence_package_hash": package.package_hash},
                    "evidence": [unit.__dict__ for unit in package.units],
                }, sort_keys=True)},
            ],
            budget=budget,
            run_id=run.id,
            stage_key=stage_key,
            cache_key=cache_key.digest,
            max_input_tokens=max(256, sum(len(unit.text) for unit in package.units) * 2),
            max_output_tokens=1800,
            business_validator=lambda candidate: validate_extraction(package, candidate),
        )
        output = result.output
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    await _persist_chapter(runtime.sessions, run, version, chapter, stage_key, output)


async def _persist_chapter(sessions, run, version, chapter, stage_key, extraction) -> None:
    artifact = extraction.model_dump_json(exclude_none=False)
    checksum = hashlib.sha256(artifact.encode()).hexdigest()
    async with sessions.begin() as session:
        existing = await session.scalar(select(AnalysisChapterStage).where(
            AnalysisChapterStage.run_id == run.id,
            AnalysisChapterStage.stage_key == stage_key,
        ).with_for_update())
        if existing is not None and existing.status == "completed":
            return
        for candidate in extraction.events:
            logical_id = f"{chapter.id}:{candidate.candidate_id}"
            event = MachineTimelineEvent(
                version_id=version.id,
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                logical_event_id=logical_id,
                title=candidate.title,
                description=candidate.description,
                event_type=candidate.event_type,
                time_precision=candidate.story_time.precision,
                time_expression=candidate.story_time.expression,
                exact_time=candidate.story_time.exact_time,
                relative_anchor_event_id=candidate.story_time.anchor_event_id,
                relative_relation=candidate.story_time.relation,
                fuzzy_start=candidate.story_time.fuzzy_start,
                fuzzy_end=candidate.story_time.fuzzy_end,
                narrative_chapter_number=chapter.chapter_number,
                narrative_index=candidate.narrative_index,
                story_rank=None,
                story_constraints=[],
                confidence=candidate.confidence,
                prompt_hash=version.prompt_hash,
                schema_hash=version.schema_hash,
                model_lineage={"stage": "chapter_extract", "deployment": version.model_lineage["chapter_extract"]},
                publication_status="provisional",
            )
            session.add(event)
            await session.flush()
            session.add_all([
                TimelineParticipant(event_id=event.id, entity_id=item.entity_id, mention=item.mention)
                for item in candidate.participants
            ])
            session.add_all([
                TimelineEvidenceRef(
                    event_id=event.id,
                    chapter_id=ref.chapter_id,
                    evidence_id=ref.evidence_id,
                    source_start=ref.source_start,
                    source_end=ref.source_end,
                    content_hash=ref.content_hash,
                ) for ref in candidate.evidence
            ])
        checkpoint = {"gateway_output": json.loads(artifact), "artifact_checksum": checksum}
        if existing is None:
            session.add(AnalysisChapterStage(
                run_id=run.id, chapter_id=chapter.id, stage_key=stage_key,
                status="completed", artifact_checksum=checksum, checkpoint=checkpoint,
            ))
        else:
            existing.status = "completed"
            existing.artifact_checksum = checksum
            existing.checkpoint = checkpoint


async def _load_persisted_candidates(session: AsyncSession, version_id: int) -> list[EventCandidate]:
    events = list((await session.scalars(select(MachineTimelineEvent).where(
        MachineTimelineEvent.version_id == version_id,
    ).order_by(MachineTimelineEvent.narrative_chapter_number, MachineTimelineEvent.narrative_index))).all())
    result: list[EventCandidate] = []
    for event in events:
        participants = list((await session.scalars(select(TimelineParticipant).where(
            TimelineParticipant.event_id == event.id,
        ))).all())
        evidence = list((await session.scalars(select(TimelineEvidenceRef).where(
            TimelineEvidenceRef.event_id == event.id,
        ))).all())
        result.append(EventCandidate(
            candidate_id=event.logical_event_id,
            title=event.title,
            description=event.description,
            event_type=event.event_type,
            narrative_chapter_number=event.narrative_chapter_number,
            narrative_index=event.narrative_index,
            participants=[Participant(mention=row.mention, entity_id=row.entity_id) for row in participants],
            story_time=StoryTime(
                precision=event.time_precision,
                expression=event.time_expression,
                exact_time=event.exact_time,
                anchor_event_id=event.relative_anchor_event_id,
                relation=event.relative_relation,
                fuzzy_start=event.fuzzy_start,
                fuzzy_end=event.fuzzy_end,
            ),
            evidence=[EvidenceRef(
                chapter_id=row.chapter_id, evidence_id=row.evidence_id,
                source_start=row.source_start, source_end=row.source_end,
                content_hash=row.content_hash,
            ) for row in evidence],
            confidence=event.confidence,
        ))
    return result


async def _reconcile_and_persist(runtime, budget, run, version) -> None:
    stage_key = "cross_chapter_reconcile:book"
    async with runtime.sessions() as session:
        completed = await session.scalar(select(AnalysisChapterStage.id).where(
            AnalysisChapterStage.run_id == run.id,
            AnalysisChapterStage.stage_key == stage_key,
            AnalysisChapterStage.status == "completed",
        ))
        if completed is not None:
            return
        candidates = await _load_persisted_candidates(session, version.id)
    payload = [{
        "candidate_id": event.candidate_id,
        "title": event.title,
        "description": event.description,
        "narrative_chapter_number": event.narrative_chapter_number,
        "narrative_index": event.narrative_index,
        "participants": [item.model_dump() for item in event.participants],
        "evidence_ids": [item.evidence_id for item in event.evidence],
    } for event in candidates]
    reconciliation_prompt_hash, reconciliation_schema_hash = reconciliation_contract_hashes()
    cache_key = hashlib.sha256(json.dumps({
        "stage": "cross_chapter_reconcile",
        "source_snapshot_hash": version.source_snapshot_hash,
        "hierarchy_build_id": version.hierarchy_build_id,
        "hierarchy_checksum": version.hierarchy_checksum,
        "version_prompt_hash": version.prompt_hash,
        "version_schema_hash": version.schema_hash,
        "prompt_hash": reconciliation_prompt_hash,
        "schema_hash": reconciliation_schema_hash,
        "events": payload,
        "model": runtime.reconciliation_deployment.lineage,
        "decoding_hash": version.decoding_hash,
        "config_hash": version.config_hash,
    }, sort_keys=True, default=str).encode()).hexdigest()
    gateway_output = None
    if runtime.gateway.persistence is not None:
        cached = await load_persistent_exact_cache(runtime.sessions, cache_key)
        if cached is not None:
            gateway_output = ReconciliationOutputModel.model_validate(cached.gateway_output, strict=True)
            await runtime.gateway.persistence.record_cache_hit(
                run_id=run.id, stage_key=stage_key, cache_key=cache_key,
                source_attempt_id=cached.source_attempt_id,
                artifact_checksum=cached.artifact_checksum,
            )
    if gateway_output is None:
        gateway_result = await runtime.gateway.generate(
            deployment=runtime.reconciliation_deployment,
            schema=ReconciliationOutputModel,
            messages=[
                {"role": "system", "content": RECONCILIATION_PROMPT},
                {"role": "user", "content": json.dumps(payload, sort_keys=True)},
            ],
            budget=budget,
            run_id=run.id,
            stage_key=stage_key,
            cache_key=cache_key,
            max_input_tokens=max(512, sum(len(event.description) for event in candidates) * 2),
            max_output_tokens=4000,
        )
        gateway_output = gateway_result.output
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    reconciled = TimelineReconciler._materialize(candidates, gateway_output.as_input())
    artifact = json.dumps({
        "events": [item.__dict__ for item in reconciled.events],
        "edges": [item.__dict__ for item in reconciled.edges],
        "conflicts": reconciled.conflicts,
    }, sort_keys=True, default=list)
    checksum = hashlib.sha256(artifact.encode()).hexdigest()
    async with runtime.sessions.begin() as session:
        rows = list((await session.scalars(select(MachineTimelineEvent).where(
            MachineTimelineEvent.version_id == version.id,
        ))).all())
        by_logical = {row.logical_event_id: row for row in rows}
        for item in reconciled.events:
            for source_id in item.source_candidate_ids:
                if source_id in by_logical:
                    by_logical[source_id].story_rank = item.story_rank
                    by_logical[source_id].story_constraints = list(reconciled.conflicts)
        for edge in reconciled.edges:
            source = by_logical.get(edge.source_event_id)
            target = by_logical.get(edge.target_event_id)
            if source is not None and target is not None:
                session.add(TimelineCausalEdge(
                    version_id=version.id,
                    source_event_id=source.id,
                    target_event_id=target.id,
                    edge_type=edge.edge_type,
                    confidence=edge.confidence,
                    evidence_refs=list(edge.evidence_ids),
                ))
        session.add(AnalysisChapterStage(
            run_id=run.id, stage_key=stage_key, status="completed",
            artifact_checksum=checksum,
            checkpoint={
                "gateway_output": gateway_output.model_dump(mode="json"),
                "artifact": json.loads(artifact),
            },
        ))
        row = await session.get(AnalysisRun, run.id)
        row.progress = {**(row.progress or {}), "stage": "reconciling"}


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
        pointer = await session.scalar(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == run.owner_id,
            TimelineActivePointer.novel_id == run.novel_id,
        ))
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


async def _finish_run(sessions, run_id: int, status: str, reason: str | None) -> None:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is None:
            return
        run.status = status
        run.status_reason = reason
        run.lease_id = None
        run.lease_expires_at = None
        run.heartbeat_at = datetime.now(UTC)
        if status == "completed":
            run.progress = {**(run.progress or {}), "stage": "completed"}
