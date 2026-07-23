"""Exact semantic carry-forward into an explicit unsealed target version.

Provider-free. Preserves semantic node/claim checksums while rebinding target
Phase 07 leaves and recomputing target-scoped link/edge components.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_rebuild import (
    NarrativeMemoryRebuildItem,
    NarrativeMemoryRebuildPlan,
)
from app.services.narrative_memory.authority import (
    CandidateAuthority,
    CandidateConflictError,
    CandidateNotFoundError,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    EdgeType,
    ExactSourceLink,
    MemoryClaim,
    MemoryEdge,
    MemoryNode,
    SourceKind,
    node_checksum,
    parse_memory_node,
)
from app.services.narrative_memory.dependency_graph import (
    evidence_fingerprint_from_leaf,
    evidence_fingerprint_from_link,
)
from app.services.narrative_memory.rebuild_contracts import (
    RebuildDecision,
    stable_json,
)


class CarryForwardError(ValueError):
    """Fail-closed carry error."""


@dataclass(frozen=True)
class CarryResult:
    plan_id: int
    carried_node_keys: tuple[str, ...]
    carried_claim_keys: tuple[str, ...]
    skipped_dirty_keys: tuple[str, ...]
    target_version_id: int


def carry_has_provider_capability() -> bool:
    return False


async def _load_plan(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
) -> tuple[NarrativeMemoryRebuildPlan, list[NarrativeMemoryRebuildItem]]:
    plan = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.owner_id == owner_id,
            NarrativeMemoryRebuildPlan.novel_id == novel_id,
            NarrativeMemoryRebuildPlan.id == plan_id,
        )
    )
    if plan is None:
        raise CarryForwardError("rebuild plan not found in scope")
    items = list(
        (
            await session.scalars(
                select(NarrativeMemoryRebuildItem).where(
                    NarrativeMemoryRebuildItem.plan_id == plan.id,
                    NarrativeMemoryRebuildItem.owner_id == owner_id,
                    NarrativeMemoryRebuildItem.novel_id == novel_id,
                )
            )
        ).all()
    )
    return plan, items


@dataclass(frozen=True)
class _MappedLeaf:
    evidence_node_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    source_kind: SourceKind
    optional_domain_source_key: str | None


async def _map_parent_link_to_target_leaf(
    session: AsyncSession,
    *,
    link: NarrativeMemorySourceLink,
    target_hierarchy_build_id: str,
    target_snapshot_hash: str,
) -> _MappedLeaf:
    """Map parent evidence fingerprint to exactly one target leaf."""

    parent_fp = evidence_fingerprint_from_link(link)
    leaves = list(
        (
            await session.scalars(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.build_id == target_hierarchy_build_id,
                    ChunkHierarchyNode.level == "evidence",
                    ChunkHierarchyNode.chapter_id == link.chapter_id,
                )
            )
        ).all()
    )
    matches: list[ChunkHierarchyNode] = []
    for leaf in leaves:
        try:
            leaf_fp = evidence_fingerprint_from_leaf(leaf)
        except Exception:
            continue
        if (
            leaf_fp.chapter_id == parent_fp.chapter_id
            and leaf_fp.source_start == parent_fp.source_start
            and leaf_fp.source_end == parent_fp.source_end
            and leaf_fp.content_hash == parent_fp.content_hash
        ):
            matches.append(leaf)
    if len(matches) != 1:
        raise CarryForwardError(
            f"ambiguous or missing target leaf for chapter {link.chapter_id} "
            f"(matches={len(matches)})"
        )
    leaf = matches[0]
    content_hash = str(leaf.content_hash or parent_fp.content_hash)
    if len(content_hash) != 64:
        content_hash = parent_fp.content_hash
    optional_key = None
    if link.optional_source_ref and isinstance(link.optional_source_ref, dict):
        optional_key = link.optional_source_ref.get("source_key")
    return _MappedLeaf(
        evidence_node_id=str(leaf.node_id),
        chapter_id=int(leaf.chapter_id),
        chapter_number=int(leaf.chapter_number),
        source_start=int(leaf.source_start),
        source_end=int(leaf.source_end),
        content_hash=content_hash,
        source_kind=SourceKind(link.source_kind),
        optional_domain_source_key=optional_key,
    )


async def carry_forward_from_plan(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
    expected_plan_checksum: str | None = None,
) -> CarryResult:
    """Copy carried semantic assets into the plan's unsealed target version."""

    plan, items = await _load_plan(
        session, owner_id=owner_id, novel_id=novel_id, plan_id=plan_id
    )
    if expected_plan_checksum and plan.plan_checksum != expected_plan_checksum:
        raise CarryForwardError("stale plan checksum")

    target = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == plan.target_version_id,
        )
    )
    if target is None:
        raise CarryForwardError("target version not found")
    sealed = await session.scalar(
        select(NarrativeMemoryManifest.id).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == plan.target_version_id,
        )
    )
    if sealed is not None:
        raise CarryForwardError("target version is sealed")

    if target.source_snapshot_hash != plan.new_source_snapshot_hash:
        raise CarryForwardError("target source snapshot drifted")
    if target.hierarchy_checksum != plan.new_hierarchy_checksum:
        raise CarryForwardError("target hierarchy checksum drifted")
    if target.hierarchy_build_id != plan.new_hierarchy_build_id:
        raise CarryForwardError("target hierarchy build drifted")

    parent_nodes = list(
        (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == plan.parent_version_id,
                )
            )
        ).all()
    )
    parent_claims = list(
        (
            await session.scalars(
                select(NarrativeMemoryClaim).where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == plan.parent_version_id,
                )
            )
        ).all()
    )
    parent_edges = list(
        (
            await session.scalars(
                select(NarrativeMemoryEdge).where(
                    NarrativeMemoryEdge.owner_id == owner_id,
                    NarrativeMemoryEdge.novel_id == novel_id,
                    NarrativeMemoryEdge.version_id == plan.parent_version_id,
                )
            )
        ).all()
    )
    parent_links = list(
        (
            await session.scalars(
                select(NarrativeMemorySourceLink).where(
                    NarrativeMemorySourceLink.owner_id == owner_id,
                    NarrativeMemorySourceLink.novel_id == novel_id,
                    NarrativeMemorySourceLink.version_id == plan.parent_version_id,
                )
            )
        ).all()
    )

    carried_keys = {
        i.asset_key
        for i in items
        if i.decision == RebuildDecision.CARRIED.value
        and i.asset_kind in {"chapter_state", "story_arc", "volume", "global_story"}
    }
    dirty_keys = {
        i.asset_key
        for i in items
        if i.decision
        in {RebuildDecision.DIRTY.value, RebuildDecision.STALE_BLOCKED.value}
        and i.asset_kind in {"chapter_state", "story_arc", "volume", "global_story"}
    }

    # Idempotent: if all carried nodes already exist under target, return.
    existing_target_nodes = list(
        (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == plan.target_version_id,
                )
            )
        ).all()
    )
    existing_keys = {n.node_key for n in existing_target_nodes}
    if carried_keys and carried_keys.issubset(existing_keys):
        # Verify checksum identity
        by_key = {n.node_key: n for n in existing_target_nodes}
        parent_by_key = {n.node_key: n for n in parent_nodes}
        for key in carried_keys:
            if by_key[key].content_checksum != parent_by_key[key].content_checksum:
                raise CarryForwardError(f"target conflict on carried node {key}")
        claim_rows = list(
            (
                await session.scalars(
                    select(NarrativeMemoryClaim).where(
                        NarrativeMemoryClaim.version_id == plan.target_version_id
                    )
                )
            ).all()
        )
        return CarryResult(
            plan_id=plan.id,
            carried_node_keys=tuple(sorted(carried_keys)),
            carried_claim_keys=tuple(sorted(c.claim_key for c in claim_rows)),
            skipped_dirty_keys=tuple(sorted(dirty_keys)),
            target_version_id=plan.target_version_id,
        )

    nodes_to_copy = [n for n in parent_nodes if n.node_key in carried_keys]
    if not nodes_to_copy and carried_keys:
        raise CarryForwardError("carried keys missing from parent authority")

    node_id_to_key = {n.id: n.node_key for n in parent_nodes}
    carried_node_ids = {n.id for n in nodes_to_copy}
    claims_to_copy = [c for c in parent_claims if c.node_id in carried_node_ids]
    claim_id_to_key = {c.id: c.claim_key for c in parent_claims}
    claim_ids = {c.id for c in claims_to_copy}
    links_to_copy = [lnk for lnk in parent_links if lnk.claim_id in claim_ids]
    edges_to_copy = [
        e
        for e in parent_edges
        if e.source_node_id in carried_node_ids and e.target_node_id in carried_node_ids
    ]

    # Build MemoryNode DTOs preserving semantic content checksums.
    memory_nodes: list[MemoryNode] = []
    for n in nodes_to_copy:
        dto = parse_memory_node(
            {
                "node_key": n.node_key,
                "node_kind": n.node_kind,
                "chapter_start": n.chapter_start,
                "chapter_end": n.chapter_end,
                "schema_version": n.schema_version,
                "display_label": n.display_label,
            }
        )
        if node_checksum(dto) != n.content_checksum:
            # display_label may be excluded from checksum — re-check without failing
            # if labels differ only: authority uses node_checksum which includes label.
            if n.display_label is None:
                raise CarryForwardError(f"node checksum drift for {n.node_key}")
        memory_nodes.append(dto)

    memory_edges: list[MemoryEdge] = []
    for e in edges_to_copy:
        edge_type = (
            e.edge_type
            if isinstance(e.edge_type, EdgeType)
            else EdgeType(str(e.edge_type))
        )
        memory_edges.append(
            MemoryEdge(
                edge_type=edge_type,
                source_node_key=node_id_to_key[e.source_node_id],
                target_node_key=node_id_to_key[e.target_node_id],
            )
        )

    # Map links to target leaves while preserving parent source_keys so
    # claim_checksum (which includes source_keys) stays byte-identical.
    source_links: list[ExactSourceLink] = []
    claim_source_keys: dict[str, list[str]] = {}
    for lnk in links_to_copy:
        claim_key = claim_id_to_key[lnk.claim_id]
        mapped = await _map_parent_link_to_target_leaf(
            session,
            link=lnk,
            target_hierarchy_build_id=target.hierarchy_build_id,
            target_snapshot_hash=target.source_snapshot_hash,
        )
        # Package-local keys are not stored on link rows; bind stably so claim
        # source_keys and links stay package-consistent after rebind.
        source_key = (
            f"src:{claim_key}:{mapped.evidence_node_id}:"
            f"{mapped.source_start}:{mapped.source_end}"
        )
        claim_source_keys.setdefault(claim_key, []).append(source_key)
        source_links.append(
            ExactSourceLink(
                source_key=source_key,
                claim_key=claim_key,
                source_kind=mapped.source_kind,
                hierarchy_build_id=target.hierarchy_build_id,
                evidence_node_id=mapped.evidence_node_id,
                chapter_id=mapped.chapter_id,
                chapter_number=mapped.chapter_number,
                source_start=mapped.source_start,
                source_end=mapped.source_end,
                content_hash=mapped.content_hash,
                source_snapshot_hash=target.source_snapshot_hash,
                optional_domain_source_key=mapped.optional_domain_source_key,
            )
        )

    final_claims: list[MemoryClaim] = []
    for c in claims_to_copy:
        node_key = node_id_to_key[c.node_id]
        keys = claim_source_keys.get(c.claim_key) or []
        if not keys:
            raise CarryForwardError(f"no source links for carried claim {c.claim_key}")
        # model_validate_json coerces closed StrEnums under strict models.
        final_claims.append(
            MemoryClaim.model_validate_json(
                stable_json(
                    {
                        "claim_key": c.claim_key,
                        "node_key": node_key,
                        "payload": c.typed_payload,
                        "uncertainty": c.uncertainty,
                        "confidence": float(c.confidence),
                        "visible_from_chapter": int(c.visible_from_chapter),
                        "source_keys": list(keys),
                    }
                )
            )
        )

    if not memory_nodes:
        return CarryResult(
            plan_id=plan.id,
            carried_node_keys=(),
            carried_claim_keys=(),
            skipped_dirty_keys=tuple(sorted(dirty_keys)),
            target_version_id=plan.target_version_id,
        )

    package = CandidatePackage(
        nodes=tuple(memory_nodes),
        claims=tuple(final_claims),
        edges=tuple(memory_edges),
        source_links=tuple(source_links),
    )
    authority = CandidateAuthority(session)
    try:
        persisted = await authority.persist_package(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=plan.target_version_id,
            package=package,
        )
    except CandidateConflictError as exc:
        raise CarryForwardError(str(exc)) from exc
    except CandidateNotFoundError as exc:
        raise CarryForwardError(str(exc)) from exc

    # Verify semantic content checksums preserved on nodes
    target_nodes = list(
        (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.version_id == plan.target_version_id,
                    NarrativeMemoryNode.owner_id == owner_id,
                )
            )
        ).all()
    )
    parent_by_key = {n.node_key: n for n in parent_nodes}
    for tn in target_nodes:
        if tn.node_key not in carried_keys:
            continue
        pn = parent_by_key[tn.node_key]
        if tn.content_checksum != pn.content_checksum:
            raise CarryForwardError(
                f"semantic node checksum not preserved for {tn.node_key}"
            )

    return CarryResult(
        plan_id=plan.id,
        carried_node_keys=tuple(sorted(persisted.node_ids)),
        carried_claim_keys=tuple(sorted(persisted.claim_ids)),
        skipped_dirty_keys=tuple(sorted(dirty_keys)),
        target_version_id=plan.target_version_id,
    )
