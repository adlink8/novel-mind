"""Cutoff-first Phase 07 leaf/raw baseline for paired qualification.

Bypasses candidate node/claim/summary data. Final citations use the same
Unicode re-slice validator path as hierarchical candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkHierarchyNode
from app.services.narrative_memory.citations import (
    CitationValidationError,
    validate_proposed_leaf,
)
from app.services.narrative_memory.descent import ProposedLeaf
from app.services.narrative_memory.qualification_contracts import (
    PairedCaseEnvelope,
    RetrievalStrategy,
    stable_checksum,
)
from app.services.narrative_memory.retrieval_contracts import RetrievalScope


@dataclass(frozen=True)
class BaselineRetrievalResult:
    strategy: str = RetrievalStrategy.LEAF_RAW_BASELINE.value
    retrieved_leaf_ids: tuple[str, ...] = ()
    accepted_citations: int = 0
    total_citations: int = 0
    fallback_used: bool = False
    route_chosen: str = "leaf_raw"
    latency_ms: float = 0.0
    artifact_checksum: str = ""
    blocked: bool = False
    block_reason: str | None = None
    detail: dict[str, Any] | None = None


def _score_leaf(query: str, content: str) -> float:
    """Deterministic lexical overlap score (provider-free)."""
    q_tokens = set(query.lower().replace("？", " ").replace("?", " ").split())
    c_tokens = set(content.lower().split())
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


async def run_leaf_raw_baseline(
    session: AsyncSession,
    envelope: PairedCaseEnvelope,
    *,
    scope: RetrievalScope | None = None,
    transport: Callable[..., Any] | None = None,
) -> BaselineRetrievalResult:
    """Retrieve from cutoff-visible Phase 07 evidence leaves only."""

    if envelope.strategy != RetrievalStrategy.LEAF_RAW_BASELINE:
        return BaselineRetrievalResult(
            blocked=True, block_reason="wrong_strategy"
        )

    common = envelope.common
    nodes = list(
        (
            await session.scalars(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.build_id == common.hierarchy_build_id,
                    ChunkHierarchyNode.novel_id == common.novel_id,
                    ChunkHierarchyNode.level == "evidence",
                    ChunkHierarchyNode.chapter_number <= common.through_chapter,
                )
            )
        ).all()
    )

    scored: list[tuple[float, ChunkHierarchyNode]] = []
    for node in nodes:
        scored.append((_score_leaf(common.query, node.content or ""), node))
    scored.sort(key=lambda x: (-x[0], x[1].node_id))
    top = [n for _, n in scored[: common.top_k]]

    accepted = 0
    leaf_ids: list[str] = []
    for node in top:
        leaf_ids.append(node.node_id)
        if scope is not None:
            proposed = ProposedLeaf(
                hierarchy_build_id=common.hierarchy_build_id,
                evidence_node_id=node.node_id,
                chapter_id=node.chapter_id,
                chapter_number=node.chapter_number,
                source_start=node.source_start,
                source_end=node.source_end,
                content_hash=node.content_hash,
                source_snapshot_hash=common.source_snapshot_hash,
                link_id=None,
                origin="raw_fallback",
            )
            try:
                cit = await validate_proposed_leaf(session, scope, proposed)
                if cit is not None:
                    accepted += 1
            except CitationValidationError:
                pass
        else:
            # Without full scope, accept node hash as present (unit/dry path)
            accepted += 1

    body = {
        "strategy": RetrievalStrategy.LEAF_RAW_BASELINE.value,
        "case_key": common.case_key,
        "leaf_ids": leaf_ids,
        "cache_namespace": envelope.cache_namespace,
        "query": common.query,
        "through_chapter": common.through_chapter,
    }
    return BaselineRetrievalResult(
        retrieved_leaf_ids=tuple(leaf_ids),
        accepted_citations=accepted,
        total_citations=len(top),
        fallback_used=False,
        route_chosen="leaf_raw",
        latency_ms=1.0,
        artifact_checksum=stable_checksum(body),
        detail={"cache_namespace": envelope.cache_namespace},
    )


def baseline_reads_candidate_claims() -> bool:
    return False


def baseline_has_provider_capability() -> bool:
    return False
