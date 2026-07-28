"""Bounded multi-level descent, mixed union/dedupe, and safe fallback.

Every expansion repeats the immutable RetrievalScope. Upper levels organize
visible candidates; they never become citations.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
)
from app.services.narrative_memory.candidate_reader import (
    load_child_nodes_via_edges,
    load_visible_claims,
    load_visible_nodes,
    load_visible_source_links,
)
from app.services.narrative_memory.retrieval_contracts import (
    FallbackReasonCode,
    RetrievalScope,
    RouteDecision,
    RouteMode,
    SafeSourceStatus,
    StartLevel,
    TraversalStep,
)
from sqlalchemy import select


@dataclass
class ProposedLeaf:
    """Unvalidated leaf proposal prior to fresh citation re-slice."""

    hierarchy_build_id: str
    evidence_node_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    source_snapshot_hash: str
    link_id: int | None = None
    claim_id: int | None = None
    origin: str = "memory"  # memory | raw_fallback

    @property
    def identity(self) -> tuple:
        return (
            self.hierarchy_build_id,
            self.evidence_node_id,
            self.chapter_id,
            self.source_start,
            self.source_end,
            self.content_hash,
        )


@dataclass
class DescentResult:
    traversal: list[TraversalStep] = field(default_factory=list)
    proposed_leaves: list[ProposedLeaf] = field(default_factory=list)
    fallback_reason: FallbackReasonCode = FallbackReasonCode.NONE
    source_status: SafeSourceStatus = SafeSourceStatus.OK
    omitted_after_budget: int = 0
    visited_node_ids: set[int] = field(default_factory=set)

    def deduped_leaves(self) -> list[ProposedLeaf]:
        seen: set[tuple] = set()
        out: list[ProposedLeaf] = []
        for leaf in sorted(
            self.proposed_leaves,
            key=lambda leaf: (
                leaf.chapter_number,
                leaf.source_start,
                leaf.evidence_node_id,
                leaf.link_id or 0,
            ),
        ):
            if leaf.identity in seen:
                continue
            seen.add(leaf.identity)
            out.append(leaf)
        return out


def _step(
    *,
    level: str,
    candidate_key: str,
    parent_key: str | None,
    relation: str,
    visible_count: int,
    omitted: int,
    outcome: str,
) -> TraversalStep:
    return TraversalStep(
        level=level,
        candidate_key=candidate_key,
        parent_key=parent_key,
        relation=relation,
        visible_candidate_count=visible_count,
        omitted_after_budget=omitted,
        outcome=outcome,
    )


async def _nodes_for_kinds(
    session: AsyncSession,
    scope: RetrievalScope,
    kinds: tuple[StartLevel, ...],
) -> tuple[list[NarrativeMemoryNode], int]:
    return await load_visible_nodes(session, scope, kinds=kinds)


async def _expand_claims_and_links(
    session: AsyncSession,
    scope: RetrievalScope,
    result: DescentResult,
    nodes: list[NarrativeMemoryNode],
    *,
    parent_key: str,
    depth: int,
) -> None:
    if depth > scope.budgets.max_depth:
        result.fallback_reason = FallbackReasonCode.BUDGET_EXHAUSTED
        result.omitted_after_budget += 1
        result.traversal.append(
            _step(
                level="budget",
                candidate_key="depth",
                parent_key=parent_key,
                relation="limit",
                visible_count=0,
                omitted=1,
                outcome="budget_exhausted",
            )
        )
        return

    node_ids = [n.id for n in nodes if n.id not in result.visited_node_ids]
    for n in nodes:
        result.visited_node_ids.add(n.id)

    if not node_ids:
        result.traversal.append(
            _step(
                level="claim",
                candidate_key="none",
                parent_key=parent_key,
                relation="expand",
                visible_count=0,
                omitted=0,
                outcome="no_visible_child",
            )
        )
        if result.fallback_reason is FallbackReasonCode.NONE:
            result.fallback_reason = FallbackReasonCode.NO_VISIBLE_CHILD
        return

    remaining = scope.budgets.max_fanout
    claims, claim_omitted = await load_visible_claims(
        session, scope, node_ids=node_ids, limit=remaining
    )
    result.omitted_after_budget += claim_omitted
    result.traversal.append(
        _step(
            level="claim",
            candidate_key=f"nodes:{len(node_ids)}",
            parent_key=parent_key,
            relation="claims",
            visible_count=len(claims),
            omitted=claim_omitted,
            outcome="admitted" if claims else "no_visible_child",
        )
    )
    if not claims:
        if result.fallback_reason is FallbackReasonCode.NONE:
            result.fallback_reason = FallbackReasonCode.NO_VISIBLE_CHILD
        return

    claim_ids = [c.id for c in claims[: scope.budgets.max_claims]]
    links, link_omitted = await load_visible_source_links(
        session, scope, claim_ids=claim_ids, limit=scope.budgets.max_leaves
    )
    result.omitted_after_budget += link_omitted
    result.traversal.append(
        _step(
            level="source_link",
            candidate_key=f"claims:{len(claim_ids)}",
            parent_key=parent_key,
            relation="source_links",
            visible_count=len(links),
            omitted=link_omitted,
            outcome="admitted" if links else "no_visible_child",
        )
    )
    if not links:
        if result.fallback_reason is FallbackReasonCode.NONE:
            result.fallback_reason = FallbackReasonCode.NO_VISIBLE_CHILD
        return

    for link in links:
        result.proposed_leaves.append(_leaf_from_link(link))


def _leaf_from_link(link: NarrativeMemorySourceLink) -> ProposedLeaf:
    return ProposedLeaf(
        hierarchy_build_id=link.hierarchy_build_id,
        evidence_node_id=link.evidence_node_id,
        chapter_id=link.chapter_id,
        chapter_number=link.chapter_number,
        source_start=link.source_start,
        source_end=link.source_end,
        content_hash=link.content_hash,
        source_snapshot_hash=link.source_snapshot_hash,
        link_id=link.id,
        claim_id=link.claim_id,
        origin="memory",
    )


async def _descend_from_parents(
    session: AsyncSession,
    scope: RetrievalScope,
    result: DescentResult,
    parents: list[NarrativeMemoryNode],
    *,
    child_kinds: tuple[StartLevel, ...],
    parent_level: str,
    depth: int,
) -> list[NarrativeMemoryNode]:
    if not parents:
        return []
    if depth > scope.budgets.max_depth:
        result.fallback_reason = FallbackReasonCode.BUDGET_EXHAUSTED
        return []

    parent_ids = [p.id for p in parents]
    children = await load_child_nodes_via_edges(
        session,
        scope,
        parent_node_ids=parent_ids,
        child_kinds=child_kinds,
    )
    # Cap fanout deterministically
    omitted = max(0, len(children) - scope.budgets.max_fanout)
    children = children[: scope.budgets.max_fanout]
    result.omitted_after_budget += omitted
    result.traversal.append(
        _step(
            level=child_kinds[0].value if child_kinds else "child",
            candidate_key=f"from:{parent_level}",
            parent_key=parent_level,
            relation="contains",
            visible_count=len(children),
            omitted=omitted,
            outcome="admitted" if children else "no_visible_child",
        )
    )
    return children


async def _raw_fallback_leaves(
    session: AsyncSession,
    scope: RetrievalScope,
    result: DescentResult,
) -> None:
    """Query Phase 07 evidence leaves under the same frozen build and cutoff."""

    stmt = (
        select(ChunkHierarchyNode)
        .where(
            ChunkHierarchyNode.build_id == scope.hierarchy_build_id,
            ChunkHierarchyNode.novel_id == scope.novel_id,
            ChunkHierarchyNode.level == "evidence",
            ChunkHierarchyNode.chapter_number <= scope.through_chapter,
        )
        .order_by(
            ChunkHierarchyNode.chapter_number.asc(),
            ChunkHierarchyNode.source_start.asc(),
            ChunkHierarchyNode.node_id.asc(),
        )
        .limit(scope.budgets.max_leaves)
    )
    rows = list((await session.scalars(stmt)).all())
    result.traversal.append(
        _step(
            level="raw_evidence",
            candidate_key=f"build:{scope.hierarchy_build_id}",
            parent_key="fallback",
            relation="raw_fallback",
            visible_count=len(rows),
            omitted=0,
            outcome="admitted" if rows else "no_answer",
        )
    )
    if not rows:
        result.fallback_reason = FallbackReasonCode.NO_ANSWER
        result.source_status = SafeSourceStatus.ABSENT
        return

    result.fallback_reason = FallbackReasonCode.RAW_FALLBACK
    result.source_status = SafeSourceStatus.FALLBACK
    for row in rows:
        result.proposed_leaves.append(
            ProposedLeaf(
                hierarchy_build_id=row.build_id,
                evidence_node_id=row.node_id,
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                source_snapshot_hash=scope.source_snapshot_hash,
                link_id=None,
                claim_id=None,
                origin="raw_fallback",
            )
        )


async def run_descent(
    session: AsyncSession,
    scope: RetrievalScope,
    route: RouteDecision,
) -> DescentResult:
    """Execute route-specific bounded descent under one immutable scope."""

    result = DescentResult()
    mode = route.mode

    if mode is RouteMode.LOCAL:
        nodes, omitted = await _nodes_for_kinds(
            session, scope, (StartLevel.CHAPTER_STATE,)
        )
        result.omitted_after_budget += omitted
        result.traversal.append(
            _step(
                level="chapter_state",
                candidate_key="start:local",
                parent_key=None,
                relation="start",
                visible_count=len(nodes),
                omitted=omitted,
                outcome="admitted" if nodes else "upper_absent",
            )
        )
        if not nodes:
            result.fallback_reason = FallbackReasonCode.UPPER_ABSENT
            await _raw_fallback_leaves(session, scope, result)
            return result
        await _expand_claims_and_links(
            session, scope, result, nodes, parent_key="local", depth=1
        )

    elif mode is RouteMode.ARC:
        parents, omitted = await _nodes_for_kinds(
            session, scope, (StartLevel.STORY_ARC, StartLevel.VOLUME)
        )
        result.omitted_after_budget += omitted
        result.traversal.append(
            _step(
                level="story_arc",
                candidate_key="start:arc",
                parent_key=None,
                relation="start",
                visible_count=len(parents),
                omitted=omitted,
                outcome="admitted" if parents else "upper_absent",
            )
        )
        if not parents:
            result.fallback_reason = FallbackReasonCode.UPPER_ABSENT
            # collapse to chapter_state
            chapters, ch_om = await _nodes_for_kinds(
                session, scope, (StartLevel.CHAPTER_STATE,)
            )
            result.omitted_after_budget += ch_om
            result.traversal.append(
                _step(
                    level="chapter_state",
                    candidate_key="collapse:arc",
                    parent_key="arc",
                    relation="collapse",
                    visible_count=len(chapters),
                    omitted=ch_om,
                    outcome="admitted" if chapters else "upper_absent",
                )
            )
            if chapters:
                await _expand_claims_and_links(
                    session, scope, result, chapters, parent_key="collapse", depth=1
                )
            else:
                await _raw_fallback_leaves(session, scope, result)
            return result

        children = await _descend_from_parents(
            session,
            scope,
            result,
            parents,
            child_kinds=(StartLevel.CHAPTER_STATE,),
            parent_level="arc",
            depth=1,
        )
        if not children:
            # partial upper: try chapter_state directly
            if result.fallback_reason is FallbackReasonCode.NONE:
                result.fallback_reason = FallbackReasonCode.UPPER_PARTIAL
            chapters, ch_om = await _nodes_for_kinds(
                session, scope, (StartLevel.CHAPTER_STATE,)
            )
            result.omitted_after_budget += ch_om
            if chapters:
                await _expand_claims_and_links(
                    session, scope, result, chapters, parent_key="arc-partial", depth=2
                )
            else:
                await _raw_fallback_leaves(session, scope, result)
            return result
        await _expand_claims_and_links(
            session, scope, result, children, parent_key="arc->chapter", depth=2
        )

    elif mode is RouteMode.GLOBAL:
        globals_, omitted = await _nodes_for_kinds(
            session, scope, (StartLevel.GLOBAL_STORY,)
        )
        result.omitted_after_budget += omitted
        result.traversal.append(
            _step(
                level="global_story",
                candidate_key="start:global",
                parent_key=None,
                relation="start",
                visible_count=len(globals_),
                omitted=omitted,
                outcome="admitted" if globals_ else "upper_absent",
            )
        )
        if not globals_:
            result.fallback_reason = FallbackReasonCode.UPPER_ABSENT
            # collapse arc then local
            arcs, arc_om = await _nodes_for_kinds(
                session, scope, (StartLevel.STORY_ARC, StartLevel.VOLUME)
            )
            result.omitted_after_budget += arc_om
            if arcs:
                children = await _descend_from_parents(
                    session,
                    scope,
                    result,
                    arcs,
                    child_kinds=(StartLevel.CHAPTER_STATE,),
                    parent_level="collapse-arc",
                    depth=1,
                )
                if not children:
                    children, _ = await _nodes_for_kinds(
                        session, scope, (StartLevel.CHAPTER_STATE,)
                    )
                if children:
                    await _expand_claims_and_links(
                        session,
                        scope,
                        result,
                        children,
                        parent_key="g-collapse",
                        depth=2,
                    )
                else:
                    await _raw_fallback_leaves(session, scope, result)
            else:
                chapters, ch_om = await _nodes_for_kinds(
                    session, scope, (StartLevel.CHAPTER_STATE,)
                )
                result.omitted_after_budget += ch_om
                if chapters:
                    await _expand_claims_and_links(
                        session, scope, result, chapters, parent_key="g-local", depth=1
                    )
                else:
                    await _raw_fallback_leaves(session, scope, result)
            return result

        arcs = await _descend_from_parents(
            session,
            scope,
            result,
            globals_,
            child_kinds=(StartLevel.STORY_ARC, StartLevel.VOLUME),
            parent_level="global",
            depth=1,
        )
        if not arcs:
            result.fallback_reason = FallbackReasonCode.UPPER_PARTIAL
            chapters, ch_om = await _nodes_for_kinds(
                session, scope, (StartLevel.CHAPTER_STATE,)
            )
            result.omitted_after_budget += ch_om
            if chapters:
                await _expand_claims_and_links(
                    session, scope, result, chapters, parent_key="g-partial", depth=2
                )
            else:
                await _raw_fallback_leaves(session, scope, result)
            return result
        chapters = await _descend_from_parents(
            session,
            scope,
            result,
            arcs,
            child_kinds=(StartLevel.CHAPTER_STATE,),
            parent_level="global->arc",
            depth=2,
        )
        if not chapters:
            result.fallback_reason = FallbackReasonCode.NO_VISIBLE_CHILD
            await _raw_fallback_leaves(session, scope, result)
            return result
        await _expand_claims_and_links(
            session, scope, result, chapters, parent_key="global->ch", depth=3
        )

    else:  # MIXED — bounded union of local + upper
        local_nodes, loc_om = await _nodes_for_kinds(
            session, scope, (StartLevel.CHAPTER_STATE,)
        )
        upper_nodes, up_om = await _nodes_for_kinds(
            session, scope, (StartLevel.STORY_ARC, StartLevel.VOLUME)
        )
        result.omitted_after_budget += loc_om + up_om
        result.traversal.append(
            _step(
                level="mixed",
                candidate_key="start:mixed",
                parent_key=None,
                relation="start",
                visible_count=len(local_nodes) + len(upper_nodes),
                omitted=loc_om + up_om,
                outcome="admitted" if (local_nodes or upper_nodes) else "upper_absent",
            )
        )
        # Expand upper into chapter children and union with local
        from_upper: list[NarrativeMemoryNode] = []
        if upper_nodes:
            from_upper = await _descend_from_parents(
                session,
                scope,
                result,
                upper_nodes,
                child_kinds=(StartLevel.CHAPTER_STATE,),
                parent_level="mixed-upper",
                depth=1,
            )
        by_id: dict[int, NarrativeMemoryNode] = {n.id: n for n in local_nodes}
        for n in from_upper:
            by_id[n.id] = n
        union = sorted(by_id.values(), key=lambda n: (n.chapter_start, n.id))
        union = union[: scope.budgets.max_nodes]
        if not union:
            result.fallback_reason = FallbackReasonCode.UPPER_ABSENT
            await _raw_fallback_leaves(session, scope, result)
            return result
        await _expand_claims_and_links(
            session, scope, result, union, parent_key="mixed", depth=2
        )

    # If memory path produced no leaves, raw fallback without widening scope.
    if not result.proposed_leaves:
        if result.fallback_reason is FallbackReasonCode.NONE:
            result.fallback_reason = FallbackReasonCode.INVALID_LEAF
        await _raw_fallback_leaves(session, scope, result)

    # Apply leaf budget after dedupe
    deduped = result.deduped_leaves()
    if len(deduped) > scope.budgets.max_leaves:
        result.omitted_after_budget += len(deduped) - scope.budgets.max_leaves
        deduped = deduped[: scope.budgets.max_leaves]
        if result.fallback_reason is FallbackReasonCode.NONE:
            result.fallback_reason = FallbackReasonCode.BUDGET_EXHAUSTED
    result.proposed_leaves = deduped
    return result
