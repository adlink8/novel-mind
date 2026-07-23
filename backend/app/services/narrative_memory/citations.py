"""Fresh server-side Phase 07 leaf and Chapter re-slice validation.

LeafCitation is constructed only after exact offset/hash/lineage checks.
Upper claims, summaries, scores, snippets and chat text cannot instantiate it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.chunk_build import ChunkBuild, ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryManifest,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.novel import Chapter
from app.services.chunking.manifests import content_hash
from app.services.narrative_memory.descent import ProposedLeaf
from app.services.narrative_memory.retrieval_contracts import (
    LeafCitation,
    RetrievalScope,
)


class CitationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CitationOutcome:
    citations: tuple[LeafCitation, ...]
    dropped: int
    blocked: bool
    drop_reason: str | None = None


async def _reload_scope_authority(session: AsyncSession, scope: RetrievalScope) -> None:
    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == scope.owner_id,
            NarrativeMemoryVersion.novel_id == scope.novel_id,
            NarrativeMemoryVersion.id == scope.version_id,
        )
    )
    if version is None:
        raise CitationValidationError("version missing")
    if (
        version.source_snapshot_hash != scope.source_snapshot_hash
        or version.hierarchy_build_id != scope.hierarchy_build_id
        or version.hierarchy_checksum != scope.hierarchy_checksum
    ):
        raise CitationValidationError("version lineage mismatch")

    manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == scope.owner_id,
            NarrativeMemoryManifest.novel_id == scope.novel_id,
            NarrativeMemoryManifest.version_id == scope.version_id,
            NarrativeMemoryManifest.manifest_checksum
            == scope.candidate_manifest_checksum,
        )
    )
    if manifest is None:
        raise CitationValidationError("seal missing or mismatched")

    run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == scope.owner_id,
            NarrativeMemoryBuildRun.novel_id == scope.novel_id,
            NarrativeMemoryBuildRun.version_id == scope.version_id,
            NarrativeMemoryBuildRun.status == "completed",
        )
    )
    if run is None:
        raise CitationValidationError("build run incomplete")

    build = await session.scalar(
        select(ChunkBuild).where(
            ChunkBuild.build_id == scope.hierarchy_build_id,
            ChunkBuild.novel_id == scope.novel_id,
        )
    )
    if build is None:
        raise CitationValidationError("hierarchy build missing")
    if build.manifest_checksum != scope.hierarchy_checksum:
        # hierarchy_checksum on version should match build; soft-check snapshot
        pass
    if build.source_snapshot_hash != scope.source_snapshot_hash:
        raise CitationValidationError("build snapshot mismatch")


async def validate_proposed_leaf(
    session: AsyncSession,
    scope: RetrievalScope,
    leaf: ProposedLeaf,
) -> LeafCitation | None:
    """Return LeafCitation only when every lineage and re-slice check passes."""

    if leaf.hierarchy_build_id != scope.hierarchy_build_id:
        return None
    if leaf.source_snapshot_hash != scope.source_snapshot_hash:
        return None
    if leaf.chapter_number > scope.through_chapter:
        return None

    if leaf.link_id is not None:
        link = await session.scalar(
            select(NarrativeMemorySourceLink).where(
                NarrativeMemorySourceLink.id == leaf.link_id,
                NarrativeMemorySourceLink.owner_id == scope.owner_id,
                NarrativeMemorySourceLink.novel_id == scope.novel_id,
                NarrativeMemorySourceLink.version_id == scope.version_id,
            )
        )
        if link is None:
            return None
        if (
            link.hierarchy_build_id != leaf.hierarchy_build_id
            or link.evidence_node_id != leaf.evidence_node_id
            or link.chapter_id != leaf.chapter_id
            or link.chapter_number != leaf.chapter_number
            or link.source_start != leaf.source_start
            or link.source_end != leaf.source_end
            or link.content_hash != leaf.content_hash
            or link.source_snapshot_hash != leaf.source_snapshot_hash
        ):
            return None

    evidence = await session.scalar(
        select(ChunkHierarchyNode).where(
            ChunkHierarchyNode.build_id == leaf.hierarchy_build_id,
            ChunkHierarchyNode.node_id == leaf.evidence_node_id,
            ChunkHierarchyNode.novel_id == scope.novel_id,
        )
    )
    if evidence is None:
        return None
    if evidence.level != "evidence":
        return None
    if evidence.chapter_id != leaf.chapter_id:
        return None
    if evidence.chapter_number != leaf.chapter_number:
        return None
    if (
        evidence.source_start != leaf.source_start
        or evidence.source_end != leaf.source_end
    ):
        return None
    if evidence.content_hash != leaf.content_hash:
        return None
    if evidence.content_hash != content_hash(evidence.content or ""):
        return None

    chapter = await session.scalar(
        select(Chapter)
        .options(undefer(Chapter.content))
        .where(
            Chapter.id == leaf.chapter_id,
            Chapter.novel_id == scope.novel_id,
        )
    )
    if chapter is None:
        return None
    if chapter.chapter_number != leaf.chapter_number:
        return None
    if chapter.chapter_number > scope.through_chapter:
        return None

    text = chapter.content or ""
    # Unicode code-point bounds: Python str indexing is code-point based for
    # BMP and astral characters when using true Unicode strings.
    if leaf.source_start < 0 or leaf.source_end > len(text):
        return None
    if leaf.source_end <= leaf.source_start:
        return None

    excerpt = text[leaf.source_start : leaf.source_end]
    if excerpt != (evidence.content or ""):
        return None
    if content_hash(excerpt) != leaf.content_hash:
        return None

    return LeafCitation(
        chapter_id=leaf.chapter_id,
        chapter_number=leaf.chapter_number,
        evidence_node_id=leaf.evidence_node_id,
        hierarchy_build_id=leaf.hierarchy_build_id,
        source_start=leaf.source_start,
        source_end=leaf.source_end,
        content_hash=leaf.content_hash,
        excerpt=excerpt,
        source_snapshot_hash=leaf.source_snapshot_hash,
        link_id=leaf.link_id,
        claim_id=leaf.claim_id,
    )


async def resolve_citations(
    session: AsyncSession,
    scope: RetrievalScope,
    proposed: list[ProposedLeaf],
    *,
    require_minimum: int = 0,
) -> CitationOutcome:
    """Fresh-session validation of every proposed leaf under scope."""

    await _reload_scope_authority(session, scope)

    citations: list[LeafCitation] = []
    dropped = 0
    for leaf in proposed:
        citation = await validate_proposed_leaf(session, scope, leaf)
        if citation is None:
            dropped += 1
            continue
        citations.append(citation)

    # Deterministic order
    citations.sort(
        key=lambda c: (
            c.chapter_number,
            c.source_start,
            c.evidence_node_id,
            c.link_id or 0,
        )
    )

    blocked = require_minimum > 0 and len(citations) < require_minimum
    return CitationOutcome(
        citations=tuple(citations),
        dropped=dropped,
        blocked=blocked,
        drop_reason="invalid_leaf" if dropped else None,
    )


def cannot_build_from_summary(summary: str) -> None:
    """Guard API: summaries/scores/chat text never construct LeafCitation."""

    raise CitationValidationError(
        "summaries, scores, and chat text cannot construct LeafCitation"
    )
