"""Validated-parent-only Global Story package construction."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    build_global_candidate,
)
from app.services.narrative_memory.contracts import CandidatePackage, NodeKind


async def load_validated_parents(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    expected_parent_keys: Sequence[str] | None = None,
) -> tuple[
    list[NarrativeMemoryNode],
    list[NarrativeMemoryClaim],
    list[NarrativeMemorySourceLink],
]:
    parents = (
        await session.scalars(
            select(NarrativeMemoryNode)
            .where(
                NarrativeMemoryNode.owner_id == owner_id,
                NarrativeMemoryNode.novel_id == novel_id,
                NarrativeMemoryNode.version_id == version_id,
                NarrativeMemoryNode.node_kind.in_(
                    [NodeKind.STORY_ARC.value, NodeKind.VOLUME.value]
                ),
            )
            .order_by(NarrativeMemoryNode.chapter_start, NarrativeMemoryNode.node_key)
        )
    ).all()
    if not parents:
        raise PackageBuildError("no middle-level parents for global")
    if expected_parent_keys is not None:
        keys = {p.node_key for p in parents}
        missing = set(expected_parent_keys) - keys
        if missing:
            raise PackageBuildError(f"missing parent keys: {sorted(missing)}")
    parent_ids = [p.id for p in parents]
    claims = (
        await session.scalars(
            select(NarrativeMemoryClaim)
            .where(
                NarrativeMemoryClaim.owner_id == owner_id,
                NarrativeMemoryClaim.novel_id == novel_id,
                NarrativeMemoryClaim.version_id == version_id,
                NarrativeMemoryClaim.node_id.in_(parent_ids),
            )
            .order_by(NarrativeMemoryClaim.claim_key)
        )
    ).all()
    claim_ids = [c.id for c in claims]
    links = (
        await session.scalars(
            select(NarrativeMemorySourceLink)
            .where(
                NarrativeMemorySourceLink.owner_id == owner_id,
                NarrativeMemorySourceLink.novel_id == novel_id,
                NarrativeMemorySourceLink.version_id == version_id,
                NarrativeMemorySourceLink.claim_id.in_(claim_ids),
            )
            .order_by(NarrativeMemorySourceLink.id)
        )
    ).all()
    return list(parents), list(claims), list(links)


def build_global_package_from_parents(
    *,
    version: NarrativeMemoryVersion,
    parents: Sequence[NarrativeMemoryNode],
    claims: Sequence[NarrativeMemoryClaim],
    links: Sequence[NarrativeMemorySourceLink],
    model_claims: Sequence[dict[str, Any]] | None = None,
) -> CandidatePackage:
    if not parents:
        raise PackageBuildError("global requires parents")
    chapter_start = min(p.chapter_start for p in parents)
    chapter_end = max(p.chapter_end for p in parents)
    return build_global_candidate(
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        parent_nodes=parents,
        parent_claims=claims,
        parent_links=links,
        model_claims=model_claims,
    )
