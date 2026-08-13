"""Clue worker judge + persist seam — per-candidate judgment and persistence.

拆分说明（refactor split）：worker 的逐候选判断/缓存/持久化 seam 拆到本模块——
``_judge_and_persist``（预算预留 → 精确缓存 → deterministic → 网络判断 →
attempt/预算结算）、``_persist_decision``（gate 决策 → MachineClue /
ClueEvidenceRef 写入 → 渐进生命周期事件）、``_exact_cache_key``、
stage checkpoint 辅助与 ``_unit_to_evidence_dict``。只依赖
``_worker_primitives`` / ``_worker_titles`` 两个叶模块，不反向 import worker。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.clue import (
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    MachineClue,
)
from app.schemas.clue import (
    ClueActorSource,
    ClueLifecycleState,
    ClueSemanticJudgment,
)
from app.services.clues.budget import BudgetExceeded, BudgetGate
from app.services.clues.candidates import ClueCandidateDraft
from app.services.clues.evidence import sha256_json
from app.services.clues.lifecycle import append_lifecycle_event

from ._worker_primitives import (
    ClueModelDeployment,
    ClueWorkerRuntime,
    DependencyPaused,
    compute_actual_cost_usd,
)
from ._worker_titles import resolve_machine_clue_title

logger = logging.getLogger(__name__)

__all__ = [
    "_exact_cache_key",
    "_judge_and_persist",
    "_mark_stage_completed",
    "_persist_decision",
    "_stage_completed",
    "_unit_to_evidence_dict",
]


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
        # Clue packages carry multi-unit excerpts + system prompt; provider prompt
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
                failed_input = result.audit.prompt_tokens or 0
                failed_output = result.audit.completion_tokens or 0
                failed_cost, failed_cost_reason = compute_actual_cost_usd(
                    input_tokens=failed_input,
                    output_tokens=failed_output,
                    input_price_per_million=(
                        runtime.deployment.input_price_per_million
                    ),
                    output_price_per_million=(
                        runtime.deployment.output_price_per_million
                    ),
                )
                result.audit.cost_usd = float(failed_cost)
                failed_usage: dict[str, Any] = {
                    "input_tokens": failed_input,
                    "output_tokens": failed_output,
                }
                if failed_cost_reason is not None:
                    failed_usage["cost_usd_reason"] = failed_cost_reason
                await runtime.call_repo.complete_attempt(
                    handle,
                    status="failed",
                    response_hash=None,
                    provider_request_id=None,
                    usage=failed_usage,
                    cost_usd=failed_cost,
                    latency_ms=latency_ms,
                    error_code="schema_or_judgment_failed",
                )
                budget.settle(
                    f"{stage_key}:primary",
                    actual_input_tokens=failed_input,
                    actual_output_tokens=failed_output,
                    actual_cost_usd=failed_cost,
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
            actual_input = int(result.audit.prompt_tokens or 100)
            actual_output = int(result.audit.completion_tokens or 50)
            actual_cost, cost_reason = compute_actual_cost_usd(
                input_tokens=actual_input,
                output_tokens=actual_output,
                input_price_per_million=runtime.deployment.input_price_per_million,
                output_price_per_million=runtime.deployment.output_price_per_million,
            )
            # Real settlement value flows to audit + attempt + ledger together.
            result.audit.cost_usd = float(actual_cost)
            usage = {
                "input_tokens": actual_input,
                "output_tokens": actual_output,
                "validated_output": validated,
            }
            if cost_reason is not None:
                usage["cost_usd_reason"] = cost_reason
            await runtime.call_repo.complete_attempt(
                handle,
                status="succeeded",
                response_hash=response_hash,
                provider_request_id=None,
                usage=usage,
                cost_usd=actual_cost,
                latency_ms=latency_ms,
                error_code=None,
            )
            budget.settle(
                f"{stage_key}:primary",
                actual_input_tokens=actual_input,
                actual_output_tokens=actual_output,
                actual_cost_usd=actual_cost,
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
        title, title_source = resolve_machine_clue_title(
            short_title=judgment.short_title,
            rationale=judgment.rationale,
            cue_text=cue_text or None,
            chapter=(
                cue_unit.narrative_chapter_number if cue_unit is not None else None
            ),
            candidate_id=draft.candidate_id,
        )
        snapshot = package.to_snapshot()
        # Keep raw cue excerpt for ops/debug; product title stays short hypothesis.
        if isinstance(snapshot, dict):
            snapshot = {**snapshot, "title_source": title_source}
            if cue_text:
                snapshot["cue_excerpt"] = cue_text[:500]
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
