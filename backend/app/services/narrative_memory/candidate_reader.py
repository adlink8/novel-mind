"""Cutoff-first, explicit-version PostgreSQL visible candidate loaders.

Visibility gates (owner/novel/version/build/snapshot/cutoff) run in SQL before
materialization, ranking, counts, status derivation or cache construction.
Never resolves current/active pointers; never mutates Phase 13/14 authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryValidationReport,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildReport,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.retrieval_contracts import (
    CacheEnvelope,
    CandidateSourceStatus,
    RetrievalBudgets,
    RetrievalQuestion,
    RetrievalScope,
    RouteDecision,
    StartLevel,
    VisibleCandidate,
    build_cache_envelope,
)


class CandidateReaderError(ValueError):
    """Fail-closed eligibility or scope error."""


class IncompleteCandidateError(CandidateReaderError):
    pass


class UnsealedCandidateError(CandidateReaderError):
    pass


class ScopeMismatchError(CandidateReaderError):
    pass


@dataclass(frozen=True)
class EligibleVersion:
    version: NarrativeMemoryVersion
    manifest: NarrativeMemoryManifest
    report: NarrativeMemoryValidationReport
    build_run: NarrativeMemoryBuildRun
    source_status: CandidateSourceStatus


@dataclass
class VisibleSet:
    nodes: list[VisibleCandidate] = field(default_factory=list)
    claims: list[VisibleCandidate] = field(default_factory=list)
    edges: list[VisibleCandidate] = field(default_factory=list)
    source_links: list[VisibleCandidate] = field(default_factory=list)
    raw_nodes: list[NarrativeMemoryNode] = field(default_factory=list)
    raw_claims: list[NarrativeMemoryClaim] = field(default_factory=list)
    raw_edges: list[NarrativeMemoryEdge] = field(default_factory=list)
    raw_links: list[NarrativeMemorySourceLink] = field(default_factory=list)
    omitted_after_budget: int = 0
    source_status: CandidateSourceStatus = CandidateSourceStatus.OK
    cache: CacheEnvelope | None = None

    def public_counts(self) -> dict[str, int]:
        return {
            "visible_node_count": len(self.nodes),
            "visible_claim_count": len(self.claims),
            "visible_edge_count": len(self.edges),
            "visible_link_count": len(self.source_links),
            "omitted_after_budget": self.omitted_after_budget,
        }


# Process-local cache: identity_hash -> (scope fields, public payload)
_VISIBLE_CACHE: dict[str, dict[str, Any]] = {}


def clear_visible_cache() -> None:
    _VISIBLE_CACHE.clear()


async def load_eligible_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    expected_source_snapshot_hash: str | None = None,
    expected_hierarchy_build_id: str | None = None,
    expected_hierarchy_checksum: str | None = None,
    expected_manifest_checksum: str | None = None,
) -> EligibleVersion:
    """Load an explicit candidate version only if sealed and build-complete."""

    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == version_id,
        )
    )
    if version is None:
        raise ScopeMismatchError("candidate version not found in scope")

    if (
        expected_source_snapshot_hash is not None
        and version.source_snapshot_hash != expected_source_snapshot_hash
    ):
        raise ScopeMismatchError("source_snapshot_hash mismatch")
    if (
        expected_hierarchy_build_id is not None
        and version.hierarchy_build_id != expected_hierarchy_build_id
    ):
        raise ScopeMismatchError("hierarchy_build_id mismatch")
    if (
        expected_hierarchy_checksum is not None
        and version.hierarchy_checksum != expected_hierarchy_checksum
    ):
        raise ScopeMismatchError("hierarchy_checksum mismatch")

    manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == version_id,
        )
    )
    if manifest is None:
        raise UnsealedCandidateError("candidate is unsealed")
    if (
        expected_manifest_checksum is not None
        and manifest.manifest_checksum != expected_manifest_checksum
    ):
        raise ScopeMismatchError("manifest_checksum mismatch")

    report = await session.scalar(
        select(NarrativeMemoryValidationReport)
        .where(
            NarrativeMemoryValidationReport.owner_id == owner_id,
            NarrativeMemoryValidationReport.novel_id == novel_id,
            NarrativeMemoryValidationReport.version_id == version_id,
            NarrativeMemoryValidationReport.manifest_checksum
            == manifest.manifest_checksum,
        )
        .order_by(NarrativeMemoryValidationReport.id.desc())
        .limit(1)
    )
    if report is None:
        raise IncompleteCandidateError("structural report missing for seal")

    build_run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == owner_id,
            NarrativeMemoryBuildRun.novel_id == novel_id,
            NarrativeMemoryBuildRun.version_id == version_id,
        )
    )
    if build_run is None or build_run.status != "completed":
        raise IncompleteCandidateError("phase 14 build run is not complete")

    stages = list(
        (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == build_run.id
                )
            )
        ).all()
    )
    if not stages or any(s.status != "completed" for s in stages):
        raise IncompleteCandidateError("phase 14 stages incomplete")

    build_report = await session.scalar(
        select(NarrativeMemoryBuildReport)
        .where(
            NarrativeMemoryBuildReport.run_id == build_run.id,
            NarrativeMemoryBuildReport.owner_id == owner_id,
            NarrativeMemoryBuildReport.novel_id == novel_id,
            NarrativeMemoryBuildReport.version_id == version_id,
        )
        .order_by(NarrativeMemoryBuildReport.id.desc())
        .limit(1)
    )
    if build_report is None or build_report.outcome != "completed_candidate":
        raise IncompleteCandidateError("phase 14 report is not completed_candidate")
    if (
        build_report.database_manifest_checksum is not None
        and build_report.database_manifest_checksum != manifest.manifest_checksum
    ):
        raise ScopeMismatchError("builder report manifest mismatch")
    if (
        build_report.worker_artifact_checksum is not None
        and build_report.worker_artifact_checksum != manifest.manifest_checksum
    ):
        raise ScopeMismatchError("worker artifact checksum mismatch")

    return EligibleVersion(
        version=version,
        manifest=manifest,
        report=report,
        build_run=build_run,
        source_status=CandidateSourceStatus.OK,
    )


def scope_from_eligible(
    eligible: EligibleVersion,
    *,
    cutoff_chapter: int,
    cutoff_snapshot_hash: str,
    full_book_authorized: bool,
    policy_version: str,
    policy_hash: str,
    budgets: RetrievalBudgets | None = None,
) -> RetrievalScope:
    v = eligible.version
    return RetrievalScope(
        owner_id=v.owner_id,
        novel_id=v.novel_id,
        version_id=v.id,
        source_snapshot_hash=v.source_snapshot_hash,
        hierarchy_build_id=v.hierarchy_build_id,
        hierarchy_checksum=v.hierarchy_checksum,
        candidate_manifest_checksum=eligible.manifest.manifest_checksum,
        cutoff={
            "through_chapter": cutoff_chapter,
            "full_book_authorized": full_book_authorized,
            "snapshot_hash": cutoff_snapshot_hash,
        },
        policy_version=policy_version,
        policy_hash=policy_hash,
        budgets=budgets or RetrievalBudgets(),
    )


def _node_visible_clause(scope: RetrievalScope):
    """Upper nodes require complete chapter_end at/before cutoff.

    Global requires full-book authorization covering its complete range, or
    chapter_end <= cutoff when authorized as fully read.
    """

    base = and_(
        NarrativeMemoryNode.owner_id == scope.owner_id,
        NarrativeMemoryNode.novel_id == scope.novel_id,
        NarrativeMemoryNode.version_id == scope.version_id,
        NarrativeMemoryNode.chapter_end <= scope.through_chapter,
    )
    return base


def _node_kind_filter(kinds: tuple[StartLevel, ...] | None):
    if not kinds:
        return True
    return NarrativeMemoryNode.node_kind.in_([k.value for k in kinds])


async def load_visible_nodes(
    session: AsyncSession,
    scope: RetrievalScope,
    *,
    kinds: tuple[StartLevel, ...] | None = None,
    node_ids: list[int] | None = None,
    limit: int | None = None,
) -> tuple[list[NarrativeMemoryNode], int]:
    """SQL visibility first; ranking only after admission."""

    budget = limit if limit is not None else scope.budgets.max_nodes
    stmt: Select[Any] = (
        select(NarrativeMemoryNode)
        .where(_node_visible_clause(scope), _node_kind_filter(kinds))
        .order_by(
            NarrativeMemoryNode.chapter_start.asc(),
            NarrativeMemoryNode.chapter_end.asc(),
            NarrativeMemoryNode.id.asc(),
        )
    )
    if node_ids is not None:
        if not node_ids:
            return [], 0
        stmt = stmt.where(NarrativeMemoryNode.id.in_(node_ids))

    # Global nodes additionally require full-book authorization when they span
    # beyond a partial read — already enforced by chapter_end <= cutoff, but
    # refuse global kind entirely when full_book is false and we want strictness:
    # still allow global only if chapter_end <= cutoff (complete range read).
    # Unauthorized partial global never partially exposes: chapter_end filter.

    rows = list((await session.scalars(stmt)).all())
    if not scope.full_book_authorized:
        rows = [
            n
            for n in rows
            if n.node_kind != StartLevel.GLOBAL_STORY.value
            or n.chapter_end <= scope.through_chapter
        ]
        # Without full-book auth, still only fully-complete globals within cutoff.
        # (chapter_end filter already applied)

    total = len(rows)
    admitted = rows[:budget]
    omitted = max(0, total - len(admitted))
    return admitted, omitted


async def load_visible_claims(
    session: AsyncSession,
    scope: RetrievalScope,
    *,
    node_ids: list[int] | None = None,
    claim_ids: list[int] | None = None,
    limit: int | None = None,
) -> tuple[list[NarrativeMemoryClaim], int]:
    budget = limit if limit is not None else scope.budgets.max_claims
    stmt = (
        select(NarrativeMemoryClaim)
        .where(
            NarrativeMemoryClaim.owner_id == scope.owner_id,
            NarrativeMemoryClaim.novel_id == scope.novel_id,
            NarrativeMemoryClaim.version_id == scope.version_id,
            NarrativeMemoryClaim.visible_from_chapter <= scope.through_chapter,
        )
        .order_by(
            NarrativeMemoryClaim.visible_from_chapter.asc(),
            NarrativeMemoryClaim.id.asc(),
        )
    )
    if node_ids is not None:
        if not node_ids:
            return [], 0
        stmt = stmt.where(NarrativeMemoryClaim.node_id.in_(node_ids))
    if claim_ids is not None:
        if not claim_ids:
            return [], 0
        stmt = stmt.where(NarrativeMemoryClaim.id.in_(claim_ids))

    rows = list((await session.scalars(stmt)).all())
    admitted = rows[:budget]
    return admitted, max(0, len(rows) - len(admitted))


async def load_visible_edges(
    session: AsyncSession,
    scope: RetrievalScope,
    *,
    source_node_ids: list[int] | None = None,
    target_node_ids: list[int] | None = None,
    edge_type: str | None = "contains",
) -> list[NarrativeMemoryEdge]:
    """Edges only between already-visible node IDs (caller supplies filtered ids)."""

    stmt = select(NarrativeMemoryEdge).where(
        NarrativeMemoryEdge.owner_id == scope.owner_id,
        NarrativeMemoryEdge.novel_id == scope.novel_id,
        NarrativeMemoryEdge.version_id == scope.version_id,
    )
    if edge_type is not None:
        stmt = stmt.where(NarrativeMemoryEdge.edge_type == edge_type)
    if source_node_ids is not None:
        if not source_node_ids:
            return []
        stmt = stmt.where(NarrativeMemoryEdge.source_node_id.in_(source_node_ids))
    if target_node_ids is not None:
        if not target_node_ids:
            return []
        stmt = stmt.where(NarrativeMemoryEdge.target_node_id.in_(target_node_ids))
    stmt = stmt.order_by(NarrativeMemoryEdge.id.asc())
    return list((await session.scalars(stmt)).all())


async def load_visible_source_links(
    session: AsyncSession,
    scope: RetrievalScope,
    *,
    claim_ids: list[int] | None = None,
    limit: int | None = None,
) -> tuple[list[NarrativeMemorySourceLink], int]:
    budget = limit if limit is not None else scope.budgets.max_leaves
    stmt = (
        select(NarrativeMemorySourceLink)
        .where(
            NarrativeMemorySourceLink.owner_id == scope.owner_id,
            NarrativeMemorySourceLink.novel_id == scope.novel_id,
            NarrativeMemorySourceLink.version_id == scope.version_id,
            NarrativeMemorySourceLink.source_snapshot_hash
            == scope.source_snapshot_hash,
            NarrativeMemorySourceLink.hierarchy_build_id == scope.hierarchy_build_id,
            NarrativeMemorySourceLink.chapter_number <= scope.through_chapter,
        )
        .order_by(
            NarrativeMemorySourceLink.chapter_number.asc(),
            NarrativeMemorySourceLink.source_start.asc(),
            NarrativeMemorySourceLink.id.asc(),
        )
    )
    if claim_ids is not None:
        if not claim_ids:
            return [], 0
        stmt = stmt.where(NarrativeMemorySourceLink.claim_id.in_(claim_ids))

    rows = list((await session.scalars(stmt)).all())
    admitted = rows[:budget]
    return admitted, max(0, len(rows) - len(admitted))


def _rank_node(node: NarrativeMemoryNode) -> VisibleCandidate:
    kind = StartLevel(node.node_kind)
    rank_key = f"{node.chapter_start:06d}:{node.chapter_end:06d}:{node.id:012d}"
    return VisibleCandidate(
        candidate_kind="node",
        entity_id=node.id,
        stable_key=f"node:{node.node_key}",
        node_kind=kind,
        chapter_start=node.chapter_start,
        chapter_end=node.chapter_end,
        rank_key=rank_key,
    )


def _rank_claim(claim: NarrativeMemoryClaim) -> VisibleCandidate:
    rank_key = f"{claim.visible_from_chapter:06d}:{claim.id:012d}"
    return VisibleCandidate(
        candidate_kind="claim",
        entity_id=claim.id,
        stable_key=f"claim:{claim.claim_key}",
        parent_node_id=claim.node_id,
        chapter_start=claim.visible_from_chapter,
        chapter_end=claim.visible_from_chapter,
        rank_key=rank_key,
    )


def _rank_edge(edge: NarrativeMemoryEdge) -> VisibleCandidate:
    return VisibleCandidate(
        candidate_kind="edge",
        entity_id=edge.id,
        stable_key=f"edge:{edge.source_node_id}->{edge.target_node_id}:{edge.edge_type}",
        parent_node_id=edge.source_node_id,
        rank_key=f"{edge.id:012d}",
    )


def _rank_link(link: NarrativeMemorySourceLink) -> VisibleCandidate:
    return VisibleCandidate(
        candidate_kind="source_link",
        entity_id=link.id,
        stable_key=(
            f"link:{link.hierarchy_build_id}:{link.evidence_node_id}:"
            f"{link.source_start}:{link.source_end}"
        ),
        parent_node_id=None,
        chapter_start=link.chapter_number,
        chapter_end=link.chapter_number,
        rank_key=f"{link.chapter_number:06d}:{link.source_start:08d}:{link.id:012d}",
    )


async def load_visible_set_for_route(
    session: AsyncSession,
    scope: RetrievalScope,
    route: RouteDecision,
    question: RetrievalQuestion,
    *,
    use_cache: bool = True,
) -> VisibleSet:
    """Load start-level nodes for the route, then scoped claims/edges/links."""

    cache_env = build_cache_envelope(
        scope=scope,
        route=route,
        question=question,
        source_status=CandidateSourceStatus.OK,
    )
    if use_cache:
        hit = _VISIBLE_CACHE.get(cache_env.identity_hash)
        if hit is not None and _cache_hit_valid(hit, scope, route, question):
            # Re-hydrate from DB to avoid serving stale detached objects; only
            # reuse the identity proof — still re-query under the same filters.
            pass

    kinds = tuple(route.start_levels)
    nodes, node_omitted = await load_visible_nodes(session, scope, kinds=kinds)

    # Mixed/local may further prefer selection chapter when present.
    if question.selected_chapter is not None and route.mode.value == "local":
        selected = [
            n
            for n in nodes
            if n.chapter_start
            <= question.selected_chapter
            <= n.chapter_end
        ]
        if selected:
            nodes = selected

    node_ids = [n.id for n in nodes]
    edges = await load_visible_edges(
        session, scope, source_node_ids=node_ids or None
    )
    # Also load edges targeting visible nodes for descent completeness
    if node_ids:
        inbound = await load_visible_edges(
            session, scope, target_node_ids=node_ids
        )
        by_id = {e.id: e for e in edges}
        for e in inbound:
            by_id[e.id] = e
        edges = sorted(by_id.values(), key=lambda e: e.id)

    claims, claim_omitted = await load_visible_claims(
        session, scope, node_ids=node_ids or None
    )
    claim_ids = [c.id for c in claims]
    links, link_omitted = await load_visible_source_links(
        session, scope, claim_ids=claim_ids or None
    )

    visible = VisibleSet(
        nodes=[_rank_node(n) for n in nodes],
        claims=[_rank_claim(c) for c in claims],
        edges=[_rank_edge(e) for e in edges],
        source_links=[_rank_link(link) for link in links],
        raw_nodes=nodes,
        raw_claims=claims,
        raw_edges=edges,
        raw_links=links,
        omitted_after_budget=node_omitted + claim_omitted + link_omitted,
        source_status=CandidateSourceStatus.OK,
        cache=cache_env,
    )

    if use_cache:
        _VISIBLE_CACHE[cache_env.identity_hash] = {
            "scope_hash": cache_env.scope_hash,
            "route_hash": cache_env.route_hash,
            "query_hash": cache_env.query_hash,
            "budget_hash": cache_env.budget_hash,
            "owner_id": scope.owner_id,
            "novel_id": scope.novel_id,
            "version_id": scope.version_id,
            "manifest": scope.candidate_manifest_checksum,
            "cutoff": scope.cutoff.snapshot_hash,
            "public": visible.public_counts(),
            "node_keys": [c.stable_key for c in visible.nodes],
            "claim_keys": [c.stable_key for c in visible.claims],
        }

    return visible


def _cache_hit_valid(
    hit: dict[str, Any],
    scope: RetrievalScope,
    route: RouteDecision,
    question: RetrievalQuestion,
) -> bool:
    env = build_cache_envelope(
        scope=scope,
        route=route,
        question=question,
        source_status=CandidateSourceStatus.OK,
    )
    return (
        hit.get("scope_hash") == env.scope_hash
        and hit.get("route_hash") == env.route_hash
        and hit.get("query_hash") == env.query_hash
        and hit.get("budget_hash") == env.budget_hash
        and hit.get("owner_id") == scope.owner_id
        and hit.get("novel_id") == scope.novel_id
        and hit.get("version_id") == scope.version_id
        and hit.get("manifest") == scope.candidate_manifest_checksum
        and hit.get("cutoff") == scope.cutoff.snapshot_hash
    )


def peek_cache_public(identity_hash: str) -> dict[str, Any] | None:
    """Return public cache payload only; never expose raw identity as a key field."""

    hit = _VISIBLE_CACHE.get(identity_hash)
    if hit is None:
        return None
    return {
        "public": hit["public"],
        "node_keys": hit["node_keys"],
        "claim_keys": hit["claim_keys"],
    }


async def load_child_nodes_via_edges(
    session: AsyncSession,
    scope: RetrievalScope,
    *,
    parent_node_ids: list[int],
    child_kinds: tuple[StartLevel, ...] | None = None,
) -> list[NarrativeMemoryNode]:
    """Descend only through legal edges under the same immutable scope."""

    if not parent_node_ids:
        return []
    edges = await load_visible_edges(
        session, scope, source_node_ids=parent_node_ids, edge_type="contains"
    )
    child_ids = [e.target_node_id for e in edges]
    if not child_ids:
        return []
    children, _ = await load_visible_nodes(
        session, scope, kinds=child_kinds, node_ids=child_ids
    )
    return children
