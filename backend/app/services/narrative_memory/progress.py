"""Durable dimension progress and DB-authoritative resume (Phase 28-04).

REQ-NM-03/04, D-04/D-10: progress events reuse the existing Agent SSE/Job
transport as *notification only*; the DB checkpoint (build run + stage rows +
append-only checkpoint journal) is the only authority across reconnects.
Reconnect recovery rehydrates from PostgreSQL rows, never from browser memory,
and the removed ``/analyze/stream`` endpoint is not restored.

Nothing in this module writes an active pointer or performs a cutover (D-07).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildCheckpoint,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import BuilderFrozenModel
from app.services.narrative_memory.contracts import Hash64, Key, VersionLabel
from app.services.narrative_memory.recovery import terminal_state_for_status

PROGRESS_EVENT_TYPE = "narrative_memory.progress"
# Statuses that keep a stage runnable (recoverable) until a terminal write.
_RESUMABLE = {"pending", "running", "paused"}


class ProgressStage(BuilderFrozenModel):
    """One durable stage row projected for a progress snapshot."""

    stage_key: Key
    stage_kind: VersionLabel
    status: VersionLabel
    terminal_state: VersionLabel | None = None
    reason_code: VersionLabel | None = None
    attempt_count: int = 0


class ProgressNotification(BuilderFrozenModel):
    """Notification-only event for the existing Agent SSE/Job transport.

    ``as_sse`` frames the payload as an OpenAI-style ``data: {json}`` chunk,
    the same envelope the gateway streaming endpoint already serves. Callers
    push this through that transport; it never carries authoritative state.
    """

    event_type: VersionLabel
    payload: dict[str, Any]

    def as_sse(self) -> str:
        chunk = {
            "event": self.event_type,
            "data": self.payload,
        }
        return (
            "data: "
            + json.dumps(
                chunk,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n\n"
        )


class DurableProgressSnapshot(BuilderFrozenModel):
    """DB-authoritative progress + resume view (recomputed on reconnect)."""

    run_id: int | None = None
    run_status: VersionLabel | None = None
    run_reason: VersionLabel | None = None
    resume_count: int = 0
    source_snapshot_hash: Hash64 | None = None
    progress: float
    resumable: bool
    authoritative: bool = True
    stage_counts: dict[str, int] = Field(default_factory=dict)
    checkpoint_counts: dict[str, int] = Field(default_factory=dict)
    stages: tuple[ProgressStage, ...] = ()
    dimensions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    notification: dict[str, Any] = Field(default_factory=dict)


def build_progress_notification(
    snapshot: DurableProgressSnapshot,
) -> ProgressNotification:
    """Build a notification-only event for the Agent SSE/Job transport.

    The payload is deliberately a digest: full stage/checkpoint rows stay in
    the DB. A client that reconnects must reload ``load_durable_progress``
    rather than trusting this event (D-10).
    """
    return ProgressNotification(
        event_type=PROGRESS_EVENT_TYPE,
        payload={
            "run_id": snapshot.run_id,
            "status": snapshot.run_status,
            "progress": snapshot.progress,
            "resumable": snapshot.resumable,
            "resume_count": snapshot.resume_count,
            "cutoff": max(
                (
                    int(v.get("cutoff") or 0)
                    for v in snapshot.dimensions.values()
                    if isinstance(v, dict)
                ),
                default=None,
            ),
            "dimensions": {
                key: {
                    "status": value.get("status"),
                    "progress": value.get("progress"),
                    "blocked_reason": value.get("blocked_reason"),
                }
                for key, value in snapshot.dimensions.items()
                if isinstance(value, dict)
            },
            "authoritative": snapshot.authoritative,
        },
    )


def _chapter_progress(stages: list[NarrativeMemoryBuildStage], total: int) -> float:
    if not total:
        return 0.0
    completed = sum(
        1
        for s in stages
        if s.stage_kind == "chapter_state" and s.status == "completed"
    )
    return round(min(completed / total, 1.0), 4)


def _stage_progress(
    stages: list[NarrativeMemoryBuildStage], total: int
) -> tuple[float, bool, dict[str, int]]:
    if not stages:
        return 0.0, False, {}
    counts = dict(Counter(s.status for s in stages))
    completed = sum(1 for s in stages if s.status == "completed")
    progress = round(completed / len(stages), 4) if stages else 0.0
    resumable = any(
        terminal_state_for_status(s.status) is None and s.status in _RESUMABLE
        for s in stages
    )
    # Prefer chapter-centric progress so a blocked arc does not read as done.
    chapter_count = len(
        {s.stage_key for s in stages if s.stage_kind == "chapter_state"}
    )
    if chapter_count:
        progress = _chapter_progress(stages, total or chapter_count)
    return progress, resumable, counts


async def load_durable_progress(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    dimension_statuses: dict[str, dict[str, Any]] | None = None,
) -> DurableProgressSnapshot:
    """Rehydrate the authoritative progress snapshot from PostgreSQL rows.

    This is the single reconnect/reload authority. It reads only durable rows
    (run, stages, checkpoint journal) and never any browser-held state.
    """
    run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == owner_id,
            NarrativeMemoryBuildRun.novel_id == novel_id,
            NarrativeMemoryBuildRun.version_id == version_id,
        )
    )
    stages: list[NarrativeMemoryBuildStage] = []
    checkpoint_counts: dict[str, int] = {}
    if run is not None:
        stages = list(
            (
                await session.scalars(
                    select(NarrativeMemoryBuildStage)
                    .where(NarrativeMemoryBuildStage.run_id == run.id)
                    .order_by(NarrativeMemoryBuildStage.id.asc())
                )
            ).all()
        )
        checkpoint_rows = (
            await session.scalars(
                select(NarrativeMemoryBuildCheckpoint).where(
                    NarrativeMemoryBuildCheckpoint.run_id == run.id
                )
            )
        ).all()
        checkpoint_counts = dict(
            Counter(
                checkpoint.terminal_state or "unknown"
                for checkpoint in checkpoint_rows
            )
        )

    chapter_numbers = list((run.progress or {}).get("chapter_numbers") or []) if run else []
    progress, resumable, stage_counts = _stage_progress(stages, len(chapter_numbers))

    stored_dimensions = dict((run.progress or {}).get("dimension_statuses") or {}) if run else {}
    dimensions = dimension_statuses if dimension_statuses is not None else stored_dimensions

    snapshot = DurableProgressSnapshot(
        run_id=int(run.id) if run else None,
        run_status=run.status if run else None,
        run_reason=run.status_reason if run else None,
        resume_count=int(run.resume_count or 0) if run else 0,
        source_snapshot_hash=run.source_snapshot_hash if run else None,
        progress=progress,
        resumable=resumable,
        authoritative=True,
        stage_counts=stage_counts,
        checkpoint_counts=checkpoint_counts,
        stages=tuple(
            ProgressStage(
                stage_key=s.stage_key,
                stage_kind=s.stage_kind,
                status=s.status,
                terminal_state=s.terminal_state,
                reason_code=s.reason_code,
                attempt_count=int(s.attempt_count or 0),
            )
            for s in stages
        ),
        dimensions=dimensions,
    )
    notification = build_progress_notification(snapshot)
    return snapshot.model_copy(
        update={"notification": notification.model_dump(mode="json")}
    )
