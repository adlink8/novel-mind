#!/usr/bin/env python3
"""Build a Phase 27 timeline-causality candidate without moving the pointer.

The normal timeline worker promotes its version when it completes.  Phase 27
needs a semantic facet first, so this operator clones the currently active
timeline version, asks the production structured-output gateway for bounded
causal proposals, applies the existing evidence gate, and leaves the active
pointer untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.analysis import (
    AnalysisBudgetLedger,
    AnalysisChapterStage,
    AnalysisRun,
    AnalysisVersion,
    ModelCallAttempt,
)
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.services.timeline.budget import BudgetGate
from app.services.timeline.promotion import snapshot_manifest
from app.services.timeline.reconcile import (
    RECONCILIATION_PROMPT,
    ReconciliationOutputModel,
)
from app.services.timeline.worker import production_runtime
from app.services.timeline.model_gateway import PersistentAttempt


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner-id", type=int, required=True)
    p.add_argument("--novel-id", type=int, required=True)
    p.add_argument("--parent-version-id", type=int, default=None)
    p.add_argument("--version-id", type=int, default=None, help="resume a candidate")
    p.add_argument("--window-size", type=int, default=80)
    p.add_argument("--overlap", type=int, default=12)
    p.add_argument("--max-windows", type=int, default=0, help="0 means all")
    return p


async def _clone_candidate(owner_id: int, novel_id: int, parent_id: int):
    async with async_session_factory.begin() as db:
        parent = await db.scalar(
            select(AnalysisVersion).where(
                AnalysisVersion.id == parent_id,
                AnalysisVersion.owner_id == owner_id,
                AnalysisVersion.novel_id == novel_id,
            )
        )
        if parent is None:
            raise ValueError("parent timeline version is outside the requested scope")

        version = AnalysisVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            parent_version_id=parent.id,
            version_key=f"phase27-{uuid.uuid4().hex}",
            status="candidate",
            source_snapshot_hash=parent.source_snapshot_hash,
            hierarchy_build_id=parent.hierarchy_build_id,
            hierarchy_checksum=parent.hierarchy_checksum,
            prompt_hash=parent.prompt_hash,
            schema_hash=parent.schema_hash,
            model_lineage=dict(parent.model_lineage or {}),
            decoding_hash=parent.decoding_hash,
            config_hash=parent.config_hash,
            price_snapshot=dict(parent.price_snapshot or {}),
            manifest={},
        )
        db.add(version)
        await db.flush()

        parents = list(
            (
                await db.scalars(
                    select(MachineTimelineEvent)
                    .where(MachineTimelineEvent.version_id == parent.id)
                    .order_by(
                        MachineTimelineEvent.narrative_chapter_number,
                        MachineTimelineEvent.narrative_index,
                        MachineTimelineEvent.id,
                    )
                )
            ).all()
        )
        event_map: dict[int, int] = {}
        for old in parents:
            new = MachineTimelineEvent(
                version_id=version.id,
                owner_id=old.owner_id,
                novel_id=old.novel_id,
                logical_event_id=old.logical_event_id,
                title=old.title,
                description=old.description,
                event_type=old.event_type,
                time_precision=old.time_precision,
                time_expression=old.time_expression,
                exact_time=old.exact_time,
                relative_anchor_event_id=old.relative_anchor_event_id,
                relative_relation=old.relative_relation,
                fuzzy_start=old.fuzzy_start,
                fuzzy_end=old.fuzzy_end,
                narrative_chapter_number=old.narrative_chapter_number,
                narrative_index=old.narrative_index,
                story_rank=old.story_rank,
                story_constraints=list(old.story_constraints or []),
                confidence=old.confidence,
                prompt_hash=old.prompt_hash,
                schema_hash=old.schema_hash,
                model_lineage=dict(old.model_lineage or {}),
                publication_status="provisional",
            )
            db.add(new)
            await db.flush()
            event_map[old.id] = new.id

        old_ids = list(event_map)
        participants = list(
            (
                await db.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id.in_(old_ids)
                    )
                )
            ).all()
        )
        for row in participants:
            db.add(
                TimelineParticipant(
                    event_id=event_map[row.event_id],
                    entity_id=row.entity_id,
                    mention=row.mention,
                )
            )
        evidence = list(
            (
                await db.scalars(
                    select(TimelineEvidenceRef).where(
                        TimelineEvidenceRef.event_id.in_(old_ids)
                    )
                )
            ).all()
        )
        for row in evidence:
            db.add(
                TimelineEvidenceRef(
                    event_id=event_map[row.event_id],
                    chapter_id=row.chapter_id,
                    evidence_id=row.evidence_id,
                    source_start=row.source_start,
                    source_end=row.source_end,
                    content_hash=row.content_hash,
                )
            )

        run = AnalysisRun(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version.id,
            # The database has a server default of ``active`` even when the
            # ORM value is None; use an explicit non-product key for facets.
            active_key="phase27",
            status="running",
            progress={"stage": "semantic_closure", "completed_windows": 0},
        )
        db.add(run)
        await db.flush()
        runtime = production_runtime()
        db.add(
            AnalysisBudgetLedger(
                run_id=run.id,
                max_calls=runtime.budget_policy.max_calls,
                max_input_tokens=runtime.budget_policy.max_input_tokens,
                max_output_tokens=runtime.budget_policy.max_output_tokens,
                max_cost_usd=runtime.budget_policy.max_cost_usd,
            )
        )
        return version.id, run.id


async def _load_candidate(version_id: int, owner_id: int, novel_id: int):
    async with async_session_factory() as db:
        version = await db.scalar(
            select(AnalysisVersion).where(
                AnalysisVersion.id == version_id,
                AnalysisVersion.owner_id == owner_id,
                AnalysisVersion.novel_id == novel_id,
            )
        )
        run = await db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.version_id == version_id,
                AnalysisRun.owner_id == owner_id,
                AnalysisRun.novel_id == novel_id,
            )
        )
        if version is None or run is None:
            raise ValueError("candidate version/run not found in requested scope")
        events = list(
            (
                await db.scalars(
                    select(MachineTimelineEvent)
                    .where(MachineTimelineEvent.version_id == version_id)
                    .order_by(
                        MachineTimelineEvent.narrative_chapter_number,
                        MachineTimelineEvent.narrative_index,
                        MachineTimelineEvent.id,
                    )
                )
            ).all()
        )
        evidence = list(
            (
                await db.scalars(
                    select(TimelineEvidenceRef).where(
                        TimelineEvidenceRef.event_id.in_([e.id for e in events])
                    )
                )
            ).all()
        )
        by_event: dict[int, set[str]] = {}
        for row in evidence:
            by_event.setdefault(row.event_id, set()).add(row.evidence_id)
        stages = set(
            (
                await db.scalars(
                    select(AnalysisChapterStage.stage_key).where(
                        AnalysisChapterStage.run_id == run.id,
                        AnalysisChapterStage.status == "completed",
                    )
                )
            ).all()
        )
        return version, run, events, by_event, stages


async def _persist_window(
    *,
    run_id: int,
    version_id: int,
    stage_key: str,
    accepted: list[dict],
    output: dict,
) -> None:
    artifact = {"accepted_edges": accepted, "provider_output": output}
    checksum = _sha(artifact)
    async with async_session_factory.begin() as db:
        existing = set(
            (
                await db.execute(
                    select(
                        TimelineCausalEdge.source_event_id,
                        TimelineCausalEdge.target_event_id,
                        TimelineCausalEdge.edge_type,
                    ).where(TimelineCausalEdge.version_id == version_id)
                )
            ).all()
        )
        for edge in accepted:
            edge_key = (
                edge["source_event_db_id"],
                edge["target_event_db_id"],
                edge["edge_type"],
            )
            if edge_key in existing:
                continue
            db.add(
                TimelineCausalEdge(
                    version_id=version_id,
                    source_event_id=edge["source_event_db_id"],
                    target_event_id=edge["target_event_db_id"],
                    edge_type=edge["edge_type"],
                    confidence=edge["confidence"],
                    evidence_refs=edge["evidence_refs"],
                )
            )
            existing.add(edge_key)
        db.add(
            AnalysisChapterStage(
                run_id=run_id,
                stage_key=stage_key,
                status="completed",
                artifact_checksum=checksum,
                checkpoint=artifact,
            )
        )


async def _finish(version_id: int, run_id: int, *, status: str, reason: str | None):
    async with async_session_factory.begin() as db:
        version = await db.get(AnalysisVersion, version_id)
        run = await db.get(AnalysisRun, run_id)
        if version is None or run is None:
            return
        if status == "completed":
            manifest, checksum = await snapshot_manifest(db, version_id)
            version.manifest = manifest
            version.manifest_checksum = checksum
            version.validated_at = datetime.now(UTC)
            version.status = "candidate"
        run.status = status
        run.status_reason = reason
        run.progress = {
            **(run.progress or {}),
            "stage": "semantic_closure_complete" if status == "completed" else status,
        }


async def _recover_reserved_attempts(run_id: int, runtime) -> int:
    """Release reservations left by an operator-stopped provider process."""
    async with async_session_factory() as db:
        rows = list(
            (
                await db.scalars(
                    select(ModelCallAttempt).where(
                        ModelCallAttempt.run_id == run_id,
                        ModelCallAttempt.status == "reserved",
                    )
                )
            ).all()
        )
    for row in rows:
        if row.reservation_id is not None:
            await runtime.gateway.persistence.mark_outcome_unknown(
                PersistentAttempt(row.id, row.reservation_id, row.attempt_number),
                latency_ms=0,
                error_code="operator_resume_after_process_stop",
            )
    if rows:
        async with async_session_factory.begin() as db:
            run = await db.get(AnalysisRun, run_id, with_for_update=True)
            if run is not None:
                run.status = "running"
                run.status_reason = "phase27_resume"
    return len(rows)


async def main() -> int:
    args = _parser().parse_args()
    if args.window_size < 20 or args.overlap < 0 or args.overlap >= args.window_size:
        raise SystemExit("window-size must be >=20 and overlap must be smaller")

    if args.version_id is not None:
        version_id = args.version_id
        version, run, events, evidence_by_event, completed = await _load_candidate(
            version_id, args.owner_id, args.novel_id
        )
    else:
        parent_id = args.parent_version_id
        if parent_id is None:
            async with async_session_factory() as db:
                pointer = await db.scalar(
                    select(TimelineActivePointer).where(
                        TimelineActivePointer.owner_id == args.owner_id,
                        TimelineActivePointer.novel_id == args.novel_id,
                    )
                )
                parent_id = pointer.version_id if pointer else None
        if parent_id is None:
            raise SystemExit("no active timeline version; pass --parent-version-id")
        version_id, run_id = await _clone_candidate(
            args.owner_id, args.novel_id, parent_id
        )
        version, run, events, evidence_by_event, completed = await _load_candidate(
            version_id, args.owner_id, args.novel_id
        )

    runtime = production_runtime()
    recovered_reserved = await _recover_reserved_attempts(run.id, runtime)
    budget = BudgetGate(runtime.budget_policy)
    step = args.window_size - args.overlap
    windows = [
        (start, min(start + args.window_size, len(events)))
        for start in range(0, len(events), step)
    ]
    if args.max_windows:
        windows = windows[: args.max_windows]
    provider_edges = accepted_edges = 0
    processed = 0

    try:
        for start, end in windows:
            stage_key = f"semantic_closure:window:{start}:{end}"
            if stage_key in completed:
                continue
            rows = []
            for event in events[start:end]:
                rows.append(
                    {
                        "event_id": event.logical_event_id,
                        "chapter": event.narrative_chapter_number,
                        "title": event.title,
                        "description": (event.description or "")[:1200],
                        "evidence_ids": sorted(evidence_by_event.get(event.id, set())),
                    }
                )
            prompt = (
                f"{RECONCILIATION_PROMPT} "
                "This is Phase 27 semantic closure. Emit only direct causal relations "
                "supported by evidence IDs present on BOTH endpoint events. "
                "Allowed edge types are causes, triggers, responds_to, blocks. "
                "Do not infer a relation from chapter adjacency, shared characters, "
                "or chronology alone. If evidence is insufficient, return no edge. "
                "Use only event IDs in this window."
            )
            payload = {"window_start": start, "window_end": end, "events": rows}
            result = await runtime.gateway.generate(
                deployment=runtime.reconciliation_deployment,
                schema=ReconciliationOutputModel,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                budget=budget,
                run_id=run.id,
                stage_key=stage_key,
                cache_key=_sha({"stage": stage_key, "payload": payload}),
                max_input_tokens=64_000,
                # A dense window can contain many evidence-backed edges; the
                # previous 4k cap caused truncated JSON/schema failures.
                max_output_tokens=8_192,
            )
            raw = result.output.model_dump(mode="json")
            provider_edges += len(result.output.causal_edges)
            by_logical = {e.logical_event_id: e for e in events[start:end]}
            accepted: list[dict] = []
            seen: set[tuple] = set()
            for edge in result.output.causal_edges:
                source = by_logical.get(edge.source_id)
                target = by_logical.get(edge.target_id)
                refs = set(edge.evidence_ids)
                if source is None or target is None or source.id == target.id:
                    continue
                if not (
                    refs & evidence_by_event.get(source.id, set())
                    and refs & evidence_by_event.get(target.id, set())
                ):
                    continue
                key = (source.id, target.id, edge.edge_type)
                if key in seen:
                    continue
                seen.add(key)
                accepted.append(
                    {
                        "source_event_id": edge.source_id,
                        "target_event_id": edge.target_id,
                        "source_event_db_id": source.id,
                        "target_event_db_id": target.id,
                        "edge_type": edge.edge_type,
                        "confidence": edge.confidence,
                        "evidence_refs": sorted(refs),
                    }
                )
            await _persist_window(
                run_id=run.id,
                version_id=version.id,
                stage_key=stage_key,
                accepted=accepted,
                output=raw,
            )
            accepted_edges += len(accepted)
            processed += 1
            print(
                json.dumps(
                    {
                        "window": [start, end],
                        "provider_edges": len(result.output.causal_edges),
                        "accepted_edges": len(accepted),
                    },
                    ensure_ascii=False,
                )
            )

        if args.max_windows:
            await _finish(version.id, run.id, status="running", reason="phase27_partial_candidate")
        else:
            await _finish(version.id, run.id, status="completed", reason="phase27_candidate")
    except Exception as exc:
        await _finish(version.id, run.id, status="paused_dependency", reason=type(exc).__name__)
        print(json.dumps({"status": "paused_dependency", "error": type(exc).__name__}))
        return 1

    async with async_session_factory() as db:
        pointer = await db.scalar(
            select(TimelineActivePointer).where(
                TimelineActivePointer.owner_id == args.owner_id,
                TimelineActivePointer.novel_id == args.novel_id,
            )
        )
        current_pointer_version = pointer.version_id if pointer else None
    print(
        json.dumps(
            {
                "status": "completed_candidate",
                "version_id": version.id,
                "run_id": run.id,
                "windows_processed": processed,
                "windows_total": len(windows),
                "provider_edges": provider_edges,
                "accepted_edges": accepted_edges,
                "active_pointer_version_id": current_pointer_version,
                "pointer_unchanged": current_pointer_version == version.parent_version_id,
                "manifest_checksum": version.manifest_checksum,
                "recovered_reserved_attempts": recovered_reserved,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
