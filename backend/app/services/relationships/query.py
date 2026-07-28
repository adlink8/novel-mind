"""
Owner/version/spoiler-scoped relationship graph read model.

PostgreSQL accepted observations are the sole fact source. Nodes, edges,
filters, counts and evidence previews all derive from one visible-set fold.
Legacy CharacterRelation is never read.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.character import Character
from app.models.novel import Chapter, Novel
from app.models.relationship import (
    CharacterIdentityOverride,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipOverride,
)
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineParticipant,
)
from app.schemas.relationship import (
    GraphDegradationMode,
    ProvenanceKind,
    RelationshipCounts,
    RelationshipDegradation,
    RelationshipEdgeKind,
    RelationshipEdgeType,
    RelationshipEvidenceRef,
    RelationshipEvidenceResponse,
    RelationshipGraphEdge,
    RelationshipGraphEdgeLabel,
    RelationshipGraphEnvelope,
    RelationshipGraphNode,
    RelationshipVersionSource,
)

# D-22 degradation thresholds (immutable product contract).
NORMAL_NODE_CAP = 200
NORMAL_EDGE_CAP = 600
HARD_NODE_CAP = 500
HARD_EDGE_CAP = 1500


@dataclass(frozen=True)
class ResolvedVersion:
    version_id: int
    source: RelationshipVersionSource
    status: str
    progress: dict[str, Any]


@dataclass
class _FoldedEdge:
    observation_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    transition: str
    confidence: float
    valid_from_chapter: int
    valid_to_chapter: int | None
    logical_key: str
    provenance: ProvenanceKind = ProvenanceKind.MACHINE
    evidence_preview: str | None = None
    evidence_count: int = 0
    edge_kind: RelationshipEdgeKind = RelationshipEdgeKind.ACCEPTED_OBSERVATION
    suggested_type: str | None = None


def logical_relationship_key(
    source_character_id: int,
    target_character_id: int,
    relation_type: str,
) -> str:
    """Stable directed key used by overrides and fold grouping."""
    return f"{source_character_id}:{target_character_id}:{relation_type}"


def _position_tuple(chapter: int, narrative_index: int = 0) -> tuple[int, int]:
    return (chapter, narrative_index)


def _covers_position(
    *,
    valid_from_chapter: int,
    valid_from_narrative_index: int,
    valid_to_chapter: int | None,
    valid_to_narrative_index: int | None,
    through_chapter: int,
    through_narrative_index: int = 0,
) -> bool:
    start = _position_tuple(valid_from_chapter, valid_from_narrative_index)
    pos = _position_tuple(through_chapter, through_narrative_index)
    if start > pos:
        return False
    if valid_to_chapter is None:
        return True
    end = _position_tuple(valid_to_chapter, valid_to_narrative_index or 0)
    return end >= pos


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class RelationshipGraphQueryService:
    """Server-side version proof, narrative fold, and spoiler-safe projection."""

    async def resolve_version(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
        version_id: int | None = None,
    ) -> ResolvedVersion | None:
        """Prove version access inside owner/novel; never merge sources."""

        if version_id is not None:
            version = await session.scalar(
                select(AnalysisVersion).where(
                    AnalysisVersion.id == version_id,
                    AnalysisVersion.owner_id == owner_id,
                    AnalysisVersion.novel_id == novel_id,
                )
            )
            if version is None:
                return None

            pointer = await session.scalar(
                select(TimelineActivePointer).where(
                    TimelineActivePointer.owner_id == owner_id,
                    TimelineActivePointer.novel_id == novel_id,
                    TimelineActivePointer.version_id == version_id,
                )
            )
            if pointer is not None and source in (
                RelationshipVersionSource.ACTIVE,
                RelationshipVersionSource.HISTORY,
            ):
                # Explicit id that is currently active is still a valid active view
                # when client asked for active/history of that id.
                if source == RelationshipVersionSource.ACTIVE or (
                    source == RelationshipVersionSource.HISTORY
                ):
                    # Prefer classifying as active when pointer matches and source=active.
                    if source == RelationshipVersionSource.ACTIVE:
                        return ResolvedVersion(
                            version_id=version_id,
                            source=RelationshipVersionSource.ACTIVE,
                            status=version.status,
                            progress={},
                        )

            run = await session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.owner_id == owner_id,
                    AnalysisRun.novel_id == novel_id,
                    AnalysisRun.active_key == "active",
                    AnalysisRun.status != "completed",
                    AnalysisRun.version_id == version_id,
                )
            )
            if (
                run is not None
                and source == RelationshipVersionSource.RUNNING_CANDIDATE
            ):
                return ResolvedVersion(
                    version_id=version_id,
                    source=RelationshipVersionSource.RUNNING_CANDIDATE,
                    status=run.status,
                    progress=dict(run.progress or {}),
                )

            if source == RelationshipVersionSource.HISTORY or (
                source == RelationshipVersionSource.ACTIVE and pointer is None
            ):
                # History: any owned version; Active+id without pointer: only if version is active status.
                if source == RelationshipVersionSource.HISTORY:
                    return ResolvedVersion(
                        version_id=version_id,
                        source=RelationshipVersionSource.HISTORY,
                        status=version.status,
                        progress={},
                    )
                if version.status == "active" or pointer is not None:
                    return ResolvedVersion(
                        version_id=version_id,
                        source=RelationshipVersionSource.ACTIVE,
                        status=version.status,
                        progress={},
                    )
                # Explicit version_id with default source: treat as history when owned.
                return ResolvedVersion(
                    version_id=version_id,
                    source=RelationshipVersionSource.HISTORY,
                    status=version.status,
                    progress={},
                )

            # Source mismatch for explicit id (e.g. asked running_candidate but not running).
            if source == RelationshipVersionSource.ACTIVE and pointer is not None:
                return ResolvedVersion(
                    version_id=version_id,
                    source=RelationshipVersionSource.ACTIVE,
                    status=version.status,
                    progress={},
                )
            if source == RelationshipVersionSource.RUNNING_CANDIDATE:
                return None
            return ResolvedVersion(
                version_id=version_id,
                source=RelationshipVersionSource.HISTORY,
                status=version.status,
                progress={},
            )

        if source == RelationshipVersionSource.ACTIVE:
            pointer = await session.scalar(
                select(TimelineActivePointer).where(
                    TimelineActivePointer.owner_id == owner_id,
                    TimelineActivePointer.novel_id == novel_id,
                )
            )
            if pointer is None:
                # Fallback: analysis version marked active for this novel/owner.
                version = await session.scalar(
                    select(AnalysisVersion)
                    .where(
                        AnalysisVersion.owner_id == owner_id,
                        AnalysisVersion.novel_id == novel_id,
                        AnalysisVersion.status == "active",
                    )
                    .order_by(AnalysisVersion.id.desc())
                    .limit(1)
                )
                if version is None:
                    return None
                return ResolvedVersion(
                    version_id=version.id,
                    source=RelationshipVersionSource.ACTIVE,
                    status=version.status,
                    progress={},
                )
            version = await session.get(AnalysisVersion, pointer.version_id)
            return ResolvedVersion(
                version_id=pointer.version_id,
                source=RelationshipVersionSource.ACTIVE,
                status=version.status if version else "active",
                progress={},
            )

        if source == RelationshipVersionSource.RUNNING_CANDIDATE:
            run = await session.scalar(
                select(AnalysisRun).where(
                    AnalysisRun.owner_id == owner_id,
                    AnalysisRun.novel_id == novel_id,
                    AnalysisRun.active_key == "active",
                    AnalysisRun.status != "completed",
                    AnalysisRun.version_id.is_not(None),
                )
            )
            if run is None or run.version_id is None:
                return None
            return ResolvedVersion(
                version_id=run.version_id,
                source=RelationshipVersionSource.RUNNING_CANDIDATE,
                status=run.status,
                progress=dict(run.progress or {}),
            )

        # HISTORY requires an explicit version_id.
        return None

    async def resolve_cutoff(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        source: RelationshipVersionSource,
        request_full_book: bool,
    ) -> tuple[int | None, bool]:
        """Return (cutoff_chapter, full_book_applied).

        D-09/D-10: missing/invalid progress → first chapter; full-book only when
        persisted timeline_full_book is true. Running candidate skips reading cutoff.
        """
        max_chapter = await session.scalar(
            select(Chapter.chapter_number)
            .where(Chapter.novel_id == novel.id)
            .order_by(Chapter.chapter_number.desc())
            .limit(1)
        )
        if max_chapter is None:
            return None, False

        progress = novel.reading_progress or {}
        persisted_full_book = bool(progress.get("timeline_full_book", False))
        full_book = bool(request_full_book and persisted_full_book)

        if source == RelationshipVersionSource.RUNNING_CANDIDATE or full_book:
            return int(max_chapter), full_book

        chapter_id = progress.get("chapter_id")
        if chapter_id is not None:
            try:
                chapter = await session.scalar(
                    select(Chapter).where(
                        Chapter.id == int(chapter_id),
                        Chapter.novel_id == novel.id,
                    )
                )
            except (TypeError, ValueError):
                chapter = None
            if chapter is not None:
                return int(chapter.chapter_number), False

        first = await session.scalar(
            select(Chapter.chapter_number)
            .where(Chapter.novel_id == novel.id)
            .order_by(Chapter.chapter_number)
            .limit(1)
        )
        return (int(first) if first is not None else None), False

    async def build_graph(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
        version_id: int | None = None,
        through_chapter: int | None = None,
        request_full_book: bool = False,
        character_id: int | None = None,
        relation_type: str | None = None,
        include_provisional: bool = False,
    ) -> RelationshipGraphEnvelope | None:
        """Fold accepted observations into a spoiler-safe graph envelope.

        Default graph is accepted observations only. Provisional timeline
        co-occurrence is used only when accepted is empty, or when the client
        opts in via ``include_provisional=true``.
        """

        resolved = await self.resolve_version(
            session,
            owner_id=owner_id,
            novel_id=novel.id,
            source=source,
            version_id=version_id,
        )
        if resolved is None:
            return None

        cutoff, full_book = await self.resolve_cutoff(
            session,
            novel=novel,
            source=resolved.source,
            request_full_book=request_full_book,
        )
        if cutoff is None:
            return RelationshipGraphEnvelope(
                novel_id=novel.id,
                version_id=resolved.version_id,
                source=resolved.source,
                through_chapter=1,
                full_book=False,
                cutoff_chapter=1,
                nodes=[],
                edges=[],
                counts=RelationshipCounts(),
                available_relation_types=[],
                available_character_ids=[],
                degradation=RelationshipDegradation(mode=GraphDegradationMode.NORMAL),
                generated_at=datetime.now(timezone.utc),
            )

        position = through_chapter if through_chapter is not None else cutoff
        if position < 1:
            position = 1
        if position > cutoff:
            position = cutoff

        identity_map = await self._identity_map(
            session,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=resolved.version_id,
        )
        override_fields = await self._active_relationship_overrides(
            session,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=resolved.version_id,
        )

        # Indexed prefilter: never expand the visible set. Character filter loads
        # only endpoint-related rows (including merged aliases) so 10k graphs stay
        # within the D-22 query budget after warmup.
        obs_filters = [
            RelationshipObservation.owner_id == owner_id,
            RelationshipObservation.novel_id == novel.id,
            RelationshipObservation.analysis_version_id == resolved.version_id,
            RelationshipObservation.status == "accepted",
        ]
        if character_id is not None:
            target = identity_map.get(character_id, character_id)
            related_ids = {character_id, target}
            for raw, canon in identity_map.items():
                if canon == target:
                    related_ids.add(raw)
            obs_filters.append(
                or_(
                    RelationshipObservation.source_character_id.in_(related_ids),
                    RelationshipObservation.target_character_id.in_(related_ids),
                )
            )

        observations = list(
            (
                await session.scalars(
                    select(RelationshipObservation)
                    .where(*obs_filters)
                    .order_by(
                        RelationshipObservation.valid_from_chapter,
                        RelationshipObservation.valid_from_narrative_index,
                        RelationshipObservation.id,
                    )
                )
            ).all()
        )

        covering = [
            obs
            for obs in observations
            if _covers_position(
                valid_from_chapter=obs.valid_from_chapter,
                valid_from_narrative_index=obs.valid_from_narrative_index,
                valid_to_chapter=obs.valid_to_chapter,
                valid_to_narrative_index=obs.valid_to_narrative_index,
                through_chapter=position,
            )
        ]

        folded = self._fold_observations(
            covering,
            identity_map=identity_map,
            override_fields=override_fields,
        )

        # Optional client filters apply only after fold (never expand the set).
        if character_id is not None:
            cid = identity_map.get(character_id, character_id)
            folded = [
                e
                for e in folded
                if e.source_character_id == cid or e.target_character_id == cid
            ]
        # Truth-tier decision must not depend on relation_type filter: if accepted
        # observations exist but none match the type filter, stay accepted-only
        # (empty edges) rather than silently flooding with provisional co-occur.
        has_accepted = bool(folded)
        if relation_type is not None:
            folded = [
                e
                for e in folded
                if e.relation_type == relation_type or e.suggested_type == relation_type
            ]

        evidence_by_obs = await self._evidence_for_observations(
            session,
            observation_ids=[
                e.observation_id
                for e in folded
                if e.edge_kind == RelationshipEdgeKind.ACCEPTED_OBSERVATION
            ],
        )
        for edge in folded:
            if edge.edge_kind != RelationshipEdgeKind.ACCEPTED_OBSERVATION:
                continue
            refs = evidence_by_obs.get(edge.observation_id, [])
            edge.evidence_count = len(refs)
            if refs:
                # Visible-set only: excerpt already attached to accepted evidence.
                edge.evidence_preview = (refs[0].excerpt or "")[:400] or None

        provisional_names: dict[int, str] = {}
        if not has_accepted:
            # Progressive product surface: when Phase 09 observations are empty,
            # derive a co-occurrence graph from timeline participants so the UI
            # is not blank. Edges are honesty-typed as cooccur, never fake ally.
            folded, provisional_names = await self._provisional_from_timeline(
                session,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=resolved.version_id,
                through_chapter=position,
                character_id=character_id,
                relation_type=relation_type,
            )
        elif include_provisional:
            # Opt-in layer: add provisional co-occurrence that does not duplicate
            # an already-accepted undirected character pair.
            provisional, provisional_names = await self._provisional_from_timeline(
                session,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=resolved.version_id,
                through_chapter=position,
                character_id=character_id,
                relation_type=relation_type,
            )
            accepted_pairs = {
                (
                    min(e.source_character_id, e.target_character_id),
                    max(e.source_character_id, e.target_character_id),
                )
                for e in folded
            }
            for edge in provisional:
                pair = (
                    min(edge.source_character_id, edge.target_character_id),
                    max(edge.source_character_id, edge.target_character_id),
                )
                if pair not in accepted_pairs:
                    folded.append(edge)

        node_ids: set[int] = set()
        for edge in folded:
            node_ids.add(edge.source_character_id)
            node_ids.add(edge.target_character_id)

        characters = {}
        if node_ids:
            rows = (
                await session.scalars(
                    select(Character).where(
                        Character.novel_id == novel.id,
                        Character.id.in_(node_ids),
                    )
                )
            ).all()
            characters = {row.id: row for row in rows}

        first_visible: dict[int, int] = {}
        for edge in folded:
            for cid in (edge.source_character_id, edge.target_character_id):
                prev = first_visible.get(cid)
                if prev is None or edge.valid_from_chapter < prev:
                    first_visible[cid] = edge.valid_from_chapter

        nodes = [
            RelationshipGraphNode(
                character_id=cid,
                name=(
                    characters[cid].name
                    if cid in characters
                    else provisional_names.get(cid, f"character:{cid}")
                ),
                aliases=_parse_aliases(
                    characters[cid].aliases if cid in characters else None
                ),
                first_visible_chapter=first_visible.get(cid, position),
            )
            for cid in sorted(node_ids)
        ]

        allowed_labels = {t.value for t in RelationshipGraphEdgeLabel}
        edges = [
            RelationshipGraphEdge(
                observation_id=e.observation_id,
                source_character_id=e.source_character_id,
                target_character_id=e.target_character_id,
                relation_type=RelationshipGraphEdgeLabel(e.relation_type),
                transition=e.transition,  # type: ignore[arg-type]
                confidence=e.confidence,
                valid_from_chapter=e.valid_from_chapter,
                valid_to_chapter=e.valid_to_chapter,
                provenance=e.provenance,
                evidence_preview=e.evidence_preview,
                evidence_count=e.evidence_count,
                edge_kind=e.edge_kind,
                suggested_type=(
                    RelationshipEdgeType(e.suggested_type)
                    if e.suggested_type in {t.value for t in RelationshipEdgeType}
                    else None
                ),
            )
            for e in folded
            if e.transition in ("establish", "change", "end")
            and e.relation_type in allowed_labels
        ]
        # Ended transitions are already removed by fold; keep only active edges.
        edges = [e for e in edges if e.transition != "end"]

        type_counts: dict[str, int] = defaultdict(int)
        for edge in edges:
            type_counts[edge.relation_type.value] += 1

        node_count = len(nodes)
        edge_count = len(edges)
        mode = self._degradation_mode(node_count, edge_count)

        available_types = sorted(
            {RelationshipGraphEdgeLabel(t) for t in type_counts.keys()},
            key=lambda t: t.value,
        )
        available_character_ids = sorted(node_ids)

        if mode == GraphDegradationMode.FILTERS_REQUIRED:
            # Hard cap: no graph elements; counts remain spoiler-safe.
            return RelationshipGraphEnvelope(
                novel_id=novel.id,
                version_id=resolved.version_id,
                source=resolved.source,
                through_chapter=position,
                full_book=full_book,
                cutoff_chapter=cutoff,
                nodes=[],
                edges=[],
                counts=RelationshipCounts(
                    nodes=node_count,
                    edges=edge_count,
                    relation_types=dict(type_counts),
                ),
                available_relation_types=available_types,
                available_character_ids=available_character_ids,
                degradation=RelationshipDegradation(
                    mode=mode,
                    node_count=node_count,
                    edge_count=edge_count,
                    hard_node_cap=HARD_NODE_CAP,
                    hard_edge_cap=HARD_EDGE_CAP,
                    message="Graph exceeds hard caps; apply filters before loading elements",
                ),
                generated_at=datetime.now(timezone.utc),
            )

        return RelationshipGraphEnvelope(
            novel_id=novel.id,
            version_id=resolved.version_id,
            source=resolved.source,
            through_chapter=position,
            full_book=full_book,
            cutoff_chapter=cutoff,
            nodes=nodes,
            edges=edges,
            counts=RelationshipCounts(
                nodes=node_count,
                edges=edge_count,
                relation_types=dict(type_counts),
            ),
            available_relation_types=available_types,
            available_character_ids=available_character_ids,
            degradation=RelationshipDegradation(
                mode=mode,
                node_count=node_count,
                edge_count=edge_count,
                hard_node_cap=HARD_NODE_CAP,
                hard_edge_cap=HARD_EDGE_CAP,
            ),
            generated_at=datetime.now(timezone.utc),
        )

    async def get_visible_evidence(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        observation_id: int,
        source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
        version_id: int | None = None,
        request_full_book: bool = False,
        through_chapter: int | None = None,
    ) -> RelationshipEvidenceResponse | None:
        """Return evidence only if the observation is in the visible folded set."""

        graph = await self.build_graph(
            session,
            novel=novel,
            owner_id=owner_id,
            source=source,
            version_id=version_id,
            through_chapter=through_chapter,
            request_full_book=request_full_book,
        )
        if graph is None:
            return None
        visible = next(
            (e for e in graph.edges if e.observation_id == observation_id), None
        )
        if visible is None:
            return None

        refs = await self._evidence_for_observations(
            session, observation_ids=[observation_id]
        )
        evidence = [
            RelationshipEvidenceRef(
                evidence_id=row.evidence_id,
                chapter_id=row.chapter_id,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=row.excerpt,
            )
            for row in refs.get(observation_id, [])
        ]
        return RelationshipEvidenceResponse(
            observation_id=observation_id,
            novel_id=novel.id,
            version_id=graph.version_id,
            through_chapter=graph.through_chapter,
            relation_type=visible.relation_type,
            source_character_id=visible.source_character_id,
            target_character_id=visible.target_character_id,
            evidence=evidence,
            provenance=visible.provenance,
        )

    # ------------------------------------------------------------------
    # Phase 10 / Phase 11 read-only contracts (D-23 / D-24)
    # ------------------------------------------------------------------

    async def load_filtered_relationship_graph(
        self,
        session: AsyncSession,
        *,
        novel: Novel,
        owner_id: int,
        source: RelationshipVersionSource = RelationshipVersionSource.ACTIVE,
        version_id: int | None = None,
        through_chapter: int | None = None,
        request_full_book: bool = False,
        character_id: int | None = None,
        relation_type: str | None = None,
    ) -> RelationshipGraphEnvelope | None:
        """Phase 10 public read-only contract: already owner/version/spoiler filtered.

        Chat text and answers must never be candidate sources. This method does
        not create sessions, messages, or write paths.
        """
        return await self.build_graph(
            session,
            novel=novel,
            owner_id=owner_id,
            source=source,
            version_id=version_id,
            through_chapter=through_chapter,
            request_full_book=request_full_book,
            character_id=character_id,
            relation_type=relation_type,
        )

    async def list_accepted_observation_refs(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        through_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        """Phase 11 contract: accepted observation IDs, character IDs, evidence refs.

        Does not create clue tables, state machines, or UI. Spoiler filtering is
        applied when through_chapter is provided.
        """
        rows = list(
            (
                await session.scalars(
                    select(RelationshipObservation).where(
                        RelationshipObservation.owner_id == owner_id,
                        RelationshipObservation.novel_id == novel_id,
                        RelationshipObservation.analysis_version_id == version_id,
                        RelationshipObservation.status == "accepted",
                    )
                )
            ).all()
        )
        if through_chapter is not None:
            rows = [
                r
                for r in rows
                if _covers_position(
                    valid_from_chapter=r.valid_from_chapter,
                    valid_from_narrative_index=r.valid_from_narrative_index,
                    valid_to_chapter=r.valid_to_chapter,
                    valid_to_narrative_index=r.valid_to_narrative_index,
                    through_chapter=through_chapter,
                )
            ]
        evidence = await self._evidence_for_observations(
            session, observation_ids=[r.id for r in rows]
        )
        payload: list[dict[str, Any]] = []
        for row in rows:
            payload.append(
                {
                    "observation_id": row.id,
                    "analysis_version_id": row.analysis_version_id,
                    "source_character_id": row.source_character_id,
                    "target_character_id": row.target_character_id,
                    "relation_type": row.relation_type,
                    "evidence": [
                        {
                            "evidence_id": e.evidence_id,
                            "chapter_id": e.chapter_id,
                            "source_start": e.source_start,
                            "source_end": e.source_end,
                            "content_hash": e.content_hash,
                        }
                        for e in evidence.get(row.id, [])
                    ],
                }
            )
        return payload

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _mention_synthetic_id(mention: str) -> int:
        """Stable positive graph id for mention-only nodes (no characters row yet)."""
        import hashlib

        digest = hashlib.sha1(mention.strip().lower().encode("utf-8")).hexdigest()
        # Keep in positive int32-ish range, avoid 0.
        return (int(digest[:8], 16) % 1_900_000_000) + 1

    @staticmethod
    def _pair_synthetic_id(
        source_id: int, target_id: int, relation_type: str = "ally"
    ) -> int:
        """Stable unique provisional observation id for a character pair + type."""
        import hashlib

        lo, hi = (
            (source_id, target_id) if source_id <= target_id else (target_id, source_id)
        )
        digest = hashlib.sha1(
            f"rel:{lo}:{hi}:{relation_type}".encode("utf-8")
        ).hexdigest()
        return (int(digest[:8], 16) % 1_900_000_000) + 1

    # Keyword heuristics for provisional typing (zh + common novel terms).
    # Priority: more specific types first when multiple match.
    _TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
        RelationshipEdgeType.ENEMY.value: (
            "战斗",
            "对决",
            "开战",
            "攻打",
            "攻击",
            "击败",
            "击杀",
            "杀死",
            "杀害",
            "仇敌",
            "敌人",
            "敌对",
            "对峙",
            "追杀",
            "讨伐",
            "交战",
            "开战",
            "冲突",
            "挑衅",
            "侮辱",
            "背叛",
            "反叛",
            "魔王战",
            "死斗",
            "斩杀",
            "围攻",
            "侵攻",
            "侵略",
            "battle",
            "fight",
            "enemy",
            "kill",
        ),
        RelationshipEdgeType.FAMILY.value: (
            "父亲",
            "母亲",
            "父女",
            "父子",
            "母女",
            "母子",
            "兄妹",
            "姐弟",
            "兄弟",
            "姐妹",
            "哥哥",
            "姐姐",
            "弟弟",
            "妹妹",
            "儿子",
            "女儿",
            "亲人",
            "血缘",
            "家族",
            "家人",
            "妻子",
            "丈夫",
            "夫妻",
            "父",
            "母",
            "family",
            "father",
            "mother",
            "sibling",
        ),
        RelationshipEdgeType.MENTOR.value: (
            "师父",
            "师傅",
            "徒弟",
            "弟子",
            "传授",
            "教导",
            "指导",
            "师从",
            "拜师",
            "收徒",
            "训练",
            "培养",
            "指点",
            "mentor",
            "master",
            "disciple",
            "apprentice",
        ),
        RelationshipEdgeType.ROMANTIC.value: (
            "恋爱",
            "恋人",
            "告白",
            "表白",
            "亲吻",
            "接吻",
            "结婚",
            "婚约",
            "爱慕",
            "喜欢",
            "倾心",
            "情人",
            "伴侣",
            "romance",
            "love",
            "kiss",
            "marry",
        ),
        RelationshipEdgeType.ALLY.value: (
            "同盟",
            "结盟",
            "盟友",
            "并肩",
            "合作",
            "帮助",
            "救援",
            "援护",
            "部下",
            "主从",
            "效忠",
            "誓约",
            "结成",
            "命名",
            "庇护",
            "守护",
            "好友",
            "伙伴",
            "友军",
            "ally",
            "friend",
            "allyship",
        ),
    }

    @classmethod
    def _infer_provisional_type(
        cls, *, title: str, description: str, event_type: str
    ) -> str:
        """Infer edge type from event text + timeline event_type (heuristic)."""
        blob = f"{title or ''}\n{description or ''}".lower()
        et = (event_type or "").lower().strip()

        # Event-type prior (timeline schema: conflict/plot/character/world).
        if et in {"conflict", "battle", "fight", "war"}:
            base = RelationshipEdgeType.ENEMY.value
        elif et in {"character", "dialogue", "social"}:
            base = RelationshipEdgeType.ALLY.value
        else:
            base = RelationshipEdgeType.ALLY.value

        scores: dict[str, int] = {t.value: 0 for t in RelationshipEdgeType}
        scores[base] += 1
        if et in {"conflict", "battle", "fight", "war"}:
            scores[RelationshipEdgeType.ENEMY.value] += 3

        for rel_type, keywords in cls._TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in blob:
                    scores[rel_type] += 2

        # Prefer non-ally when it has clear keyword evidence.
        ranked = sorted(
            scores.items(),
            key=lambda item: (
                item[1],
                1 if item[0] != RelationshipEdgeType.ALLY.value else 0,
            ),
            reverse=True,
        )
        best_type, best_score = ranked[0]
        if best_score <= 0:
            return RelationshipEdgeType.ALLY.value
        return best_type

    async def _provisional_from_timeline(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        through_chapter: int,
        character_id: int | None,
        relation_type: str | None,
    ) -> tuple[list[_FoldedEdge], dict[int, str]]:
        """Provisional co-occurrence graph from timeline participants.

        Primary label is always ``cooccur`` (not a confirmed fiction type).
        Heuristic ally/enemy/… live only in ``suggested_type`` and preview text
        as non-assertive type clues, never as accepted observations.
        """
        events = list(
            (
                await session.scalars(
                    select(MachineTimelineEvent).where(
                        MachineTimelineEvent.owner_id == owner_id,
                        MachineTimelineEvent.novel_id == novel_id,
                        MachineTimelineEvent.version_id == version_id,
                        MachineTimelineEvent.narrative_chapter_number
                        <= through_chapter,
                    )
                )
            ).all()
        )
        if not events:
            return [], {}

        event_by_id = {e.id: e for e in events}
        event_ids = list(event_by_id.keys())
        parts = list(
            (
                await session.scalars(
                    select(TimelineParticipant).where(
                        TimelineParticipant.event_id.in_(event_ids)
                    )
                )
            ).all()
        )
        by_event: dict[int, list[TimelineParticipant]] = defaultdict(list)
        for p in parts:
            by_event[p.event_id].append(p)

        # pair -> aggregate co-occurrence + per-type votes
        pair_stats: dict[tuple[int, int], dict[str, Any]] = {}
        names: dict[int, str] = {}

        for event_id, plist in by_event.items():
            event = event_by_id.get(event_id)
            if event is None:
                continue
            inferred = self._infer_provisional_type(
                title=event.title or "",
                description=event.description or "",
                event_type=event.event_type or "",
            )
            seen: dict[int, str] = {}
            for p in plist:
                mention = (p.mention or "").strip()
                if not mention:
                    continue
                cid = (
                    p.entity_id
                    if p.entity_id is not None
                    else self._mention_synthetic_id(mention)
                )
                names[cid] = mention if p.entity_id is None else names.get(cid, mention)
                seen[cid] = mention
            ids = sorted(seen.keys())
            ch = event.narrative_chapter_number
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    key = (a, b)
                    st = pair_stats.get(key)
                    if st is None:
                        st = {
                            "count": 0,
                            "first_chapter": ch,
                            "type_votes": defaultdict(int),
                            "type_samples": {},
                        }
                        pair_stats[key] = st
                    st["count"] += 1
                    st["type_votes"][inferred] += 1
                    if ch < st["first_chapter"]:
                        st["first_chapter"] = ch
                    # Keep a short sample title per type for preview.
                    if inferred not in st["type_samples"]:
                        st["type_samples"][inferred] = (event.title or "")[:80]

        # One co-occurrence edge per pair; optional suggested_type for UI tint.
        # Quotas still diversify by suggested heuristic so conflict arcs surface
        # without claiming accepted fiction types.
        min_cooccur = 2
        type_quota = {
            RelationshipEdgeType.ENEMY.value: 14,
            RelationshipEdgeType.ALLY.value: 14,
            RelationshipEdgeType.FAMILY.value: 8,
            RelationshipEdgeType.MENTOR.value: 8,
            RelationshipEdgeType.ROMANTIC.value: 6,
        }
        type_label = {
            "ally": "同盟/协作",
            "enemy": "敌对/冲突",
            "family": "亲属",
            "mentor": "师徒",
            "romantic": "爱慕",
        }
        cooccur_label = RelationshipGraphEdgeLabel.COOCCUR.value

        # (pair, suggested_t, vote_n, st)
        typed: list[tuple[tuple[int, int], str, int, dict[str, Any]]] = []
        for key, st in pair_stats.items():
            if st["count"] < min_cooccur:
                continue
            a, b = key
            if character_id is not None and character_id not in (a, b):
                continue
            votes: dict[str, int] = dict(st["type_votes"])
            if not votes:
                votes = {RelationshipEdgeType.ALLY.value: int(st["count"])}
            ordered = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
            selected: list[tuple[str, int]] = [ordered[0]]
            if len(ordered) > 1:
                top_t, top_v = ordered[0]
                second_t, second_v = ordered[1]
                if second_v >= max(2, int(top_v * 0.4)) and second_t != top_t:
                    selected.append((second_t, second_v))
            for suggested_t, vote_n in selected:
                if relation_type is not None and relation_type not in (
                    suggested_t,
                    cooccur_label,
                ):
                    continue
                if vote_n < 1:
                    continue
                typed.append((key, suggested_t, vote_n, st))

        # Prefer higher votes; enemy slightly boosted so conflict arcs surface.
        def sort_key(
            item: tuple[tuple[int, int], str, int, dict[str, Any]],
        ) -> tuple[int, int, int, tuple[int, int]]:
            key, suggested_t, vote_n, st = item
            boost = 3 if suggested_t == RelationshipEdgeType.ENEMY.value else 0
            if suggested_t in (
                RelationshipEdgeType.FAMILY.value,
                RelationshipEdgeType.MENTOR.value,
                RelationshipEdgeType.ROMANTIC.value,
            ):
                boost = 2
            return (-(vote_n + boost), -int(st["count"]), int(st["first_chapter"]), key)

        typed.sort(key=sort_key)

        used_quota: dict[str, int] = defaultdict(int)
        folded: list[_FoldedEdge] = []
        # Deduplicate undirected pair so multi-suggested does not double-claim.
        seen_pairs: set[tuple[int, int]] = set()
        for (a, b), suggested_t, vote_n, st in typed:
            pair_key = (a, b)
            if pair_key in seen_pairs:
                continue
            if used_quota[suggested_t] >= type_quota.get(suggested_t, 8):
                continue
            sample = (st.get("type_samples") or {}).get(suggested_t) or ""
            clue_label = type_label.get(suggested_t, suggested_t)
            preview = (
                f"时间线共现×{int(st['count'])}"
                f"（类型线索·{clue_label}×{int(vote_n)}，非已确认关系）"
                + (f"：{sample}" if sample else "")
                + " · 临时图"
            )
            folded.append(
                _FoldedEdge(
                    observation_id=self._pair_synthetic_id(a, b, cooccur_label),
                    source_character_id=a,
                    target_character_id=b,
                    relation_type=cooccur_label,
                    transition="establish",
                    confidence=min(
                        0.55, 0.22 + 0.03 * int(vote_n) + 0.02 * int(st["count"])
                    ),
                    valid_from_chapter=int(st["first_chapter"]),
                    valid_to_chapter=None,
                    logical_key=logical_relationship_key(a, b, cooccur_label),
                    provenance=ProvenanceKind.MACHINE,
                    evidence_preview=preview[:400],
                    evidence_count=int(st["count"]),
                    edge_kind=RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE,
                    suggested_type=suggested_t,
                )
            )
            used_quota[suggested_t] += 1
            seen_pairs.add(pair_key)

        return folded, names

    @staticmethod
    def _degradation_mode(node_count: int, edge_count: int) -> GraphDegradationMode:
        if node_count > HARD_NODE_CAP or edge_count > HARD_EDGE_CAP:
            return GraphDegradationMode.FILTERS_REQUIRED
        if node_count > NORMAL_NODE_CAP or edge_count > NORMAL_EDGE_CAP:
            return GraphDegradationMode.LARGE
        return GraphDegradationMode.NORMAL

    def _fold_observations(
        self,
        observations: Iterable[RelationshipObservation],
        *,
        identity_map: dict[int, int],
        override_fields: dict[str, dict[str, Any]],
    ) -> list[_FoldedEdge]:
        """Deterministic transition fold per logical relationship key (D-06)."""

        by_key: dict[str, list[RelationshipObservation]] = defaultdict(list)
        for obs in observations:
            src = identity_map.get(obs.source_character_id, obs.source_character_id)
            tgt = identity_map.get(obs.target_character_id, obs.target_character_id)
            if src == tgt:
                continue
            key = logical_relationship_key(src, tgt, obs.relation_type)
            by_key[key].append(obs)

        folded: list[_FoldedEdge] = []
        for key, chain in by_key.items():
            chain.sort(
                key=lambda o: (
                    o.valid_from_chapter,
                    o.valid_from_narrative_index,
                    o.id,
                )
            )
            current: _FoldedEdge | None = None
            for obs in chain:
                src = identity_map.get(obs.source_character_id, obs.source_character_id)
                tgt = identity_map.get(obs.target_character_id, obs.target_character_id)
                if obs.transition == "end":
                    current = None
                    continue
                current = _FoldedEdge(
                    observation_id=obs.id,
                    source_character_id=src,
                    target_character_id=tgt,
                    relation_type=obs.relation_type,
                    transition=obs.transition,
                    confidence=float(obs.confidence),
                    valid_from_chapter=obs.valid_from_chapter,
                    valid_to_chapter=obs.valid_to_chapter,
                    logical_key=key,
                    provenance=ProvenanceKind.MACHINE,
                )

            if current is None:
                continue

            # Apply latest eligible overrides for this logical key (overlay only).
            patches = override_fields.get(current.logical_key, {})
            # Also accept overrides keyed without type if type was changed.
            if not patches:
                # Try all override keys that share endpoints.
                for okey, fields in override_fields.items():
                    parts = okey.split(":")
                    if (
                        len(parts) == 3
                        and parts[0] == str(current.source_character_id)
                        and parts[1] == str(current.target_character_id)
                    ):
                        patches = fields
                        break

            provenance = ProvenanceKind.MACHINE
            if patches:
                provenance = ProvenanceKind.MANUAL
                if "relation_type" in patches:
                    value = patches["relation_type"]
                    if isinstance(value, dict):
                        value = value.get("relation_type", current.relation_type)
                    current.relation_type = str(value)
                if "transition" in patches:
                    value = patches["transition"]
                    if isinstance(value, dict):
                        value = value.get("transition", current.transition)
                    current.transition = str(value)
                if "valid_from" in patches:
                    value = patches["valid_from"]
                    if isinstance(value, dict) and "valid_from_chapter" in value:
                        current.valid_from_chapter = int(value["valid_from_chapter"])
                if "valid_to" in patches:
                    value = patches["valid_to"]
                    if isinstance(value, dict):
                        if value.get("valid_to_chapter") is None:
                            current.valid_to_chapter = None
                        else:
                            current.valid_to_chapter = int(value["valid_to_chapter"])
                current.provenance = provenance

            if current.transition == "end":
                continue
            folded.append(current)

        folded.sort(
            key=lambda e: (
                e.valid_from_chapter,
                e.source_character_id,
                e.target_character_id,
                e.observation_id,
            )
        )
        return folded

    async def _identity_map(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> dict[int, int]:
        """Latest-wins character merge map from append-only identity overrides."""

        rows = list(
            (
                await session.scalars(
                    select(CharacterIdentityOverride)
                    .where(
                        CharacterIdentityOverride.owner_id == owner_id,
                        CharacterIdentityOverride.novel_id == novel_id,
                        CharacterIdentityOverride.analysis_version_id == version_id,
                    )
                    .order_by(CharacterIdentityOverride.id)
                )
            ).all()
        )
        # Latest row by canonical target wins; needs_relink does not apply merges.
        latest_by_merged: dict[int, CharacterIdentityOverride] = {}
        for row in rows:
            for mid in row.merged_character_ids or []:
                latest_by_merged[int(mid)] = row
        mapping: dict[int, int] = {}
        for mid, row in latest_by_merged.items():
            if row.status != "active":
                continue
            mapping[mid] = row.canonical_character_id
            mapping[row.canonical_character_id] = row.canonical_character_id
        return mapping

    async def _active_relationship_overrides(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> dict[str, dict[str, Any]]:
        """Latest-wins field patches per logical key (append-only supersession)."""

        rows = list(
            (
                await session.scalars(
                    select(RelationshipOverride)
                    .where(
                        RelationshipOverride.owner_id == owner_id,
                        RelationshipOverride.novel_id == novel_id,
                        RelationshipOverride.analysis_version_id == version_id,
                    )
                    .order_by(RelationshipOverride.id)
                )
            ).all()
        )
        # Highest id per (logical_key, field_name) wins; only status=active applies.
        latest: dict[tuple[str, str], RelationshipOverride] = {}
        for row in rows:
            latest[(row.logical_relationship_key, row.field_name)] = row

        result: dict[str, dict[str, Any]] = defaultdict(dict)
        for (key, field), row in latest.items():
            if row.status != "active":
                continue
            result[key][field] = deepcopy(row.value)
        return dict(result)

    async def _evidence_for_observations(
        self,
        session: AsyncSession,
        *,
        observation_ids: list[int],
    ) -> dict[int, list[RelationshipEvidenceLink]]:
        if not observation_ids:
            return {}
        rows = list(
            (
                await session.scalars(
                    select(RelationshipEvidenceLink)
                    .where(RelationshipEvidenceLink.observation_id.in_(observation_ids))
                    .order_by(
                        RelationshipEvidenceLink.observation_id,
                        RelationshipEvidenceLink.sort_order,
                        RelationshipEvidenceLink.id,
                    )
                )
            ).all()
        )
        out: dict[int, list[RelationshipEvidenceLink]] = defaultdict(list)
        for row in rows:
            out[row.observation_id].append(row)
        return dict(out)


relationship_graph_query_service = RelationshipGraphQueryService()
