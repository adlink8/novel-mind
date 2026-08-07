"""Durable production orchestration for versioned timeline analysis."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.analysis import (
    AnalysisBudgetLedger,
    AnalysisChapterStage,
    AnalysisRun,
    AnalysisVersion,
)
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.character import Character
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.schemas.timeline import (
    EventCandidate,
    EvidenceRef,
    Participant,
    StoryTime,
    TimelineExtraction,
)
from app.services.timeline.budget import BudgetExceeded, BudgetGate, BudgetPolicy
from app.services.timeline.evidence import (
    EvidencePackage,
    EvidenceUnit,
    rebind_extraction_to_package,
    validate_extraction,
)
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
    extraction_prompt: str = (
        "Extract only evidence-backed timeline events from the supplied package."
    )
    budget_policy: BudgetPolicy = field(
        default_factory=lambda: BudgetPolicy(
            # 长篇（500+ 章）× 每章 1–2 次 Vertex 调用；预留必须覆盖 schema+证据包
            max_calls=5_000,
            max_input_tokens=100_000_000,
            max_output_tokens=20_000_000,
            max_cost_usd=Decimal("200"),
        )
    )


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


class _VertexTransport:
    """Google Cloud Vertex structured calls（与剧情分析同一条 GCP 链路）。"""

    async def complete(self, **kwargs: Any) -> dict[str, Any]:
        from app.services.vertex_gemini import acomplete

        model = kwargs.get("model") or ""
        messages = list(kwargs.get("messages") or [])
        timeout = float(kwargs.get("timeout") or 120)
        response_format = kwargs.get("response_format")
        max_tokens = int(
            kwargs.get("max_tokens") or kwargs.get("max_output_tokens") or 4096
        )

        schema: dict[str, Any] | None = None
        if response_format is not None and hasattr(
            response_format, "model_json_schema"
        ):
            schema = response_format.model_json_schema()

        response = await acomplete(
            messages,
            model=str(model),
            temperature=0.0,
            max_tokens=max_tokens,
            timeout=timeout,
            response_json_schema=schema,
        )
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        }
        content = response.choices[0].message.content or ""
        # 去掉可能的 markdown fence
        text = content.strip()
        if text.startswith("```"):
            text = (
                text.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
            )
            text = text.removesuffix("```").strip()
        return {
            "id": getattr(response, "id", None) or f"vertex-{model}",
            "content": text,
            "usage": usage,
        }


def production_runtime() -> TimelineWorkerRuntime:
    """Construct the production Phase 08 deployment pair.

    默认与「数据分析」/剧情分析对齐：Google Cloud Vertex Gemini。
    仅当 chat_provider 明确为 openai 且配置了 key 时回退 OpenAI。
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
        # Flash 级单价占位（仅预算账本用；GCP 账单以项目为准）
        deployment = ModelDeployment(
            "vertex_google",
            model_id,
            model_id,
            True,  # JSON schema via Vertex responseMimeType
            Decimal("0.10"),
            Decimal("0.40"),
        )
        return TimelineWorkerRuntime(
            sessions=async_session_factory,
            gateway=TimelineModelGateway(
                _VertexTransport(),
                persistence=PostgresCallRepository(async_session_factory),
            ),
            extraction_deployment=deployment,
            reconciliation_deployment=deployment,
            extraction_prompt=_load_prompt(),
        )

    import litellm

    extraction_model = "gpt-4o-mini-2024-07-18"
    reconciliation_model = "gpt-4o-2024-08-06"
    return TimelineWorkerRuntime(
        sessions=async_session_factory,
        gateway=TimelineModelGateway(
            _LiteLLMTransport(),
            persistence=PostgresCallRepository(async_session_factory),
        ),
        extraction_deployment=ModelDeployment(
            "openai",
            extraction_model,
            extraction_model,
            bool(
                litellm.supports_response_schema(
                    extraction_model, custom_llm_provider="openai"
                )
            ),
            Decimal("0.15"),
            Decimal("0.60"),
        ),
        reconciliation_deployment=ModelDeployment(
            "openai",
            reconciliation_model,
            reconciliation_model,
            bool(
                litellm.supports_response_schema(
                    reconciliation_model, custom_llm_provider="openai"
                )
            ),
            Decimal("2.50"),
            Decimal("10.00"),
        ),
        extraction_prompt=_load_prompt(),
    )


def _load_prompt() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "timeline_chapter_extract.v1.txt"
    )
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


async def _load_character_ids(
    sessions: async_sessionmaker[AsyncSession], novel_id: int
) -> set[int]:
    """该 novel 已注册的 characters 主键集合（用于 TimelineParticipant FK 校验）。"""
    async with sessions() as session:
        rows = await session.scalars(
            select(Character.id).where(Character.novel_id == novel_id)
        )
        return set(rows.all())


