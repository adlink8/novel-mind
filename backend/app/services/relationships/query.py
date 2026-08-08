"""
Owner/version/spoiler-scoped relationship graph read model.

PostgreSQL accepted observations are the sole fact source. Nodes, edges,
filters, counts and evidence previews all derive from one visible-set fold.
Legacy CharacterRelation is never read.

拆分说明（refactor split）：折叠/退化/provisional seam 拆到
``_query_fold.py``（``FoldQueryMixin``），identity/override/evidence 加载拆到
``_query_identity.py``（``IdentityQueryMixin``），共享纯工具
（``logical_relationship_key`` / ``_FoldedEdge`` / ``_covers_position`` /
``_position_tuple`` / D-22 caps）下沉到叶模块 ``query_primitives.py`` 并在本
模块 re-export——``RelationshipGraphQueryService`` /
``relationship_graph_query_service`` / ``logical_relationship_key`` /
``_FoldedEdge`` / caps 的 import surface 不变。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisRun, AnalysisVersion
from app.models.character import Character
from app.models.novel import Chapter, Novel
from app.models.relationship import RelationshipObservation
from app.models.timeline import TimelineActivePointer
from app.schemas.relationship import (
    GraphDegradationMode,
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
    RelationshipIntakeKind,
    RelationshipVersionSource,
)

from ._query_fold import FoldQueryMixin
from ._query_identity import IdentityQueryMixin
from .query_primitives import (
    HARD_EDGE_CAP,
    HARD_NODE_CAP,
    NORMAL_EDGE_CAP,
    NORMAL_NODE_CAP,
    _FoldedEdge,
    _covers_position,
    _position_tuple,
    logical_relationship_key,
)

__all__ = [
    # re-exported from query_primitives (leaf) — unchanged import surface.
    "HARD_EDGE_CAP",
    "HARD_NODE_CAP",
    "NORMAL_EDGE_CAP",
    "NORMAL_NODE_CAP",
    "_FoldedEdge",
    "_covers_position",
    "_position_tuple",
    "logical_relationship_key",
    "ResolvedVersion",
    "RelationshipGraphQueryService",
    "relationship_graph_query_service",
]


@dataclass(frozen=True)
class ResolvedVersion:
    version_id: int
    source: RelationshipVersionSource
    status: str
    progress: dict[str, Any]


def _parse_aliases(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


class RelationshipGraphQueryService(
    FoldQueryMixin,
    IdentityQueryMixin,
):
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
                intake_kind=(
                    RelationshipIntakeKind(e.intake_kind)
                    if e.intake_kind in {k.value for k in RelationshipIntakeKind}
                    else RelationshipIntakeKind.UNKNOWN
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
                    "intake_kind": (
                        getattr(row, "intake_kind", None)
                        or RelationshipIntakeKind.UNKNOWN.value
                    ),
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


relationship_graph_query_service = RelationshipGraphQueryService()
