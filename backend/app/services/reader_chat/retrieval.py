"""Visible-set-first evidence retrieval and Phase 09 read-only consumer contract.

Phase 10 never imports Phase 09 ORM models. Relationship observations arrive only
through :class:`RelationshipObservationReader`, bound to the completed Phase 09
public API (``load_filtered_relationship_graph``). Runtime outages are explicit
source statuses; missing contracts are execution failures, not null adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisVersion
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineEvidenceRef,
)
from app.schemas.relationship import RelationshipVersionSource

# Priority ranks used when packing the immutable context manifest (AI-SPEC §7).
SOURCE_PRIORITY: dict[str, int] = {
    "selection": 0,
    "hierarchy": 1,
    "knowledge": 2,
    "timeline": 3,
    "relationship_observation": 4,
}

DEFAULT_MAX_EVIDENCE = 24
DEFAULT_MAX_EXCERPT_CODE_POINTS = 700
DEFAULT_MAX_PER_SOURCE = 8


class SourceStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    ABSENT = "absent"


@dataclass(frozen=True)
class RelationshipObservationEvidence:
    evidence_id: str
    chapter_id: int
    source_start: int
    source_end: int
    content_hash: str
    chapter_number: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class RelationshipObservationItem:
    """Strict D-10 consumer DTO: versioned, evidence-bound, spoiler-filtered."""

    observation_id: int
    analysis_version_id: int
    owner_id: int
    novel_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    valid_from_chapter: int
    valid_to_chapter: int | None
    status: str
    evidence: tuple[RelationshipObservationEvidence, ...]
    confidence: float = 0.0

    def version_lineage(self) -> dict[str, Any]:
        return {
            "analysis_version_id": self.analysis_version_id,
            "observation_id": self.observation_id,
            "relation_type": self.relation_type,
            "valid_from_chapter": self.valid_from_chapter,
            "valid_to_chapter": self.valid_to_chapter,
        }


@runtime_checkable
class RelationshipObservationReader(Protocol):
    """Read-only Phase 09 consumer. Implementations must not write domain facts."""

    async def list_visible_observations(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        through_chapter: int | None,
        request_full_book: bool = False,
    ) -> list[RelationshipObservationItem]:
        """Return accepted, version/evidence/spoiler-scoped observations only."""
        ...


def revalidate_observation_item(
    item: RelationshipObservationItem,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff_chapter: int | None,
    full_book: bool,
) -> RelationshipObservationItem | None:
    """Drop observations that fail owner/version/status/spoiler revalidation."""

    if item.owner_id != owner_id or item.novel_id != novel_id:
        return None
    if item.analysis_version_id != version_id:
        return None
    if item.status != "accepted":
        return None
    if item.source_character_id == item.target_character_id:
        return None
    if not item.evidence:
        return None
    if not full_book and cutoff_chapter is not None:
        if item.valid_from_chapter > cutoff_chapter:
            return None
        if item.valid_to_chapter is not None and item.valid_to_chapter < 1:
            return None
    for ev in item.evidence:
        if ev.source_end <= ev.source_start or ev.source_start < 0:
            return None
        if len(ev.content_hash) != 64:
            return None
    return item


@dataclass
class RetrievedEvidence:
    evidence_key: str
    source_type: str
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    version_lineage: dict[str, Any] = field(default_factory=dict)
    priority: int = 99
    rank_key: tuple = ()


@dataclass
class RetrievalResult:
    items: list[RetrievedEvidence]
    omitted_counts: dict[str, int]
    source_status: dict[str, str]
    hierarchy_build_id: str
    hierarchy_checksum: str
    analysis_version_id: int | None


def bound_excerpt(text: str, max_code_points: int = DEFAULT_MAX_EXCERPT_CODE_POINTS) -> str:
    if code_point_len_local(text) <= max_code_points:
        return text
    return code_point_slice_local(text, 0, max_code_points - 1) + "…"


def code_point_len_local(text: str) -> int:
    return len(text)


def code_point_slice_local(text: str, start: int, end: int) -> str:
    return text[start:end]


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


async def resolve_active_hierarchy(
    session: AsyncSession, *, novel_id: int
) -> tuple[str, str] | None:
    pointer = await session.scalar(
        select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id)
    )
    if pointer is None:
        return None
    build = await session.scalar(
        select(ChunkBuild).where(
            ChunkBuild.build_id == pointer.build_id,
            ChunkBuild.novel_id == novel_id,
        )
    )
    if build is None:
        return None
    return build.build_id, build.manifest_checksum


async def resolve_active_analysis_version(
    session: AsyncSession, *, owner_id: int, novel_id: int
) -> int | None:
    pointer = await session.scalar(
        select(TimelineActivePointer).where(
            TimelineActivePointer.owner_id == owner_id,
            TimelineActivePointer.novel_id == novel_id,
        )
    )
    if pointer is None:
        return None
    version = await session.get(AnalysisVersion, pointer.version_id)
    if version is None:
        return None
    return int(pointer.version_id)


async def fetch_hierarchy_evidence(
    session: AsyncSession,
    *,
    novel_id: int,
    build_id: str,
    cutoff_chapter: int | None,
    full_book: bool,
    selection_chapter_id: int,
    selection_start: int,
    selection_end: int,
    max_items: int = DEFAULT_MAX_PER_SOURCE,
) -> tuple[list[RetrievedEvidence], int]:
    query = select(ChunkHierarchyNode).where(
        ChunkHierarchyNode.novel_id == novel_id,
        ChunkHierarchyNode.build_id == build_id,
        ChunkHierarchyNode.level == "evidence",
    )
    if not full_book and cutoff_chapter is not None:
        query = query.where(ChunkHierarchyNode.chapter_number <= cutoff_chapter)
    rows = list((await session.scalars(query)).all())

    overlapping: list[ChunkHierarchyNode] = []
    same_chapter: list[ChunkHierarchyNode] = []
    other: list[ChunkHierarchyNode] = []
    for row in rows:
        if row.chapter_id == selection_chapter_id and overlaps(
            selection_start, selection_end, row.source_start, row.source_end
        ):
            overlapping.append(row)
        elif row.chapter_id == selection_chapter_id:
            same_chapter.append(row)
        else:
            other.append(row)

    def _sort_key(node: ChunkHierarchyNode) -> tuple:
        return (node.chapter_number, node.source_start, node.source_end, node.node_id)

    ordered = (
        sorted(overlapping, key=_sort_key)
        + sorted(same_chapter, key=_sort_key)
        + sorted(other, key=_sort_key)
    )
    omitted = max(0, len(ordered) - max_items)
    selected = ordered[:max_items]
    items: list[RetrievedEvidence] = []
    for node in selected:
        is_overlap = node.chapter_id == selection_chapter_id and overlaps(
            selection_start, selection_end, node.source_start, node.source_end
        )
        items.append(
            RetrievedEvidence(
                evidence_key=f"hierarchy:{node.node_id}",
                source_type="hierarchy",
                source_id=str(node.node_id),
                chapter_id=int(node.chapter_id),
                chapter_number=int(node.chapter_number),
                source_start=int(node.source_start),
                source_end=int(node.source_end),
                content_hash=str(node.content_hash),
                excerpt=bound_excerpt(node.content or ""),
                version_lineage={
                    "hierarchy_build_id": build_id,
                    "node_id": node.node_id,
                    "level": node.level,
                },
                priority=SOURCE_PRIORITY["hierarchy"],
                rank_key=(0 if is_overlap else 1, node.chapter_number, node.source_start),
            )
        )
    return items, omitted


async def fetch_timeline_evidence(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    cutoff_chapter: int | None,
    full_book: bool,
    chapters_by_id: dict[int, Chapter],
    max_items: int = DEFAULT_MAX_PER_SOURCE,
) -> tuple[list[RetrievedEvidence], int, str]:
    if version_id is None:
        return [], 0, SourceStatus.ABSENT

    event_query = select(MachineTimelineEvent).where(
        MachineTimelineEvent.owner_id == owner_id,
        MachineTimelineEvent.novel_id == novel_id,
        MachineTimelineEvent.version_id == version_id,
        MachineTimelineEvent.publication_status.in_(("published", "provisional")),
    )
    if not full_book and cutoff_chapter is not None:
        event_query = event_query.where(
            MachineTimelineEvent.narrative_chapter_number <= cutoff_chapter
        )
    events = list((await session.scalars(event_query)).all())
    if not events:
        return [], 0, SourceStatus.OK

    event_ids = [e.id for e in events]
    evidence_rows = list(
        (
            await session.scalars(
                select(TimelineEvidenceRef).where(
                    TimelineEvidenceRef.event_id.in_(event_ids)
                )
            )
        ).all()
    )
    by_event: dict[int, list[TimelineEvidenceRef]] = {}
    for ref in evidence_rows:
        by_event.setdefault(ref.event_id, []).append(ref)

    chapter_number_by_id = {
        cid: ch.chapter_number for cid, ch in chapters_by_id.items()
    }
    # Reject evidence whose chapter is beyond cutoff even if event slipped through.
    candidates: list[RetrievedEvidence] = []
    for event in events:
        for ref in by_event.get(event.id, []):
            ch_num = chapter_number_by_id.get(ref.chapter_id)
            if ch_num is None:
                chapter = await session.get(Chapter, ref.chapter_id)
                if chapter is None or chapter.novel_id != novel_id:
                    continue
                ch_num = chapter.chapter_number
            if not full_book and cutoff_chapter is not None and ch_num > cutoff_chapter:
                continue
            excerpt_source = event.description or event.title
            candidates.append(
                RetrievedEvidence(
                    evidence_key=f"timeline:{event.logical_event_id}:{ref.evidence_id}",
                    source_type="timeline",
                    source_id=str(ref.evidence_id),
                    chapter_id=int(ref.chapter_id),
                    chapter_number=int(ch_num),
                    source_start=int(ref.source_start),
                    source_end=int(ref.source_end),
                    content_hash=str(ref.content_hash),
                    excerpt=bound_excerpt(excerpt_source),
                    version_lineage={
                        "analysis_version_id": version_id,
                        "logical_event_id": event.logical_event_id,
                        "event_id": event.id,
                    },
                    priority=SOURCE_PRIORITY["timeline"],
                    rank_key=(
                        event.narrative_chapter_number,
                        event.narrative_index,
                        ref.source_start,
                        ref.evidence_id,
                    ),
                )
            )

    candidates.sort(key=lambda item: item.rank_key)
    omitted = max(0, len(candidates) - max_items)
    return candidates[:max_items], omitted, SourceStatus.OK


async def fetch_relationship_evidence(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    version_id: int | None,
    cutoff_chapter: int | None,
    full_book: bool,
    reader: RelationshipObservationReader | None,
    chapters_by_number: dict[int, Chapter],
    max_items: int = DEFAULT_MAX_PER_SOURCE,
) -> tuple[list[RetrievedEvidence], int, str]:
    if reader is None:
        return [], 0, SourceStatus.ABSENT
    if version_id is None:
        return [], 0, SourceStatus.ABSENT

    try:
        raw_items = await reader.list_visible_observations(
            session,
            novel=novel,
            owner_id=owner_id,
            version_id=version_id,
            through_chapter=None if full_book else cutoff_chapter,
            request_full_book=full_book,
        )
    except Exception:
        return [], 0, SourceStatus.UNAVAILABLE

    validated: list[RelationshipObservationItem] = []
    for item in raw_items:
        ok = revalidate_observation_item(
            item,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff_chapter=cutoff_chapter,
            full_book=full_book,
        )
        if ok is not None:
            validated.append(ok)

    candidates: list[RetrievedEvidence] = []
    for item in validated:
        for ev in item.evidence:
            ch_num = ev.chapter_number
            if ch_num is None:
                chapter = await session.get(Chapter, ev.chapter_id)
                if chapter is None or chapter.novel_id != novel.id:
                    continue
                ch_num = chapter.chapter_number
            if not full_book and cutoff_chapter is not None and ch_num > cutoff_chapter:
                continue
            excerpt = ev.excerpt or (
                f"{item.relation_type}:{item.source_character_id}->{item.target_character_id}"
            )
            candidates.append(
                RetrievedEvidence(
                    evidence_key=f"relationship_observation:{item.observation_id}:{ev.evidence_id}",
                    source_type="relationship_observation",
                    source_id=str(item.observation_id),
                    chapter_id=int(ev.chapter_id),
                    chapter_number=int(ch_num),
                    source_start=int(ev.source_start),
                    source_end=int(ev.source_end),
                    content_hash=str(ev.content_hash),
                    excerpt=bound_excerpt(excerpt),
                    version_lineage=item.version_lineage(),
                    priority=SOURCE_PRIORITY["relationship_observation"],
                    rank_key=(
                        item.valid_from_chapter,
                        item.observation_id,
                        ev.evidence_id,
                    ),
                )
            )

    candidates.sort(key=lambda item: item.rank_key)
    omitted = max(0, len(candidates) - max_items)
    return candidates[:max_items], omitted, SourceStatus.OK


class Phase09RelationshipObservationReader:
    """Binds completed Phase 09 public reader — no ORM imports from relationships models."""

    def __init__(self, service: Any | None = None) -> None:
        if service is None:
            from app.services.relationships.query import (
                relationship_graph_query_service,
            )

            service = relationship_graph_query_service
        if not hasattr(service, "load_filtered_relationship_graph"):
            raise RuntimeError(
                "Phase 09 public contract load_filtered_relationship_graph is absent; "
                "stop on declared phase dependency (do not install a null adapter)."
            )
        self._service = service

    async def list_visible_observations(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        version_id: int,
        through_chapter: int | None,
        request_full_book: bool = False,
    ) -> list[RelationshipObservationItem]:
        envelope = await self._service.load_filtered_relationship_graph(
            session,
            novel=novel,
            owner_id=owner_id,
            source=RelationshipVersionSource.ACTIVE,
            version_id=version_id,
            through_chapter=through_chapter,
            request_full_book=request_full_book,
        )
        if envelope is None:
            return []

        evidence_by_obs: dict[int, list[RelationshipObservationEvidence]] = {}
        if hasattr(self._service, "list_accepted_observation_refs"):
            refs = await self._service.list_accepted_observation_refs(
                session,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=version_id,
                through_chapter=None if request_full_book else through_chapter,
            )
            for row in refs:
                oid = int(row["observation_id"])
                packed: list[RelationshipObservationEvidence] = []
                for ev in row.get("evidence") or []:
                    packed.append(
                        RelationshipObservationEvidence(
                            evidence_id=str(ev["evidence_id"]),
                            chapter_id=int(ev["chapter_id"]),
                            source_start=int(ev["source_start"]),
                            source_end=int(ev["source_end"]),
                            content_hash=str(ev["content_hash"]),
                            excerpt=ev.get("excerpt"),
                        )
                    )
                evidence_by_obs[oid] = packed

        items: list[RelationshipObservationItem] = []
        for edge in envelope.edges:
            evs = evidence_by_obs.get(edge.observation_id, [])
            if not evs and edge.evidence_count:
                # Graph preview only — synthesize a non-authoritative placeholder is forbidden.
                # Skip edges without revalidatable evidence lineage.
                continue
            if not evs:
                continue
            items.append(
                RelationshipObservationItem(
                    observation_id=int(edge.observation_id),
                    analysis_version_id=int(envelope.version_id),
                    owner_id=owner_id,
                    novel_id=int(novel.id),
                    source_character_id=int(edge.source_character_id),
                    target_character_id=int(edge.target_character_id),
                    relation_type=str(edge.relation_type),
                    valid_from_chapter=int(edge.valid_from_chapter),
                    valid_to_chapter=(
                        int(edge.valid_to_chapter)
                        if edge.valid_to_chapter is not None
                        else None
                    ),
                    status="accepted",
                    evidence=tuple(evs),
                    confidence=float(edge.confidence),
                )
            )
        return items


async def retrieve_visible_evidence(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    selection_chapter_id: int,
    selection_start: int,
    selection_end: int,
    cutoff_chapter: int | None,
    full_book: bool,
    relationship_reader: RelationshipObservationReader | None = None,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
) -> RetrievalResult:
    """Owner/novel/cutoff filtered retrieval; never trusts client evidence IDs."""

    source_status: dict[str, str] = {
        "hierarchy": SourceStatus.ABSENT,
        "timeline": SourceStatus.ABSENT,
        "knowledge": SourceStatus.ABSENT,
        "relationship_observation": SourceStatus.ABSENT,
    }
    omitted: dict[str, int] = {
        "hierarchy": 0,
        "timeline": 0,
        "knowledge": 0,
        "relationship_observation": 0,
    }

    hierarchy_meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    hierarchy_build_id = ""
    hierarchy_checksum = ""
    hierarchy_items: list[RetrievedEvidence] = []
    if hierarchy_meta is None:
        source_status["hierarchy"] = SourceStatus.ABSENT
    else:
        hierarchy_build_id, hierarchy_checksum = hierarchy_meta
        hierarchy_items, omitted["hierarchy"] = await fetch_hierarchy_evidence(
            session,
            novel_id=novel.id,
            build_id=hierarchy_build_id,
            cutoff_chapter=cutoff_chapter,
            full_book=full_book,
            selection_chapter_id=selection_chapter_id,
            selection_start=selection_start,
            selection_end=selection_end,
            max_items=max_per_source,
        )
        source_status["hierarchy"] = SourceStatus.OK

    version_id = await resolve_active_analysis_version(
        session, owner_id=owner_id, novel_id=novel.id
    )

    chapters = list(
        (
            await session.scalars(
                select(Chapter).where(Chapter.novel_id == novel.id)
            )
        ).all()
    )
    chapters_by_id = {c.id: c for c in chapters}
    chapters_by_number = {c.chapter_number: c for c in chapters}

    timeline_items, omitted["timeline"], source_status["timeline"] = (
        await fetch_timeline_evidence(
            session,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff_chapter=cutoff_chapter,
            full_book=full_book,
            chapters_by_id=chapters_by_id,
            max_items=max_per_source,
        )
    )

    # Knowledge units are optional; absence is explicit and never invented.
    source_status["knowledge"] = SourceStatus.ABSENT
    knowledge_items: list[RetrievedEvidence] = []

    rel_items, omitted["relationship_observation"], source_status[
        "relationship_observation"
    ] = await fetch_relationship_evidence(
        session,
        novel=novel,
        owner_id=owner_id,
        version_id=version_id,
        cutoff_chapter=cutoff_chapter,
        full_book=full_book,
        reader=relationship_reader,
        chapters_by_number=chapters_by_number,
        max_items=max_per_source,
    )

    packed = hierarchy_items + knowledge_items + timeline_items + rel_items
    packed.sort(
        key=lambda item: (
            item.priority,
            item.rank_key,
            item.evidence_key,
        )
    )
    if len(packed) > max_evidence:
        # Truncate between complete entries only.
        overflow = packed[max_evidence:]
        for item in overflow:
            omitted[item.source_type] = omitted.get(item.source_type, 0) + 1
        packed = packed[:max_evidence]

    return RetrievalResult(
        items=packed,
        omitted_counts=omitted,
        source_status=source_status,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        analysis_version_id=version_id,
    )
