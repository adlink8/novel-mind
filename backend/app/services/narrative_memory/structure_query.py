"""Read-only Narrative Memory structure queries for Structure Workspace.

Product surface rules (Phase 20):
- Explicit ``version_id`` only — never invent active/current pointer.
- Always mark ``publication_status`` / badge as ``candidate_preview``.
- Server-side ``through_chapter`` visibility (nodes by chapter_end, claims by
  visible_from_chapter).
- Owner-scoped preview path that never writes and does not require full seal
  eligibility (unlike retrieval ``load_eligible_version``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import func, select
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
from app.schemas.narrative_memory_product import (
    NmClaimItem,
    NmClaimsResponse,
    NmSourceLinkItem,
    NmSourceLinksResponse,
    NmStructureNode,
    NmStructureTreeResponse,
    NmVersionListItem,
    NmVersionListResponse,
)

PUBLICATION_STATUS = "candidate_preview"
BADGE = "candidate_preview"

# Display preference: top-down L4 → L2
_KIND_ORDER = {
    "global_story": 0,
    "volume": 1,
    "story_arc": 2,
    "chapter_state": 3,
}


class StructureQueryError(Exception):
    """Base structure query error with HTTP-ish status code."""

    status_code: int = 400

    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if status_code is not None:
            self.status_code = status_code


class VersionNotFoundError(StructureQueryError):
    status_code = 404


class NodeNotFoundError(StructureQueryError):
    status_code = 404


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without DB)
# ---------------------------------------------------------------------------


def compute_readiness(
    *,
    total_nodes: int,
    has_manifest: bool = False,
    validation_verdict: str | None = None,
    node_counts_by_kind: dict[str, int] | None = None,
) -> str:
    """Best-effort readiness for product preview (never implies production active)."""

    if total_nodes <= 0:
        return "empty"
    if has_manifest and validation_verdict == "qualified_candidate":
        return "sealed_candidate"
    counts = node_counts_by_kind or {}
    has_chapter = counts.get("chapter_state", 0) > 0
    has_upper = any(
        counts.get(k, 0) > 0 for k in ("story_arc", "volume", "global_story")
    )
    if has_manifest or (has_chapter and has_upper) or total_nodes >= 3:
        return "preview_eligible"
    return "incomplete"


def filter_nodes_by_cutoff(
    nodes: Sequence[Any], through_chapter: int
) -> list[Any]:
    """Keep nodes whose full range ends at or before the spoiler cutoff."""

    return [n for n in nodes if int(n.chapter_end) <= int(through_chapter)]


def filter_claims_by_cutoff(
    claims: Sequence[Any], through_chapter: int
) -> list[Any]:
    return [
        c
        for c in claims
        if int(c.visible_from_chapter) <= int(through_chapter)
    ]


def claim_summary_text(typed_payload: dict[str, Any] | None) -> str:
    """Extract a short human-readable summary from a typed claim payload."""

    if not typed_payload:
        return ""
    for key in ("summary", "summary_text", "text", "description"):
        val = typed_payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
    outcome = typed_payload.get("outcome")
    if isinstance(outcome, dict):
        if outcome.get("value_kind") == "text" and outcome.get("value"):
            return str(outcome["value"]).strip()[:500]
        if outcome.get("value") is not None:
            return str(outcome["value"]).strip()[:500]
    if isinstance(outcome, str) and outcome.strip():
        return outcome.strip()[:500]
    for key in ("event_kind", "dimension", "entity_key", "claim_kind"):
        val = typed_payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:200]
    return ""


def assemble_structure_nodes(
    nodes: Sequence[Any],
    contains_edges: Sequence[Any],
) -> list[NmStructureNode]:
    """Build product tree nodes with child_ids from contains edges among visible set."""

    visible_ids = {int(n.id) for n in nodes}
    children: dict[int, list[int]] = defaultdict(list)
    for edge in contains_edges:
        src = int(edge.source_node_id)
        tgt = int(edge.target_node_id)
        if src in visible_ids and tgt in visible_ids:
            children[src].append(tgt)

    for parent_id in children:
        children[parent_id] = sorted(set(children[parent_id]))

    ordered = sorted(
        nodes,
        key=lambda n: (
            _KIND_ORDER.get(str(n.node_kind), 9),
            int(n.chapter_start),
            int(n.chapter_end),
            int(n.id),
        ),
    )
    result: list[NmStructureNode] = []
    for n in ordered:
        label = getattr(n, "display_label", None)
        result.append(
            NmStructureNode(
                id=int(n.id),
                node_key=str(n.node_key),
                node_kind=str(n.node_kind),
                display_label=label if label else None,
                chapter_start=int(n.chapter_start),
                chapter_end=int(n.chapter_end),
                child_ids=children.get(int(n.id), []),
            )
        )
    return result


def resolve_through_chapter(
    through_chapter: int | None,
    *,
    novel_chapter_count: int | None = None,
) -> int:
    """Normalize cutoff: positive int; clamp to novel max when known."""

    if through_chapter is None:
        if novel_chapter_count and novel_chapter_count > 0:
            return int(novel_chapter_count)
        return 10**9
    value = int(through_chapter)
    if value < 1:
        raise StructureQueryError(
            "through_chapter must be >= 1", status_code=400
        )
    if novel_chapter_count and novel_chapter_count > 0:
        return min(value, int(novel_chapter_count))
    return value


def count_nodes_by_kind(nodes: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for n in nodes:
        counts[str(n.node_kind)] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# DB-backed read-only loaders
# ---------------------------------------------------------------------------


async def _load_version_or_404(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> NarrativeMemoryVersion:
    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == version_id,
        )
    )
    if version is None:
        raise VersionNotFoundError("narrative memory version not found")
    return version


async def _node_counts_for_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> dict[str, int]:
    rows = (
        await session.execute(
            select(NarrativeMemoryNode.node_kind, func.count())
            .where(
                NarrativeMemoryNode.owner_id == owner_id,
                NarrativeMemoryNode.novel_id == novel_id,
                NarrativeMemoryNode.version_id == version_id,
            )
            .group_by(NarrativeMemoryNode.node_kind)
        )
    ).all()
    return {str(kind): int(count) for kind, count in rows}


async def _manifest_and_verdict(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> tuple[bool, str | None]:
    manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == version_id,
        )
    )
    if manifest is None:
        return False, None
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
    verdict = report.verdict if report is not None else None
    return True, verdict


async def list_versions(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> NmVersionListResponse:
    """List candidate versions for a novel; never picks a default active version."""

    versions = list(
        (
            await session.scalars(
                select(NarrativeMemoryVersion)
                .where(
                    NarrativeMemoryVersion.owner_id == owner_id,
                    NarrativeMemoryVersion.novel_id == novel_id,
                )
                .order_by(NarrativeMemoryVersion.id.desc())
            )
        ).all()
    )
    items: list[NmVersionListItem] = []
    for v in versions:
        counts = await _node_counts_for_version(
            session, owner_id=owner_id, novel_id=novel_id, version_id=v.id
        )
        total = sum(counts.values())
        has_manifest, verdict = await _manifest_and_verdict(
            session, owner_id=owner_id, novel_id=novel_id, version_id=v.id
        )
        readiness = compute_readiness(
            total_nodes=total,
            has_manifest=has_manifest,
            validation_verdict=verdict,
            node_counts_by_kind=counts,
        )
        created = None
        if getattr(v, "created_at", None) is not None:
            created = v.created_at.isoformat()
        items.append(
            NmVersionListItem(
                version_id=v.id,
                version_key=v.version_key,
                readiness=readiness,  # type: ignore[arg-type]
                badge=BADGE,
                node_counts=counts or None,
                has_manifest=has_manifest,
                validation_verdict=verdict,
                created_at=created,
            )
        )
    message = None
    if not items:
        message = "no narrative memory candidate versions"
    return NmVersionListResponse(
        novel_id=novel_id,
        versions=items,
        publication_status=PUBLICATION_STATUS,
        message=message,
    )


async def load_structure_tree(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    through_chapter: int,
) -> NmStructureTreeResponse:
    """Load visible structure tree for an explicit candidate version (preview)."""

    await _load_version_or_404(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )

    all_nodes = list(
        (
            await session.scalars(
                select(NarrativeMemoryNode)
                .where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == version_id,
                )
                .order_by(
                    NarrativeMemoryNode.chapter_start.asc(),
                    NarrativeMemoryNode.id.asc(),
                )
            )
        ).all()
    )
    full_counts = count_nodes_by_kind(all_nodes)
    has_manifest, verdict = await _manifest_and_verdict(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    readiness = compute_readiness(
        total_nodes=len(all_nodes),
        has_manifest=has_manifest,
        validation_verdict=verdict,
        node_counts_by_kind=full_counts,
    )

    visible = filter_nodes_by_cutoff(all_nodes, through_chapter)
    visible_ids = [n.id for n in visible]
    edges: list[NarrativeMemoryEdge] = []
    if visible_ids:
        edges = list(
            (
                await session.scalars(
                    select(NarrativeMemoryEdge).where(
                        NarrativeMemoryEdge.owner_id == owner_id,
                        NarrativeMemoryEdge.novel_id == novel_id,
                        NarrativeMemoryEdge.version_id == version_id,
                        NarrativeMemoryEdge.edge_type == "contains",
                        NarrativeMemoryEdge.source_node_id.in_(visible_ids),
                    )
                )
            ).all()
        )

    product_nodes = assemble_structure_nodes(visible, edges)
    message: str | None = None
    if readiness == "empty":
        message = "candidate version has no structure nodes"
    elif not product_nodes and all_nodes:
        message = (
            f"no nodes visible through chapter {through_chapter}; "
            "raise through_chapter or wait for earlier-range nodes"
        )
    elif readiness == "incomplete":
        message = "candidate structure is incomplete (preview only)"

    return NmStructureTreeResponse(
        novel_id=novel_id,
        version_id=version_id,
        through_chapter=through_chapter,
        publication_status=PUBLICATION_STATUS,
        readiness=readiness,  # type: ignore[arg-type]
        nodes=product_nodes,
        message=message,
    )


async def load_node_claims(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    node_id: int,
    through_chapter: int,
) -> NmClaimsResponse:
    await _load_version_or_404(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    node = await session.scalar(
        select(NarrativeMemoryNode).where(
            NarrativeMemoryNode.owner_id == owner_id,
            NarrativeMemoryNode.novel_id == novel_id,
            NarrativeMemoryNode.version_id == version_id,
            NarrativeMemoryNode.id == node_id,
        )
    )
    if node is None:
        raise NodeNotFoundError("narrative memory node not found")

    claims = list(
        (
            await session.scalars(
                select(NarrativeMemoryClaim)
                .where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == version_id,
                    NarrativeMemoryClaim.node_id == node_id,
                    NarrativeMemoryClaim.visible_from_chapter <= through_chapter,
                )
                .order_by(
                    NarrativeMemoryClaim.visible_from_chapter.asc(),
                    NarrativeMemoryClaim.id.asc(),
                )
            )
        ).all()
    )
    items = [
        NmClaimItem(
            id=c.id,
            claim_kind=c.claim_kind,
            summary=claim_summary_text(c.typed_payload or {}),
            text=claim_summary_text(c.typed_payload or {}) or None,
            typed_payload=dict(c.typed_payload or {}),
            uncertainty=c.uncertainty,
            confidence=float(c.confidence),
            visible_from_chapter=c.visible_from_chapter,
            node_id=c.node_id,
        )
        for c in claims
    ]
    return NmClaimsResponse(
        novel_id=novel_id,
        version_id=version_id,
        node_id=node_id,
        through_chapter=through_chapter,
        publication_status=PUBLICATION_STATUS,
        claims=items,
        message=None if items else "no claims visible at this cutoff",
    )


async def load_node_source_links(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    node_id: int,
    through_chapter: int,
) -> NmSourceLinksResponse:
    await _load_version_or_404(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    node = await session.scalar(
        select(NarrativeMemoryNode).where(
            NarrativeMemoryNode.owner_id == owner_id,
            NarrativeMemoryNode.novel_id == novel_id,
            NarrativeMemoryNode.version_id == version_id,
            NarrativeMemoryNode.id == node_id,
        )
    )
    if node is None:
        raise NodeNotFoundError("narrative memory node not found")

    claim_ids = list(
        (
            await session.scalars(
                select(NarrativeMemoryClaim.id).where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == version_id,
                    NarrativeMemoryClaim.node_id == node_id,
                    NarrativeMemoryClaim.visible_from_chapter <= through_chapter,
                )
            )
        ).all()
    )
    if not claim_ids:
        return NmSourceLinksResponse(
            novel_id=novel_id,
            version_id=version_id,
            node_id=node_id,
            through_chapter=through_chapter,
            publication_status=PUBLICATION_STATUS,
            source_links=[],
            message="no source links (no visible claims at this cutoff)",
        )

    links = list(
        (
            await session.scalars(
                select(NarrativeMemorySourceLink)
                .where(
                    NarrativeMemorySourceLink.owner_id == owner_id,
                    NarrativeMemorySourceLink.novel_id == novel_id,
                    NarrativeMemorySourceLink.version_id == version_id,
                    NarrativeMemorySourceLink.claim_id.in_(claim_ids),
                    NarrativeMemorySourceLink.chapter_number <= through_chapter,
                )
                .order_by(
                    NarrativeMemorySourceLink.chapter_number.asc(),
                    NarrativeMemorySourceLink.source_start.asc(),
                    NarrativeMemorySourceLink.id.asc(),
                )
            )
        ).all()
    )
    items = [
        NmSourceLinkItem(
            id=link.id,
            claim_id=link.claim_id,
            source_kind=link.source_kind,
            hierarchy_build_id=link.hierarchy_build_id,
            evidence_node_id=link.evidence_node_id,
            chapter_number=link.chapter_number,
            source_start=link.source_start,
            source_end=link.source_end,
            content_hash=link.content_hash,
            optional_source_ref=link.optional_source_ref,
        )
        for link in links
    ]
    return NmSourceLinksResponse(
        novel_id=novel_id,
        version_id=version_id,
        node_id=node_id,
        through_chapter=through_chapter,
        publication_status=PUBLICATION_STATUS,
        source_links=items,
        message=None if items else "no source links visible at this cutoff",
    )