def _sanitize_participant_entity_ids(
    extraction: TimelineExtraction, known_ids: set[int]
) -> None:
    """FK fail-soft：把 LLM 臆造的 entity_id（novel 无对应 characters 行）置 None。

    对齐 relationships/candidates 的 fail-soft 模式：永不信任 LLM 给出的角色 id。
    只校验 id 存在性（本 novel scope）；mention 文本始终保留。
    """
    for event in extraction.events:
        for item in event.participants:
            if item.entity_id is not None and item.entity_id not in known_ids:
                item.entity_id = None


async def _load_character_registry(
    sessions: async_sessionmaker[AsyncSession], novel_id: int
) -> list[dict[str, Any]]:
    """该 novel 的 characters 注册表（id + name + aliases），随证据一起喂给抽取模型。"""
    async with sessions() as session:
        rows = (
            await session.execute(
                select(Character.id, Character.name, Character.aliases).where(
                    Character.novel_id == novel_id
                )
            )
        ).all()
    registry = []
    for row in rows:
        aliases = [
            alias.strip() for alias in (row.aliases or "").split(",") if alias.strip()
        ]
        registry.append({"id": row.id, "name": row.name, "aliases": aliases})
    return registry


async def _persist_chapter(
    sessions, run, version, chapter, stage_key, extraction
) -> None:
    known_character_ids = await _load_character_ids(sessions, run.novel_id)
    _sanitize_participant_entity_ids(extraction, known_character_ids)
    artifact = extraction.model_dump_json(exclude_none=False)
    checksum = hashlib.sha256(artifact.encode()).hexdigest()
    async with sessions.begin() as session:
        existing = await session.scalar(
            select(AnalysisChapterStage)
            .where(
                AnalysisChapterStage.run_id == run.id,
                AnalysisChapterStage.stage_key == stage_key,
            )
            .with_for_update()
        )
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
                model_lineage={
                    "stage": "chapter_extract",
                    "deployment": version.model_lineage["chapter_extract"],
                },
                publication_status="provisional",
            )
            session.add(event)
            await session.flush()
            session.add_all(
                [
                    TimelineParticipant(
                        event_id=event.id,
                        entity_id=item.entity_id,
                        mention=item.mention,
                    )
                    for item in candidate.participants
                ]
            )
            session.add_all(
                [
                    TimelineEvidenceRef(
                        event_id=event.id,
                        chapter_id=ref.chapter_id,
                        evidence_id=ref.evidence_id,
                        source_start=ref.source_start,
                        source_end=ref.source_end,
                        content_hash=ref.content_hash,
                    )
                    for ref in candidate.evidence
                ]
            )
        checkpoint = {
            "gateway_output": json.loads(artifact),
            "artifact_checksum": checksum,
        }
        if existing is None:
            session.add(
                AnalysisChapterStage(
                    run_id=run.id,
                    chapter_id=chapter.id,
                    stage_key=stage_key,
                    status="completed",
                    artifact_checksum=checksum,
                    checkpoint=checkpoint,
                )
            )
        else:
            existing.status = "completed"
            existing.artifact_checksum = checksum
            existing.checkpoint = checkpoint


async def _load_persisted_candidates(
    session: AsyncSession, version_id: int
) -> list[EventCandidate]:
    events = list(
        (
            await session.scalars(
                select(MachineTimelineEvent)
                .where(
                    MachineTimelineEvent.version_id == version_id,
                )
                .order_by(
                    MachineTimelineEvent.narrative_chapter_number,
                    MachineTimelineEvent.narrative_index,
                )
            )
        ).all()
    )
    result: list[EventCandidate] = []
    for event in events:
        participants = list(
            (
                await session.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id == event.id,
                    )
                )
            ).all()
        )
        evidence = list(
            (
                await session.scalars(
                    select(TimelineEvidenceRef).where(
                        TimelineEvidenceRef.event_id == event.id,
                    )
                )
            ).all()
        )
        result.append(
            EventCandidate(
                candidate_id=event.logical_event_id,
                title=event.title,
                description=event.description,
                event_type=event.event_type,
                narrative_chapter_number=event.narrative_chapter_number,
                narrative_index=event.narrative_index,
                participants=[
                    Participant(mention=row.mention, entity_id=row.entity_id)
                    for row in participants
                ],
                story_time=StoryTime(
                    precision=event.time_precision,
                    expression=event.time_expression,
                    exact_time=event.exact_time,
                    anchor_event_id=event.relative_anchor_event_id,
                    relation=event.relative_relation,
                    fuzzy_start=event.fuzzy_start,
                    fuzzy_end=event.fuzzy_end,
                ),
                evidence=[
                    EvidenceRef(
                        chapter_id=row.chapter_id,
                        evidence_id=row.evidence_id,
                        source_start=row.source_start,
                        source_end=row.source_end,
                        content_hash=row.content_hash,
                    )
                    for row in evidence
                ],
                confidence=event.confidence,
            )
        )
    return result


