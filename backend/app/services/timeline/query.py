"""Owner-scoped, visible-set-first timeline reads."""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineOverride,
    TimelineParticipant,
)
from app.schemas.timeline import (
    Participant,
    TimelineCounts,
    TimelineOrdering,
    TimelineVersionSource,
    TimelineVersionView,
    TimelineVisibleEdge,
    TimelineVisibleEvent,
)


async def resolve_version_id(
    session: AsyncSession, *, owner_id: int, novel_id: int, source: TimelineVersionSource
) -> tuple[int, str, dict] | None:
    if source == TimelineVersionSource.ACTIVE:
        pointer = await session.scalar(select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == owner_id,
            TimelineActivePointer.novel_id == novel_id,
        ))
        if pointer is None:
            return None
        version = await session.get(AnalysisVersion, pointer.version_id)
        return (pointer.version_id, version.status if version else "active", {})
    run = await session.scalar(select(AnalysisRun).where(
        AnalysisRun.owner_id == owner_id,
        AnalysisRun.novel_id == novel_id,
        AnalysisRun.active_key == "active",
        AnalysisRun.version_id.is_not(None),
    ))
    if run is None or run.version_id is None:
        return None
    return run.version_id, run.status, dict(run.progress or {})


async def _chapter_cutoff(session: AsyncSession, novel: Novel) -> int | None:
    progress = novel.reading_progress or {}
    chapter_id = progress.get("chapter_id")
    if chapter_id is not None:
        chapter = await session.scalar(select(Chapter).where(
            Chapter.id == int(chapter_id), Chapter.novel_id == novel.id,
        ))
        if chapter is not None:
            return chapter.chapter_number
    # D20: no usable progress means first chapter, never the whole book.
    return await session.scalar(select(Chapter.chapter_number).where(
        Chapter.novel_id == novel.id,
    ).order_by(Chapter.chapter_number).limit(1))


async def build_version_view(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    source: TimelineVersionSource,
    ordering: TimelineOrdering,
    person: str | None,
    include_causal: bool,
    request_full_book: bool,
) -> TimelineVersionView | None:
    resolved = await resolve_version_id(session, owner_id=owner_id, novel_id=novel.id, source=source)
    if resolved is None:
        return None
    version_id, status, progress = resolved
    persisted_full_book = bool((novel.reading_progress or {}).get("timeline_full_book", False))
    cutoff = None if request_full_book and persisted_full_book else await _chapter_cutoff(session, novel)

    event_query = select(MachineTimelineEvent).where(
        MachineTimelineEvent.owner_id == owner_id,
        MachineTimelineEvent.novel_id == novel.id,
        MachineTimelineEvent.version_id == version_id,
    )
    if cutoff is None and not (request_full_book and persisted_full_book):
        event_query = event_query.where(False)
    elif cutoff is not None:
        event_query = event_query.where(MachineTimelineEvent.narrative_chapter_number <= cutoff)
    visible_rows = list((await session.scalars(event_query)).all())
    visible_ids = {row.id for row in visible_rows}

    participants = list((await session.scalars(select(TimelineParticipant).where(
        TimelineParticipant.event_id.in_(visible_ids)
    ))).all()) if visible_ids else []
    participants_by_event: dict[int, list[TimelineParticipant]] = {}
    for row in participants:
        participants_by_event.setdefault(row.event_id, []).append(row)

    if person:
        matched_ids = {row.event_id for row in participants if row.mention.casefold() == person.casefold()}
        visible_rows = [row for row in visible_rows if row.id in matched_ids]
        visible_ids = {row.id for row in visible_rows}

    # Overrides are fetched only for already-visible logical IDs, preventing text leaks.
    logical_ids = {row.logical_event_id for row in visible_rows}
    overrides = list((await session.scalars(select(TimelineOverride).where(
        TimelineOverride.owner_id == owner_id,
        TimelineOverride.novel_id == novel.id,
        TimelineOverride.logical_event_id.in_(logical_ids),
        TimelineOverride.status == "active",
        TimelineOverride.needs_relink.is_(False),
    ).order_by(TimelineOverride.id))).all()) if logical_ids else []
    override_by_event: dict[str, dict[str, object]] = {}
    for row in overrides:
        override_by_event.setdefault(row.logical_event_id, {})[row.field_name] = row.value

    order_key = ((lambda row: (row.story_rank is None, row.story_rank, row.narrative_chapter_number, row.narrative_index))
                 if ordering == TimelineOrdering.STORY else
                 (lambda row: (row.narrative_chapter_number, row.narrative_index)))
    visible_rows.sort(key=order_key)
    events = []
    participant_mentions: list[str] = []
    for row in visible_rows:
        patch = override_by_event.get(row.logical_event_id, {})
        provenance = {field: "manual" for field in patch}
        event_participants = participants_by_event.get(row.id, [])
        participant_mentions.extend(item.mention for item in event_participants)
        events.append(TimelineVisibleEvent(
            id=row.id, logical_event_id=row.logical_event_id,
            title=patch.get("title", row.title), description=patch.get("description", row.description),
            event_type=patch.get("event_type", row.event_type),
            narrative_chapter_number=row.narrative_chapter_number,
            narrative_index=row.narrative_index, story_rank=row.story_rank,
            time_precision=row.time_precision,
            time_expression=patch.get("time_expression", row.time_expression),
            confidence=row.confidence,
            participants=[Participant(mention=item.mention, entity_id=item.entity_id) for item in event_participants],
            provenance=provenance,
        ))
    edges = []
    if include_causal and visible_ids:
        edge_rows = (await session.scalars(select(TimelineCausalEdge).where(
            TimelineCausalEdge.version_id == version_id,
            TimelineCausalEdge.source_event_id.in_(visible_ids),
            TimelineCausalEdge.target_event_id.in_(visible_ids),
        ))).all()
        edges = [TimelineVisibleEdge(source_event_id=e.source_event_id,
                                     target_event_id=e.target_event_id,
                                     edge_type=e.edge_type, confidence=e.confidence) for e in edge_rows]
    aggregates = dict(Counter(participant_mentions))
    return TimelineVersionView(
        source=source, version_id=version_id, status=status, progress=progress,
        events=events, causal_edges=edges,
        counts=TimelineCounts(events=len(events), participants=len(participant_mentions), causal_edges=len(edges)),
        aggregates=aggregates,
    )
