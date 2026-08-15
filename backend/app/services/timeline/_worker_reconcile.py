"""Timeline worker cross-chapter reconciliation stage.

Responsibilities of this leaf module (refactor split):
- ``_load_persisted_candidates`` rebuilds ``EventCandidate`` values from the
  persisted ``MachineTimelineEvent``/participant/evidence rows.
- ``_reconcile_and_persist`` runs the idempotent ``cross_chapter_reconcile:book``
  stage: exact-cache lookup, LLM reconcile (with a deterministic pass-through
  for >120-event books), then persists story_rank/story_constraints back onto
  the events plus ``TimelineCausalEdge`` rows and the stage checkpoint.

This module imports ``_raise_if_cancel_requested`` from ``_worker_prepare``
and depends on reconcile/extraction/model_gateway — it never imports the
worker facade, so no import cycle. Public names are re-exported from
``worker.py`` unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisChapterStage, AnalysisRun
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.schemas.timeline import (
    EventCandidate,
    EvidenceRef,
    Participant,
    StoryTime,
)
from app.services.timeline._worker_prepare import _raise_if_cancel_requested
from app.services.timeline.extraction import load_persistent_exact_cache
from app.services.timeline.model_gateway import DependencyPaused, ModelCallFailed
from app.services.timeline.reconcile import (
    RECONCILIATION_PROMPT,
    ReconciliationOutputModel,
    TimelineReconciler,
    reconciliation_contract_hashes,
)

logger = logging.getLogger(__name__)


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
    # Large full-book candidate sets (500+ chapters) can exceed structured-output
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