async def _reconcile_and_persist(runtime, budget, run, version) -> None:
    stage_key = "cross_chapter_reconcile:book"
    async with runtime.sessions() as session:
        completed = await session.scalar(
            select(AnalysisChapterStage.id).where(
                AnalysisChapterStage.run_id == run.id,
                AnalysisChapterStage.stage_key == stage_key,
                AnalysisChapterStage.status == "completed",
            )
        )
        if completed is not None:
            return
        candidates = await _load_persisted_candidates(session, version.id)
    payload = [
        {
            "candidate_id": event.candidate_id,
            "title": event.title,
            "description": event.description,
            "narrative_chapter_number": event.narrative_chapter_number,
            "narrative_index": event.narrative_index,
            "participants": [item.model_dump() for item in event.participants],
            "evidence_ids": [item.evidence_id for item in event.evidence],
        }
        for event in candidates
    ]
    reconciliation_prompt_hash, reconciliation_schema_hash = (
        reconciliation_contract_hashes()
    )
    cache_key = hashlib.sha256(
        json.dumps(
            {
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
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    gateway_output = None
    if runtime.gateway.persistence is not None:
        cached = await load_persistent_exact_cache(runtime.sessions, cache_key)
        if cached is not None:
            gateway_output = ReconciliationOutputModel.model_validate(
                cached.gateway_output, strict=True
            )
            await runtime.gateway.persistence.record_cache_hit(
                run_id=run.id,
                stage_key=stage_key,
                cache_key=cache_key,
                source_attempt_id=cached.source_attempt_id,
                artifact_checksum=cached.artifact_checksum,
            )
    # Large full-book candidate sets (500+ chapters) blow Vertex/OpenAI structured
    # reconcile payloads. Prefer LLM only for small/medium windows; otherwise
    # deterministic pass-through so promotion and downstream rel/clue can run.
    _MAX_LLM_RECONCILE_EVENTS = 120
    if gateway_output is None and len(candidates) > _MAX_LLM_RECONCILE_EVENTS:
        gateway_output = ReconciliationOutputModel(
            duplicate_groups=[],
            story_constraints=[],
            causal_edges=[],
        )
    if gateway_output is None:
        try:
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
                max_input_tokens=max(
                    32_768,
                    sum(len(event.description) for event in candidates) * 2 + 24_000,
                ),
                max_output_tokens=8_192,
            )
            gateway_output = gateway_result.output
        except (ModelCallFailed, DependencyPaused) as exc:
            # Fail soft: keep extracted events and continue to promote.
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "timeline reconcile LLM failed for run %s (%s events): %s; using pass-through",
                run.id,
                len(candidates),
                exc,
            )
            gateway_output = ReconciliationOutputModel(
                duplicate_groups=[],
                story_constraints=[],
                causal_edges=[],
            )
    await _raise_if_cancel_requested(runtime.sessions, run.id)
    reconciled = TimelineReconciler._materialize(candidates, gateway_output.as_input())
    artifact = json.dumps(
        {
            "events": [item.__dict__ for item in reconciled.events],
            "edges": [item.__dict__ for item in reconciled.edges],
            "conflicts": reconciled.conflicts,
        },
        sort_keys=True,
        default=list,
    )
    checksum = hashlib.sha256(artifact.encode()).hexdigest()
    async with runtime.sessions.begin() as session:
        rows = list(
            (
                await session.scalars(
                    select(MachineTimelineEvent).where(
                        MachineTimelineEvent.version_id == version.id,
                    )
                )
            ).all()
        )
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
                session.add(
                    TimelineCausalEdge(
                        version_id=version.id,
                        source_event_id=source.id,
                        target_event_id=target.id,
                        edge_type=edge.edge_type,
                        confidence=edge.confidence,
                        evidence_refs=list(edge.evidence_ids),
                    )
                )
        session.add(
            AnalysisChapterStage(
                run_id=run.id,
                stage_key=stage_key,
                status="completed",
                artifact_checksum=checksum,
                checkpoint={
                    "gateway_output": gateway_output.model_dump(mode="json"),
                    "artifact": json.loads(artifact),
                },
            )
        )
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
