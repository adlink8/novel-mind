"""Durable candidate clue analysis orchestration.

Claims leases, freezes lineage, reserves budget before each model call,
persists stage checkpoints, qualifies a complete version, and moves the
active pointer only via CAS. Failed candidates never move active.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueBudgetLedger,
    ClueEvidenceRef,
    MachineClue,
)
from app.models.novel import Chapter
from app.models.timeline import MachineTimelineEvent, TimelineActivePointer
from app.schemas.clue import (
    ClueActorSource,
    ClueLifecycleState,
    ClueSemanticJudgment,
)
from app.services.clues.budget import (
    BudgetExceeded,
    BudgetGate,
    BudgetPolicy,
    ClueCallRepository,
    UnknownPricing,
)
from app.services.clues.candidates import (
    CandidateRecallConfig,
    ClueCandidateDraft,
    ClueCandidateRecallService,
    HierarchyEvidenceNode,
    TimelineEventRef,
    clue_candidate_recall_service,
)
from app.services.clues.evidence import sha256_json, sha256_text
from app.services.clues.gates import ClueGateService, clue_gate_service, policy_hash
from app.services.clues.lifecycle import append_lifecycle_event
from app.services.clues.llm_judge import ClueLLMJudgeService, clue_llm_judge_service
from app.services.clues.versions import promote_version, snapshot_manifest

logger = logging.getLogger(__name__)

CONFIG_HASH = sha256_text("clue-worker.v1")
DECODING_HASH = sha256_json(
    {"temperature": 0.0, "stream": False, "provider_retries": 0, "max_tokens": 1200}
)


class ClueWorkerError(RuntimeError):
    pass


class ClueCancellationRequested(RuntimeError):
    pass


class DependencyPaused(RuntimeError):
    pass


@dataclass(frozen=True)
class ClueModelDeployment:
    provider: str
    model_id: str
    revision: str
    input_price_per_million: Decimal | None
    output_price_per_million: Decimal | None

    @property
    def lineage(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "revision": self.revision,
        }


@dataclass
class ClueWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    call_repo: ClueCallRepository
    deployment: ClueModelDeployment
    judge: ClueLLMJudgeService = field(default_factory=lambda: clue_llm_judge_service)
    recall: ClueCandidateRecallService = field(
        default_factory=lambda: clue_candidate_recall_service
    )
    gates: ClueGateService = field(default_factory=lambda: clue_gate_service)
    budget_policy: BudgetPolicy = field(
        default_factory=lambda: BudgetPolicy(
            max_calls=500,
            max_input_tokens=5_000_000,
            max_output_tokens=500_000,
            max_cost_usd=Decimal("25"),
        )
    )
    # Test hook: candidate_id → judgment dict (skips network; still reserves if configured).
    deterministic_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # When True, deterministic outputs count as cache hits (zero provider calls).
    deterministic_as_cache: bool = True


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


def _exact_cache_key(
    *,
    version: ClueAnalysisVersion,
    draft: ClueCandidateDraft,
    deployment: ClueModelDeployment,
) -> str:
    payload = {
        "stage": "clue_semantic_judge",
        "source_snapshot_hash": version.source_snapshot_hash,
        "hierarchy_build_id": version.hierarchy_build_id,
        "hierarchy_checksum": version.hierarchy_checksum,
        "timeline_version_id": version.timeline_version_id,
        "timeline_checksum": version.timeline_checksum,
        "candidate_id": draft.candidate_id,
        "package_hash": draft.package_hash,
        "prompt_hash": version.prompt_hash,
        "schema_hash": version.schema_hash,
        "model": deployment.lineage,
        "decoding_hash": version.decoding_hash,
        "config_hash": version.config_hash,
        "gate_config_hash": version.policy_hash,
    }
    return sha256_json(payload)


async def _stage_completed(
    sessions: async_sessionmaker[AsyncSession], run_id: int, stage_key: str
) -> bool:
    async with sessions() as session:
        run = await session.get(ClueAnalysisRun, run_id)
        if run is None:
            return False
        completed = set((run.checkpoint or {}).get("completed_stages") or [])
        return stage_key in completed


async def _mark_stage_completed(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
    stage_key: str,
    artifact: dict[str, Any] | None = None,
) -> None:
    async with sessions.begin() as session:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        if run is None:
            return
        checkpoint = dict(run.checkpoint or {})
        completed = list(checkpoint.get("completed_stages") or [])
        if stage_key not in completed:
            completed.append(stage_key)
        checkpoint["completed_stages"] = completed
        if artifact is not None:
            artifacts = dict(checkpoint.get("artifacts") or {})
            artifacts[stage_key] = artifact
            checkpoint["artifacts"] = artifacts
        run.checkpoint = checkpoint


async def _judge_and_persist(
    runtime: ClueWorkerRuntime,
    budget: BudgetGate,
    run: ClueAnalysisRun,
    version: ClueAnalysisVersion,
    draft: ClueCandidateDraft,
) -> None:
    stage_key = f"clue_judge:{draft.candidate_id}"
    if await _stage_completed(runtime.sessions, run.id, stage_key):
        return

    cache_key = _exact_cache_key(
        version=version, draft=draft, deployment=runtime.deployment
    )
    judgment: ClueSemanticJudgment | None = None
    used_cache = False

    # Exact cache from prior validated attempt.
    cached = await runtime.call_repo.load_exact_cache(cache_key)
    if cached is not None:
        try:
            judgment = ClueSemanticJudgment.model_validate(cached["gateway_output"])
            used_cache = True
            await runtime.call_repo.record_cache_hit(
                run_id=run.id,
                stage_key=stage_key,
                cache_key=cache_key,
                source_attempt_id=cached.get("source_attempt_id"),
                artifact_checksum=cached.get("artifact_checksum") or cache_key,
            )
        except Exception:
            judgment = None

    det = runtime.deterministic_outputs.get(draft.candidate_id)
    if judgment is None and det is not None:
        judgment = ClueSemanticJudgment.model_validate(det)
        if runtime.deterministic_as_cache:
            used_cache = True
            await runtime.call_repo.record_cache_hit(
                run_id=run.id,
                stage_key=stage_key,
                cache_key=cache_key,
                source_attempt_id=None,
                artifact_checksum=sha256_json(det),
            )

    if judgment is None:
        # Budget-safe reservation before network I/O.
        if not budget.network_calls_allowed:
            raise BudgetExceeded("budget is paused; no further calls are allowed")
        request_hash = sha256_json(
            {
                "package_hash": draft.package_hash,
                "prompt_hash": version.prompt_hash,
                "schema_hash": version.schema_hash,
            }
        )
        # Clue packages carry multi-unit excerpts + system prompt; Vertex prompt
        # tokens routinely exceed the old 12k headroom (observed ~16k on novel 91).
        # Under-reservation makes BudgetGate.settle raise after a successful call,
        # which the except path rewrites to outcome_unknown / paused_dependency.
        reserve_input_tokens = 48_000
        reserve_output_tokens = 2_000  # MAX_JUDGE_TOKENS is 1200
        handle = await runtime.call_repo.reserve_and_start(
            run_id=run.id,
            stage_key=stage_key,
            reservation_key=f"{stage_key}:primary",
            request_hash=request_hash,
            cache_key=cache_key,
            input_tokens=reserve_input_tokens,
            output_tokens=reserve_output_tokens,
            input_price_per_million=runtime.deployment.input_price_per_million,
            output_price_per_million=runtime.deployment.output_price_per_million,
        )
        # Mirror pure BudgetGate so in-process ceilings stay consistent.
        budget.reserve(
            f"{stage_key}:primary",
            input_tokens=reserve_input_tokens,
            output_tokens=reserve_output_tokens,
            input_price_per_million=runtime.deployment.input_price_per_million,
            output_price_per_million=runtime.deployment.output_price_per_million,
        )
        started = time.perf_counter()
        try:
            result = await runtime.judge.judge_package(draft.package, repair=False)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if not result.ok or result.structured is None:
                # One same-deployment schema repair.
                result = await runtime.judge.judge_package(draft.package, repair=True)
                latency_ms = int((time.perf_counter() - started) * 1000)
            if result.structured is None:
                await runtime.call_repo.complete_attempt(
                    handle,
                    status="failed",
                    response_hash=None,
                    provider_request_id=None,
                    usage={
                        "input_tokens": result.audit.prompt_tokens or 0,
                        "output_tokens": result.audit.completion_tokens or 0,
                    },
                    cost_usd=None,
                    latency_ms=latency_ms,
                    error_code="schema_or_judgment_failed",
                )
                budget.settle(
                    f"{stage_key}:primary",
                    actual_input_tokens=result.audit.prompt_tokens or 0,
                    actual_output_tokens=result.audit.completion_tokens or 0,
                    actual_cost_usd=Decimal(0),
                )
                await _mark_stage_completed(
                    runtime.sessions,
                    run.id,
                    stage_key,
                    {
                        "status": "judgment_failed",
                        "gate_failures": result.gate_failures,
                    },
                )
                return
            judgment = result.structured
            validated = judgment.model_dump(mode="json")
            response_hash = sha256_json(validated)
            usage = {
                "input_tokens": result.audit.prompt_tokens or 100,
                "output_tokens": result.audit.completion_tokens or 50,
                "validated_output": validated,
            }
            await runtime.call_repo.complete_attempt(
                handle,
                status="succeeded",
                response_hash=response_hash,
                provider_request_id=None,
                usage=usage,
                cost_usd=Decimal(str(result.audit.cost_usd or 0)),
                latency_ms=latency_ms,
                error_code=None,
            )
            budget.settle(
                f"{stage_key}:primary",
                actual_input_tokens=int(usage["input_tokens"]),
                actual_output_tokens=int(usage["output_tokens"]),
                actual_cost_usd=Decimal(str(result.audit.cost_usd or 0)),
            )
        except Exception as exc:
            await runtime.call_repo.complete_attempt(
                handle,
                status="outcome_unknown",
                response_hash=None,
                provider_request_id=None,
                usage={},
                cost_usd=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code=type(exc).__name__[:80],
            )
            raise DependencyPaused(
                f"provider outcome unknown: {type(exc).__name__}"
            ) from exc

    assert judgment is not None
    await _persist_decision(
        runtime, run, version, draft, judgment, used_cache=used_cache
    )
    await _mark_stage_completed(
        runtime.sessions,
        run.id,
        stage_key,
        {
            "candidate_id": draft.candidate_id,
            "package_hash": draft.package_hash,
            "classification": judgment.classification.value,
            "cache_hit": used_cache,
        },
    )


def _unit_to_evidence_dict(unit, role: str) -> dict[str, Any]:
    return {
        "evidence_id": unit.evidence_id,
        "role": role,
        "chapter_id": unit.chapter_id,
        "narrative_chapter_number": unit.narrative_chapter_number,
        "source_start": unit.source_start,
        "source_end": unit.source_end,
        "content_hash": unit.content_hash,
        "excerpt": (unit.text or "")[:500],
    }


def _clean_title_stem(text: str, *, max_len: int = 24) -> str:
    """Collapse whitespace and take a short stem for product titles."""
    cleaned = " ".join((text or "").replace("\r", "\n").split())
    if not cleaned:
        return ""
    # Prefer first sentence-like fragment.
    for sep in ("。", "！", "？", ".", "!", "?", "；", ";"):
        if sep in cleaned:
            cleaned = cleaned.split(sep, 1)[0].strip()
            break
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "…"


def build_machine_clue_title(
    *,
    rationale: str | None,
    cue_text: str | None,
    chapter: int | None,
    candidate_id: str,
    max_len: int = 32,
) -> str:
    """Short hypothesis title — never the raw long cue excerpt alone.

    Prefer the first cleaned line of the judgment rationale; otherwise
    ``伏笔·第N章`` + a short stem from cue text.
    """
    rationale_line = ""
    if rationale:
        first = (rationale.replace("\r", "\n").split("\n", 1)[0] or "").strip()
        rationale_line = _clean_title_stem(first, max_len=max_len)
    if rationale_line and len(rationale_line) >= 2:
        return rationale_line[:max_len]

    stem = _clean_title_stem(cue_text or "", max_len=16)
    if chapter is not None and int(chapter) > 0:
        prefix = f"伏笔·第{int(chapter)}章"
        if stem:
            title = f"{prefix}·{stem}"
        else:
            title = prefix
        return title[:max_len]

    if stem:
        return f"伏笔·{stem}"[:max_len]
    return (candidate_id or "伏笔候选")[:max_len]


async def _persist_decision(
    runtime: ClueWorkerRuntime,
    run: ClueAnalysisRun,
    version: ClueAnalysisVersion,
    draft: ClueCandidateDraft,
    judgment: ClueSemanticJudgment,
    *,
    used_cache: bool,
) -> None:
    """Gate + persist machine clue and optional lifecycle (no active pointer move)."""

    # Target state from classification.
    classification = judgment.classification.value
    if classification == "cue_only":
        to_status = ClueLifecycleState.ACTIVE
    elif classification == "reinforcement":
        to_status = ClueLifecycleState.REINFORCED
    elif classification == "payoff":
        to_status = ClueLifecycleState.PAID_OFF
    elif classification in {"unrelated", "ambiguous"}:
        to_status = ClueLifecycleState.DISMISSED
    else:
        to_status = ClueLifecycleState.DISMISSED

    package = draft.package
    owner_id = int(run.owner_id)
    novel_id = int(run.novel_id)
    version_id = int(version.id)
    hierarchy_build_id = str(version.hierarchy_build_id)

    decision = runtime.gates.evaluate_transition(
        package=package,
        judgment=judgment,
        from_status=ClueLifecycleState.CANDIDATE,
        to_status=(
            ClueLifecycleState.ACTIVE
            if to_status
            in {
                ClueLifecycleState.ACTIVE,
                ClueLifecycleState.REINFORCED,
                ClueLifecycleState.PAID_OFF,
            }
            else to_status
        ),
        owner_id=owner_id,
        novel_id=novel_id,
        hierarchy_build_id=hierarchy_build_id,
    )

    async with runtime.sessions.begin() as session:
        existing = await session.scalar(
            select(MachineClue).where(
                MachineClue.version_id == version_id,
                MachineClue.logical_clue_id == draft.candidate_id,
            )
        )
        if existing is not None:
            return

        cue_unit = package.cue_units[0] if package.cue_units else None
        cue_text = (cue_unit.text or "") if cue_unit is not None else ""
        title = build_machine_clue_title(
            rationale=judgment.rationale,
            cue_text=cue_text or None,
            chapter=(
                cue_unit.narrative_chapter_number if cue_unit is not None else None
            ),
            candidate_id=draft.candidate_id,
        )
        snapshot = package.to_snapshot()
        # Keep raw cue excerpt for ops/debug; product title stays short hypothesis.
        if isinstance(snapshot, dict) and cue_text:
            snapshot = {
                **snapshot,
                "cue_excerpt": cue_text[:500],
                "title_source": "rationale_or_chapter_stem",
            }
        machine = MachineClue(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            logical_clue_id=draft.candidate_id,
            title=title or draft.candidate_id,
            summary=(judgment.rationale or "")[:4000],
            package_hash=draft.package_hash,
            package_snapshot=snapshot,
            confidence=float(judgment.confidence),
            publication_status="provisional",
            first_cue_chapter=(
                cue_unit.narrative_chapter_number if cue_unit is not None else None
            ),
            first_cue_source_start=cue_unit.source_start
            if cue_unit is not None
            else None,
        )
        session.add(machine)
        await session.flush()

        for index, unit in enumerate(package.cue_units):
            session.add(
                ClueEvidenceRef(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    machine_clue_id=machine.id,
                    role="cue",
                    evidence_id=unit.evidence_id,
                    evidence_identity=(
                        f"{unit.evidence_id}:{unit.chapter_id}:"
                        f"{unit.source_start}:{unit.source_end}:{unit.content_hash}"
                    ),
                    chapter_id=unit.chapter_id,
                    narrative_chapter_number=unit.narrative_chapter_number,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                    content_hash=unit.content_hash,
                    excerpt=(unit.text or "")[:500],
                    sort_order=index,
                )
            )
        for index, unit in enumerate(package.later_units):
            session.add(
                ClueEvidenceRef(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    machine_clue_id=machine.id,
                    role="reinforcement",
                    evidence_id=unit.evidence_id,
                    evidence_identity=(
                        f"{unit.evidence_id}:{unit.chapter_id}:"
                        f"{unit.source_start}:{unit.source_end}:{unit.content_hash}"
                    ),
                    chapter_id=unit.chapter_id,
                    narrative_chapter_number=unit.narrative_chapter_number,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                    content_hash=unit.content_hash,
                    excerpt=(unit.text or "")[:500],
                    sort_order=index,
                )
            )
        await session.flush()

        if not decision.accepted:
            machine.publication_status = "provisional"
            return

        # Progressive lifecycle for accepted cue path.
        cue_evidence = [_unit_to_evidence_dict(u, "cue") for u in package.cue_units]
        if cue_evidence:
            try:
                await append_lifecycle_event(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    to_status=ClueLifecycleState.ACTIVE,
                    actor_source=ClueActorSource.MACHINE,
                    reason=f"gate:{decision.gate_status}",
                    evidence=cue_evidence,
                    event_key=f"machine-active:{draft.candidate_id}",
                    machine_clue_id=machine.id,
                    gate_audit={
                        "gate_status": decision.gate_status,
                        "reason_codes": decision.reason_codes,
                        "cache_hit": used_cache,
                    },
                )
                machine.publication_status = "published"
            except Exception as exc:
                logger.info("lifecycle active skipped: %s", exc)

        if classification == "reinforcement" and package.later_units:
            reinf = [
                _unit_to_evidence_dict(u, "reinforcement")
                for u in package.later_units[:1]
            ]
            try:
                await append_lifecycle_event(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    to_status=ClueLifecycleState.REINFORCED,
                    actor_source=ClueActorSource.MACHINE,
                    reason="gate:reinforcement",
                    evidence=reinf,
                    event_key=f"machine-reinforced:{draft.candidate_id}",
                    machine_clue_id=machine.id,
                    gate_audit={"classification": classification},
                )
            except Exception as exc:
                logger.info("lifecycle reinforced skipped: %s", exc)

        if classification == "payoff" and package.later_units and package.cue_units:
            reinf = [
                _unit_to_evidence_dict(u, "reinforcement")
                for u in package.later_units[:1]
            ]
            try:
                await append_lifecycle_event(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    to_status=ClueLifecycleState.REINFORCED,
                    actor_source=ClueActorSource.MACHINE,
                    reason="gate:reinforcement_for_payoff",
                    evidence=reinf,
                    event_key=f"machine-reinforced:{draft.candidate_id}",
                    machine_clue_id=machine.id,
                    gate_audit={"classification": classification},
                )
                pay_ev = [
                    _unit_to_evidence_dict(package.cue_units[0], "cue"),
                    _unit_to_evidence_dict(package.later_units[-1], "payoff"),
                ]
                payoff_decision = runtime.gates.evaluate_transition(
                    package=package,
                    judgment=judgment,
                    from_status=ClueLifecycleState.REINFORCED,
                    to_status=ClueLifecycleState.PAID_OFF,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    hierarchy_build_id=hierarchy_build_id,
                )
                if payoff_decision.accepted:
                    await append_lifecycle_event(
                        session,
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_id=version_id,
                        logical_clue_id=draft.candidate_id,
                        to_status=ClueLifecycleState.PAID_OFF,
                        actor_source=ClueActorSource.MACHINE,
                        reason="gate:paid_off",
                        evidence=pay_ev,
                        event_key=f"machine-paid_off:{draft.candidate_id}",
                        machine_clue_id=machine.id,
                        gate_audit={
                            "gate_status": payoff_decision.gate_status,
                            "reason_codes": payoff_decision.reason_codes,
                        },
                    )
            except Exception as exc:
                logger.info("lifecycle payoff chain skipped: %s", exc)

        if classification in {"unrelated", "ambiguous"}:
            try:
                await append_lifecycle_event(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                    logical_clue_id=draft.candidate_id,
                    to_status=ClueLifecycleState.DISMISSED,
                    actor_source=ClueActorSource.MACHINE,
                    reason=f"gate:dismiss:{classification}",
                    evidence=[],
                    event_key=f"machine-dismissed:{draft.candidate_id}",
                    machine_clue_id=machine.id,
                    gate_audit={"classification": classification},
                )
            except Exception as exc:
                logger.info("lifecycle dismiss skipped: %s", exc)


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
