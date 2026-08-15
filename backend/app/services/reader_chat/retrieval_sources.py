"""Reader Chat evidence source fetch layers (hierarchy / timeline / relationship / knowledge).

四个来源的 fetch 器 + Phase 09 只读读者适配器（``Phase09RelationshipObservationReader``）
+ active pointer 解析（``resolve_active_hierarchy`` / ``resolve_active_analysis_version``）。
依赖方向：本模块只依赖 ``retrieval_types``（契约层）与 ORM 模型 / relationships 公开
API，不反向 import context / conversations。

拆分说明（refactor split）：原 ``retrieval.py`` 按职责域拆为 ``retrieval_types`` /
``retrieval_sources`` / ``retrieval_snapshot`` 三模块，``retrieval.py`` 保留为门面
并显式 re-export 全部顶层符号。``SOURCE_PRIORITY`` 单例定义在 ``retrieval_types``，
此处只读不重定义。
"""

from __future__ import annotations

from typing import Any

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

from .retrieval_types import (
    DEFAULT_MAX_PER_SOURCE,
    SOURCE_PRIORITY,
    RelationshipObservationEvidence,
    RelationshipObservationItem,
    RelationshipObservationReader,
    RetrievedEvidence,
    SourceStatus,
    bound_excerpt,
    overlaps,
    revalidate_observation_item,
)


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
                rank_key=(
                    0 if is_overlap else 1,
                    node.chapter_number,
                    node.source_start,
                ),
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


async def fetch_knowledge_evidence(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    cutoff_chapter: int | None,
    full_book: bool,
    chapters_by_number: dict[int, Chapter],
    max_items: int = DEFAULT_MAX_PER_SOURCE,
) -> tuple[list[RetrievedEvidence], int, str]:
    """问答按需分析（chat_backfill）物化的域表 candidate 证据检索（Phase 40）。

    读三个域表的 candidate 行（world_model_knowledge / key_scene_evidence_ranges /
    visual_bible_evidence_refs），转 RetrievedEvidence 进上下文。全部带
    ``candidate: True`` 标记——候选证据，不是正式批准事实（D-05 诚实呈现）。

    仅当有候选行时返回 ``ok``（维度从 ABSENT 转 OK，worker 的 abstain 判定随之
    转向可回答/partial）；无行则 ABSENT（不虚造）。
    """
    if version_id is None:
        return [], 0, SourceStatus.ABSENT

    items: list[RetrievedEvidence] = []

    # 1) world_model_knowledge：gate_status='passed' + disclosure 过滤的 claims
    from app.models.world_model_knowledge import WorldModelKnowledge
    from app.services.world_model.knowledge import EpistemicClaim

    wm_rows = (
        await session.scalars(
            select(WorldModelKnowledge)
            .where(
                WorldModelKnowledge.owner_id == owner_id,
                WorldModelKnowledge.novel_id == novel_id,
                WorldModelKnowledge.version_id == version_id,
                WorldModelKnowledge.gate_status == "passed",
            )
            .order_by(WorldModelKnowledge.known_at.asc())
        )
    ).all()
    for row in wm_rows:
        if not full_book and row.disclosure_cutoff > (cutoff_chapter or 0):
            continue
        try:
            claim = EpistemicClaim.model_validate(dict(row.canonical_payload or {}))
        except Exception:  # noqa: BLE001 - 坏行诚实跳过，不阻断检索
            continue
        for ref in claim.source_refs:
            ch = chapters_by_number.get(ref.chapter_number)
            excerpt = ""
            if ch is not None:
                excerpt = bound_excerpt(
                    (ch.content or "")[ref.source_start : ref.source_end]
                )
            items.append(
                RetrievedEvidence(
                    evidence_key=f"knowledge:wm:{claim.knowledge_key}:{ref.evidence_id}",
                    source_type="knowledge",
                    source_id=f"wm:{claim.knowledge_key}",
                    chapter_id=ref.chapter_id,
                    chapter_number=ref.chapter_number,
                    source_start=ref.source_start,
                    source_end=ref.source_end,
                    content_hash=ref.content_hash,
                    excerpt=excerpt,
                    version_lineage={
                        "candidate": True,
                        "authority": claim.authority.value,
                        "epistemic_status": claim.epistemic_status.value,
                        "gate_status": row.gate_status,
                        "source_kind": claim.source_kind.value,
                    },
                    priority=SOURCE_PRIORITY["knowledge"],
                    rank_key=(ref.chapter_number, ref.source_start, ref.evidence_id),
                )
            )

    # 2) key_scene_evidence_ranges：候选集的 leaf 证据行
    from app.models.key_scene import SceneCandidateSet, SceneEvidenceRange

    ks_rows = (
        await session.scalars(
            select(SceneEvidenceRange)
            .join(
                SceneCandidateSet,
                SceneCandidateSet.id == SceneEvidenceRange.set_id,
            )
            .where(
                SceneEvidenceRange.owner_id == owner_id,
                SceneEvidenceRange.novel_id == novel_id,
            )
            .order_by(SceneEvidenceRange.chapter_number.asc())
        )
    ).all()
    for row in ks_rows:
        if not full_book and row.cutoff_chapter > (cutoff_chapter or 0):
            continue
        ch = chapters_by_number.get(row.chapter_number)
        excerpt = bound_excerpt(row.excerpt or "") if ch else ""
        items.append(
            RetrievedEvidence(
                evidence_key=f"knowledge:ks:{row.evidence_key}",
                source_type="knowledge",
                source_id=f"ks:{row.set_id}",
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=excerpt,
                version_lineage={
                    "candidate": True,
                    "review_state": "candidate",
                    "source": "key_scene",
                },
                priority=SOURCE_PRIORITY["knowledge"],
                rank_key=(row.chapter_number, row.source_start, row.evidence_key),
            )
        )

    # 3) visual_bible_evidence_refs：候选版本的 leaf 证据行
    from app.models.visual_bible import VisualBibleVersion, VisualEvidenceRef

    vb_rows = (
        await session.scalars(
            select(VisualEvidenceRef)
            .join(
                VisualBibleVersion,
                VisualBibleVersion.id == VisualEvidenceRef.version_id,
            )
            .where(
                VisualEvidenceRef.owner_id == owner_id,
                VisualEvidenceRef.novel_id == novel_id,
            )
            .order_by(VisualEvidenceRef.chapter_number.asc())
        )
    ).all()
    for row in vb_rows:
        if not full_book and row.cutoff_chapter > (cutoff_chapter or 0):
            continue
        ch = chapters_by_number.get(row.chapter_number)
        excerpt = bound_excerpt(row.excerpt or "") if ch else ""
        items.append(
            RetrievedEvidence(
                evidence_key=f"knowledge:vb:{row.evidence_key}",
                source_type="knowledge",
                source_id=f"vb:{row.version_id}",
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=excerpt,
                version_lineage={
                    "candidate": True,
                    "review_state": "candidate",
                    "source": "visual_bible",
                },
                priority=SOURCE_PRIORITY["knowledge"],
                rank_key=(row.chapter_number, row.source_start, row.evidence_key),
            )
        )

    if not items:
        return [], 0, SourceStatus.ABSENT
    items.sort(key=lambda i: i.rank_key)
    omitted = max(0, len(items) - max_items)
    return items[:max_items], omitted, SourceStatus.OK
