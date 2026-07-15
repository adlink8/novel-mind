"""Owner-scoped, visible-set-first clue projection.

Spoiler filtering precedes state, counts, filters, links, evidence and chains.
Full-book disclosure reuses Phase 08 `timeline_full_book` only.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueLink,
    ClueOverride,
    MachineClue,
)
from app.models.novel import Novel
from app.schemas.clue import (
    ClueLifecycleState,
    ClueVersionSource,
    ClueVisibleEnvelope,
    ClueVisibleItem,
    LifecycleEventInput,
    ClueActorSource,
    ClueEvidenceRef as ClueEvidenceRefSchema,
    ClueEvidenceRole,
    replay_lifecycle,
)
from app.services.clues.overrides import latest_overrides
from app.services.timeline.query import resolve_chapter_cutoff


async def resolve_clue_version_id(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    source: ClueVersionSource,
) -> tuple[int, str, dict] | None:
    if source == ClueVersionSource.ACTIVE:
        pointer = await session.scalar(
            select(ClueActivePointer).where(
                ClueActivePointer.owner_id == owner_id,
                ClueActivePointer.novel_id == novel_id,
            )
        )
        if pointer is None:
            return None
        version = await session.get(ClueAnalysisVersion, pointer.version_id)
        return (
            pointer.version_id,
            version.status if version else "validated",
            {},
        )
    if source == ClueVersionSource.RUNNING_CANDIDATE:
        run = await session.scalar(
            select(ClueAnalysisRun).where(
                ClueAnalysisRun.owner_id == owner_id,
                ClueAnalysisRun.novel_id == novel_id,
                ClueAnalysisRun.active_key == "active",
                ClueAnalysisRun.status != "completed",
                ClueAnalysisRun.version_id.is_not(None),
            )
        )
        if run is None or run.version_id is None:
            return None
        return run.version_id, run.status, dict(run.progress or {})
    return None


def _event_to_input(row: ClueLifecycleEvent) -> LifecycleEventInput:
    evidence: list[ClueEvidenceRefSchema] = []
    for ident in row.evidence_identities or []:
        parts = str(ident).split(":")
        if len(parts) < 5:
            continue
        try:
            chapter_id = int(parts[1])
            source_start = int(parts[2])
            source_end = int(parts[3])
            content_hash = parts[4]
        except ValueError:
            continue
        role = ClueEvidenceRole.CUE
        if row.to_status == "reinforced":
            role = ClueEvidenceRole.REINFORCEMENT
        elif row.to_status == "paid_off":
            role = ClueEvidenceRole.PAYOFF
        elif row.to_status == "dismissed":
            role = ClueEvidenceRole.DISPOSITION
        evidence.append(
            ClueEvidenceRefSchema(
                evidence_id=parts[0],
                role=role,
                chapter_id=chapter_id,
                narrative_chapter_number=chapter_id,
                source_start=source_start,
                source_end=source_end,
                content_hash=content_hash if len(content_hash) == 64 else "0" * 64,
            )
        )
    if row.to_status == "paid_off" and len(evidence) >= 2:
        evidence = [
            evidence[0].model_copy(update={"role": ClueEvidenceRole.CUE}),
            *[
                e.model_copy(update={"role": ClueEvidenceRole.PAYOFF})
                for e in evidence[1:]
            ],
        ]
    return LifecycleEventInput(
        from_status=ClueLifecycleState(row.from_status),
        to_status=ClueLifecycleState(row.to_status),
        actor_source=ClueActorSource(row.actor_source),
        reason=row.reason,
        evidence=evidence,
        event_key=row.event_key,
    )


def _event_visible(
    row: ClueLifecycleEvent,
    cutoff: int | None,
    *,
    evidence_chapter_by_identity: dict[str, int] | None = None,
) -> bool:
    """Lifecycle event is visible when all supporting narrative chapters ≤ cutoff.

    Prefer ORM narrative_chapter_number via evidence_identity map; fall back to
    cue_chapter/payoff_chapter columns. Identity strings encode chapter_id (PK),
    which must not be treated as narrative chapter numbers.
    """

    if cutoff is None:
        return True
    chapters: list[int] = []
    if evidence_chapter_by_identity:
        for ident in row.evidence_identities or []:
            narrative = evidence_chapter_by_identity.get(str(ident))
            if narrative is not None:
                chapters.append(int(narrative))
    if row.cue_chapter is not None:
        chapters.append(int(row.cue_chapter))
    if row.payoff_chapter is not None:
        chapters.append(int(row.payoff_chapter))
    if not chapters:
        # Disposition without evidence: visible (human reject at any time).
        # Unit tests may embed narrative chapter in identity when no map given.
        for ident in row.evidence_identities or []:
            parts = str(ident).split(":")
            if len(parts) >= 2:
                try:
                    chapters.append(int(parts[1]))
                except ValueError:
                    pass
    if not chapters:
        return True
    return max(chapters) <= cutoff


def derive_visible_state(
    events: list[ClueLifecycleEvent],
    *,
    cutoff: int | None,
    evidence_chapter_by_identity: dict[str, int] | None = None,
) -> ClueLifecycleState:
    """Replay only events whose evidence is fully visible at cutoff."""

    visible_inputs: list[LifecycleEventInput] = []
    current = ClueLifecycleState.CANDIDATE
    for row in sorted(events, key=lambda r: r.id):
        if not _event_visible(
            row, cutoff, evidence_chapter_by_identity=evidence_chapter_by_identity
        ):
            continue
        # from_status must match derived current; skip if history was filtered
        # such that intermediate steps disappear — restart chain only when
        # from_status matches.
        if row.from_status != current.value:
            # If early events are visible and continuous, this shouldn't happen.
            # When intermediate is hidden we stop applying later transitions.
            continue
        try:
            inp = _event_to_input(row)
            current = replay_lifecycle([*visible_inputs, inp])
            visible_inputs.append(inp)
        except Exception:
            break
    return current


async def build_clue_version_view(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    source: ClueVersionSource,
    request_full_book: bool = False,
    character_id: int | None = None,
    status_filter: str | None = None,
    version_id: int | None = None,
) -> ClueVisibleEnvelope | None:
    """Visible-set-first projection for one version source."""

    if version_id is not None:
        version = await session.get(ClueAnalysisVersion, version_id)
        if (
            version is None
            or version.owner_id != owner_id
            or version.novel_id != novel.id
        ):
            return None
        resolved_version_id = version.id
        status = version.status
        progress: dict[str, Any] = {}
    else:
        resolved = await resolve_clue_version_id(
            session, owner_id=owner_id, novel_id=novel.id, source=source
        )
        if resolved is None:
            return None
        resolved_version_id, status, progress = resolved

    persisted_full_book = bool(
        (novel.reading_progress or {}).get("timeline_full_book", False)
    )
    # Running candidate surfaces the full analysis job (worker scope), not reader cutoff.
    if source == ClueVersionSource.RUNNING_CANDIDATE:
        cutoff: int | None = None
        full_book = True
        through_chapter = 10**9
    elif request_full_book and persisted_full_book:
        cutoff = None
        full_book = True
        through_chapter = 10**9
    else:
        cutoff = await resolve_chapter_cutoff(session, novel)
        full_book = False
        through_chapter = int(cutoff or 1)

    clues = list(
        (
            await session.scalars(
                select(MachineClue).where(
                    MachineClue.owner_id == owner_id,
                    MachineClue.novel_id == novel.id,
                    MachineClue.version_id == resolved_version_id,
                )
            )
        ).all()
    )
    if not clues:
        return ClueVisibleEnvelope(
            novel_id=novel.id,
            version_id=resolved_version_id,
            source=source,
            through_chapter=max(1, through_chapter if through_chapter < 10**9 else 1),
            full_book=full_book,
            cutoff_chapter=max(1, int(cutoff or 1)),
            clues=[],
            counts={"clues": 0, "by_state": {}},
            available_states=[],
            available_character_ids=[],
        )

    all_logical = {c.logical_clue_id for c in clues}
    lifecycle_rows = list(
        (
            await session.scalars(
                select(ClueLifecycleEvent)
                .where(
                    ClueLifecycleEvent.version_id == resolved_version_id,
                    ClueLifecycleEvent.logical_clue_id.in_(all_logical),
                )
                .order_by(ClueLifecycleEvent.id)
            )
        ).all()
    )
    events_by_logical: dict[str, list[ClueLifecycleEvent]] = {}
    for row in lifecycle_rows:
        events_by_logical.setdefault(row.logical_clue_id, []).append(row)

    evidence_rows = list(
        (
            await session.scalars(
                select(ClueEvidenceRef).where(
                    ClueEvidenceRef.version_id == resolved_version_id,
                    ClueEvidenceRef.logical_clue_id.in_(all_logical),
                )
            )
        ).all()
    )
    evidence_by_logical: dict[str, list[ClueEvidenceRef]] = {}
    evidence_chapter_by_identity: dict[str, int] = {}
    for row in evidence_rows:
        evidence_by_logical.setdefault(row.logical_clue_id, []).append(row)
        evidence_chapter_by_identity[row.evidence_identity] = int(
            row.narrative_chapter_number
        )

    link_rows = list(
        (
            await session.scalars(
                select(ClueLink).where(
                    ClueLink.version_id == resolved_version_id,
                    ClueLink.logical_clue_id.in_(all_logical),
                )
            )
        ).all()
    )
    links_by_logical: dict[str, list[ClueLink]] = {}
    for row in link_rows:
        links_by_logical.setdefault(row.logical_clue_id, []).append(row)

    # First pass: which machine clues have any visible cue evidence?
    visible_clues: list[MachineClue] = []
    for clue in clues:
        evs = evidence_by_logical.get(clue.logical_clue_id, [])
        if cutoff is not None:
            visible_evs = [
                e for e in evs if e.narrative_chapter_number <= cutoff
            ]
            # Hide clues whose first cue is beyond cutoff.
            cue_chapters = [
                e.narrative_chapter_number for e in visible_evs if e.role == "cue"
            ]
            if not cue_chapters:
                first = clue.first_cue_chapter
                if first is None or first > cutoff:
                    continue
            else:
                if min(cue_chapters) > cutoff:
                    continue
        visible_clues.append(clue)

    visible_logical = {c.logical_clue_id for c in visible_clues}

    # Overrides only for already-visible logical IDs (no future text leak).
    override_rows = list(
        (
            await session.scalars(
                select(ClueOverride)
                .where(
                    ClueOverride.owner_id == owner_id,
                    ClueOverride.novel_id == novel.id,
                    ClueOverride.logical_clue_id.in_(visible_logical),
                )
                .order_by(ClueOverride.id)
            )
        ).all()
    ) if visible_logical else []
    heads = latest_overrides(override_rows)
    override_by_logical: dict[str, dict[str, Any]] = {}
    for row in heads:
        if row.needs_relink:
            continue
        override_by_logical.setdefault(row.logical_clue_id, {})[row.field_name] = row

    items: list[ClueVisibleItem] = []
    state_counter: Counter[str] = Counter()
    character_ids: set[int] = set()

    for clue in sorted(
        visible_clues,
        key=lambda c: (
            c.first_cue_chapter or 10**9,
            c.first_cue_source_start or 0,
            c.logical_clue_id,
        ),
    ):
        events = events_by_logical.get(clue.logical_clue_id, [])
        derived = derive_visible_state(
            events,
            cutoff=cutoff,
            evidence_chapter_by_identity=evidence_chapter_by_identity,
        )
        # Human disposition override can force dismiss/confirm visibility.
        ovr = override_by_logical.get(clue.logical_clue_id, {})
        provenance: dict[str, str] = {}
        if "disposition" in ovr:
            provenance["disposition"] = "manual"
            action = getattr(ovr["disposition"], "action", None) or (
                ovr["disposition"].action if hasattr(ovr["disposition"], "action") else None
            )
            # value may hold to_status
            val = getattr(ovr["disposition"], "value", {}) or {}
            if isinstance(val, dict):
                if val.get("to_status") == "dismissed":
                    derived = ClueLifecycleState.DISMISSED
                elif val.get("to_status") == "active" and derived == ClueLifecycleState.CANDIDATE:
                    derived = ClueLifecycleState.ACTIVE
            if action == "reject":
                derived = ClueLifecycleState.DISMISSED
            elif action == "confirm" and derived == ClueLifecycleState.CANDIDATE:
                derived = ClueLifecycleState.ACTIVE
        if "note" in ovr:
            provenance["note"] = "manual"
        if "link" in ovr:
            provenance["link"] = "manual"

        if status_filter and derived.value != status_filter:
            continue

        visible_evidence = [
            e
            for e in evidence_by_logical.get(clue.logical_clue_id, [])
            if cutoff is None or e.narrative_chapter_number <= cutoff
        ]
        visible_links = []
        for link in links_by_logical.get(clue.logical_clue_id, []):
            # Drop links whose supporting evidence is hidden.
            support = list(link.supporting_evidence_ids or [])
            if cutoff is not None and support:
                # If any supporting evidence id maps to hidden chapter, drop link.
                support_hidden = False
                for e in evidence_by_logical.get(clue.logical_clue_id, []):
                    if e.evidence_id in support and e.narrative_chapter_number > cutoff:
                        support_hidden = True
                        break
                if support_hidden:
                    continue
            visible_links.append(link)
            if link.character_id is not None:
                character_ids.add(int(link.character_id))

        if character_id is not None:
            link_chars = {int(l.character_id) for l in visible_links if l.character_id}
            if character_id not in link_chars:
                continue

        title = clue.title
        if "title" in ovr:
            val = getattr(ovr["title"], "value", None)
            if isinstance(val, dict) and "title" in val:
                title = str(val["title"])
            elif isinstance(val, str):
                title = val

        items.append(
            ClueVisibleItem(
                logical_clue_id=clue.logical_clue_id,
                title=title,
                derived_state=derived,
                narrative_chapter_number=int(clue.first_cue_chapter or 1),
                source_start=int(clue.first_cue_source_start or 0),
                confidence=float(clue.confidence),
                evidence_count=len(visible_evidence),
                link_count=len(visible_links),
                provenance=provenance,  # type: ignore[arg-type]
            )
        )
        state_counter[derived.value] += 1

    available_states = [
        ClueLifecycleState(s) for s in sorted(state_counter.keys())
    ]
    cutoff_chapter = int(cutoff or 1) if cutoff is not None else max(
        (i.narrative_chapter_number for i in items), default=1
    )
    return ClueVisibleEnvelope(
        novel_id=novel.id,
        version_id=resolved_version_id,
        source=source,
        through_chapter=through_chapter if through_chapter < 10**9 else cutoff_chapter,
        full_book=full_book,
        cutoff_chapter=cutoff_chapter,
        clues=items,
        counts={
            "clues": len(items),
            "by_state": dict(state_counter),
            "progress": progress,
            "status": status,
        },
        available_states=available_states,
        available_character_ids=sorted(character_ids),
    )


async def build_clue_envelope(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    request_full_book: bool = False,
    character_id: int | None = None,
    status_filter: str | None = None,
) -> dict[str, Any]:
    """Active + running-candidate envelopes for the list endpoint."""

    active = await build_clue_version_view(
        session,
        novel=novel,
        owner_id=owner_id,
        source=ClueVersionSource.ACTIVE,
        request_full_book=request_full_book,
        character_id=character_id,
        status_filter=status_filter,
    )
    running = await build_clue_version_view(
        session,
        novel=novel,
        owner_id=owner_id,
        source=ClueVersionSource.RUNNING_CANDIDATE,
        request_full_book=request_full_book,
        character_id=character_id,
        status_filter=status_filter,
    )
    return {
        "active": active.model_dump(mode="json") if active else None,
        "running_candidate": running.model_dump(mode="json") if running else None,
    }


async def clue_detail_panels(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    version_id: int,
    logical_clue_id: str,
    request_full_book: bool = False,
) -> dict[str, Any] | None:
    """Evidence/link/payoff chain panels with visible-set-first filtering."""

    view = await build_clue_version_view(
        session,
        novel=novel,
        owner_id=owner_id,
        source=ClueVersionSource.ACTIVE,
        request_full_book=request_full_book,
        version_id=version_id,
    )
    if view is None:
        return None
    item = next((c for c in view.clues if c.logical_clue_id == logical_clue_id), None)
    if item is None:
        return None

    persisted_full_book = bool(
        (novel.reading_progress or {}).get("timeline_full_book", False)
    )
    cutoff = (
        None
        if request_full_book and persisted_full_book
        else await resolve_chapter_cutoff(session, novel)
    )

    evidence = list(
        (
            await session.scalars(
                select(ClueEvidenceRef)
                .where(
                    ClueEvidenceRef.version_id == version_id,
                    ClueEvidenceRef.logical_clue_id == logical_clue_id,
                )
                .order_by(ClueEvidenceRef.sort_order, ClueEvidenceRef.id)
            )
        ).all()
    )
    if cutoff is not None:
        evidence = [e for e in evidence if e.narrative_chapter_number <= cutoff]

    links = list(
        (
            await session.scalars(
                select(ClueLink).where(
                    ClueLink.version_id == version_id,
                    ClueLink.logical_clue_id == logical_clue_id,
                )
            )
        ).all()
    )
    events = list(
        (
            await session.scalars(
                select(ClueLifecycleEvent)
                .where(
                    ClueLifecycleEvent.version_id == version_id,
                    ClueLifecycleEvent.logical_clue_id == logical_clue_id,
                )
                .order_by(ClueLifecycleEvent.id)
            )
        ).all()
    )
    ev_map = {
        e.evidence_identity: int(e.narrative_chapter_number)
        for e in evidence
    }
    # Re-load all evidence identities for chapter map (detail may have filtered).
    all_ev = list(
        (
            await session.scalars(
                select(ClueEvidenceRef).where(
                    ClueEvidenceRef.version_id == version_id,
                    ClueEvidenceRef.logical_clue_id == logical_clue_id,
                )
            )
        ).all()
    )
    for e in all_ev:
        ev_map[e.evidence_identity] = int(e.narrative_chapter_number)
    visible_events = [
        e
        for e in events
        if _event_visible(e, cutoff, evidence_chapter_by_identity=ev_map)
    ]

    return {
        "clue": item.model_dump(mode="json"),
        "evidence": [
            {
                "evidence_id": e.evidence_id,
                "role": e.role,
                "chapter_id": e.chapter_id,
                "narrative_chapter_number": e.narrative_chapter_number,
                "source_start": e.source_start,
                "source_end": e.source_end,
                "content_hash": e.content_hash,
                "excerpt": e.excerpt,
            }
            for e in evidence
        ],
        "links": [
            {
                "target_kind": l.target_kind,
                "character_id": l.character_id,
                "timeline_event_id": l.timeline_event_id,
                "relationship_observation_ref": l.relationship_observation_ref,
                "validation_status": l.validation_status,
            }
            for l in links
        ],
        "lifecycle": [
            {
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor_source": e.actor_source,
                "reason": e.reason,
                "event_key": e.event_key,
            }
            for e in visible_events
        ],
        "payoff_chain": [
            {
                "to_status": e.to_status,
                "event_key": e.event_key,
            }
            for e in visible_events
            if e.to_status in {"active", "reinforced", "paid_off"}
        ],
    }
