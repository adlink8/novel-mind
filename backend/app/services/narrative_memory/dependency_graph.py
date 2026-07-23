"""Lossless dependency graph reconstruction from PostgreSQL authority.

Provider-free. Database IDs, insertion order, display text, and Phase 15
retrieval telemetry never participate in graph identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryValidationReport,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.novel import Chapter
from app.services.narrative_memory.rebuild_contracts import (
    AssetKind,
    DependencyGraph,
    EdgeKind,
    EvidenceFingerprint,
    GraphEdge,
    GraphVertex,
    ReasonCode,
    asset_kind_from_node_kind,
    stable_checksum,
)


class DependencyGraphError(ValueError):
    """Fail-closed graph construction error."""


@dataclass(frozen=True)
class ParentAuthoritySnapshot:
    version: NarrativeMemoryVersion
    nodes: tuple[NarrativeMemoryNode, ...]
    claims: tuple[NarrativeMemoryClaim, ...]
    edges: tuple[NarrativeMemoryEdge, ...]
    source_links: tuple[NarrativeMemorySourceLink, ...]
    manifest: NarrativeMemoryManifest | None
    validation_report: NarrativeMemoryValidationReport | None
    boundary_plan: dict[str, Any] | None
    boundary_plan_checksum: str | None


@dataclass(frozen=True)
class TargetHierarchySnapshot:
    hierarchy_build_id: str
    hierarchy_checksum: str
    source_snapshot_hash: str
    chapters: tuple[Chapter, ...]
    evidence_leaves: tuple[ChunkHierarchyNode, ...]


async def load_parent_authority(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    parent_version_id: int,
    require_sealed: bool = True,
) -> ParentAuthoritySnapshot:
    """Load complete parent candidate rows before any filtering."""

    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == parent_version_id,
        )
    )
    if version is None:
        raise DependencyGraphError("parent version not found in explicit scope")

    nodes = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == parent_version_id,
                )
            )
        ).all()
    )
    claims = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryClaim).where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == parent_version_id,
                )
            )
        ).all()
    )
    edges = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryEdge).where(
                    NarrativeMemoryEdge.owner_id == owner_id,
                    NarrativeMemoryEdge.novel_id == novel_id,
                    NarrativeMemoryEdge.version_id == parent_version_id,
                )
            )
        ).all()
    )
    source_links = tuple(
        (
            await session.scalars(
                select(NarrativeMemorySourceLink).where(
                    NarrativeMemorySourceLink.owner_id == owner_id,
                    NarrativeMemorySourceLink.novel_id == novel_id,
                    NarrativeMemorySourceLink.version_id == parent_version_id,
                )
            )
        ).all()
    )
    # Reject foreign-scope contamination if any row mismatched (defensive).
    for row in (*nodes, *claims, *edges, *source_links):
        if (
            row.owner_id != owner_id
            or row.novel_id != novel_id
            or row.version_id != parent_version_id
        ):
            raise DependencyGraphError("foreign scope row in parent candidate")

    node_keys = {n.node_key for n in nodes}
    if len(node_keys) != len(nodes):
        raise DependencyGraphError("duplicate node keys in parent candidate")
    claim_keys = {c.claim_key for c in claims}
    if len(claim_keys) != len(claims):
        raise DependencyGraphError("duplicate claim keys in parent candidate")

    unknown_kinds = {n.node_kind for n in nodes} - {
        "chapter_state",
        "story_arc",
        "volume",
        "global_story",
    }
    if unknown_kinds:
        raise DependencyGraphError(f"unknown node kinds: {sorted(unknown_kinds)}")

    manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == parent_version_id,
        )
    )
    if require_sealed and manifest is None:
        raise DependencyGraphError("parent candidate is unsealed")

    validation_report = await session.scalar(
        select(NarrativeMemoryValidationReport)
        .where(
            NarrativeMemoryValidationReport.owner_id == owner_id,
            NarrativeMemoryValidationReport.novel_id == novel_id,
            NarrativeMemoryValidationReport.version_id == parent_version_id,
        )
        .order_by(NarrativeMemoryValidationReport.id.desc())
        .limit(1)
    )

    build_run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == owner_id,
            NarrativeMemoryBuildRun.novel_id == novel_id,
            NarrativeMemoryBuildRun.version_id == parent_version_id,
        )
    )
    boundary_plan = None
    boundary_plan_checksum = None
    if build_run is not None:
        boundary_plan = build_run.boundary_plan
        boundary_plan_checksum = build_run.boundary_plan_checksum

    return ParentAuthoritySnapshot(
        version=version,
        nodes=nodes,
        claims=claims,
        edges=edges,
        source_links=source_links,
        manifest=manifest,
        validation_report=validation_report,
        boundary_plan=boundary_plan,
        boundary_plan_checksum=boundary_plan_checksum,
    )


async def load_target_hierarchy(
    session: AsyncSession,
    *,
    novel_id: int,
    hierarchy_build_id: str,
    expected_checksum: str | None = None,
    expected_snapshot: str | None = None,
) -> TargetHierarchySnapshot:
    """Load exact target Phase 07 hierarchy for mapping and change classification."""

    from app.models.chunk_build import ChunkBuild

    build = await session.scalar(
        select(ChunkBuild).where(
            ChunkBuild.build_id == hierarchy_build_id,
            ChunkBuild.novel_id == novel_id,
        )
    )
    if build is None:
        raise DependencyGraphError("target hierarchy build not found")
    if not build.immutable or build.is_candidate:
        raise DependencyGraphError("target hierarchy is not committed authority")
    if expected_checksum is not None and build.manifest_checksum != expected_checksum:
        raise DependencyGraphError("target hierarchy checksum mismatch")
    if (
        expected_snapshot is not None
        and build.source_snapshot_hash != expected_snapshot
    ):
        raise DependencyGraphError("target source snapshot mismatch")

    leaves = tuple(
        (
            await session.scalars(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.build_id == hierarchy_build_id,
                    ChunkHierarchyNode.level == "evidence",
                )
            )
        ).all()
    )
    chapter_ids = {leaf.chapter_id for leaf in leaves if leaf.chapter_id is not None}
    # Also include all novel chapters so insert/delete classification works
    # even when a chapter has no evidence yet.
    chapters = tuple(
        (
            await session.scalars(
                select(Chapter)
                .where(Chapter.novel_id == novel_id)
                .options(undefer(Chapter.content))
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
    )
    chapter_id_set = {int(ch.id) for ch in chapters}
    if chapter_ids and any(int(cid) not in chapter_id_set for cid in chapter_ids):
        # Foreign chapter on leaf is fail-closed.
        raise DependencyGraphError("evidence leaf references foreign chapter")

    return TargetHierarchySnapshot(
        hierarchy_build_id=build.build_id,
        hierarchy_checksum=build.manifest_checksum,
        source_snapshot_hash=build.source_snapshot_hash,
        chapters=chapters,
        evidence_leaves=leaves,
    )


def evidence_fingerprint_from_leaf(leaf: ChunkHierarchyNode) -> EvidenceFingerprint:
    if leaf.chapter_id is None or leaf.chapter_number is None:
        raise DependencyGraphError("evidence leaf missing chapter identity")
    if leaf.source_start is None or leaf.source_end is None:
        raise DependencyGraphError("evidence leaf missing offsets")
    content_hash = getattr(leaf, "content_hash", None) or getattr(
        leaf, "text_hash", None
    )
    if not content_hash:
        # Fall back to stable node payload hash if the column is absent.
        content_hash = stable_checksum(
            {
                "node_id": leaf.node_id,
                "source_start": leaf.source_start,
                "source_end": leaf.source_end,
                "chapter_id": leaf.chapter_id,
            }
        )
    return EvidenceFingerprint(
        chapter_id=int(leaf.chapter_id),
        chapter_number=int(leaf.chapter_number),
        source_start=int(leaf.source_start),
        source_end=int(leaf.source_end),
        content_hash=str(content_hash)
        if len(str(content_hash)) == 64
        else stable_checksum(str(content_hash)),
    )


def evidence_fingerprint_from_link(
    link: NarrativeMemorySourceLink,
) -> EvidenceFingerprint:
    return EvidenceFingerprint(
        chapter_id=int(link.chapter_id),
        chapter_number=int(link.chapter_number),
        source_start=int(link.source_start),
        source_end=int(link.source_end),
        content_hash=str(link.content_hash),
    )


def chapter_evidence_fingerprint(
    fingerprints: Sequence[EvidenceFingerprint],
) -> str:
    # Deduplicate: multiple claims may reference the same leaf.
    ordered = sorted({fp.fingerprint() for fp in fingerprints})
    return stable_checksum(ordered)


def build_dependency_graph(
    parent: ParentAuthoritySnapshot,
    *,
    target: TargetHierarchySnapshot | None = None,
    boundary_plan: dict[str, Any] | None = None,
    boundary_plan_checksum: str | None = None,
) -> DependencyGraph:
    """Construct canonical semantic dependency graph from authority snapshots.

    When target is provided, source/evidence vertices use target identity for
    new-side mapping; parent semantic content still anchors middle-layer nodes.
    """

    vertices: list[GraphVertex] = []
    edges: list[GraphEdge] = []

    plan = boundary_plan or parent.boundary_plan or {}
    plan_cs = boundary_plan_checksum or parent.boundary_plan_checksum
    if plan and plan_cs:
        vertices.append(
            GraphVertex(
                asset_key="boundary_plan:book",
                asset_kind=AssetKind.BOUNDARY_PLAN,
                content_checksum=plan_cs
                if len(plan_cs) == 64
                else stable_checksum(plan),
                stage_key="arc_volume_plan:book",
                attributes={
                    "source_kind": str(plan.get("source_kind") or ""),
                    "chapter_min": int(plan.get("chapter_min") or 0) or None,
                    "chapter_max": int(plan.get("chapter_max") or 0) or None,
                },
            )
        )

    # Source chapters from parent links + optional target chapters.
    chapter_ids_from_links = {
        (link.chapter_id, link.chapter_number) for link in parent.source_links
    }
    if target is not None:
        for ch in target.chapters:
            chapter_ids_from_links.add((ch.id, ch.chapter_number))

    for chapter_id, chapter_number in sorted(chapter_ids_from_links):
        vertices.append(
            GraphVertex(
                asset_key=f"source_chapter:{chapter_id}",
                asset_kind=AssetKind.SOURCE_CHAPTER,
                chapter_start=chapter_number,
                chapter_end=chapter_number,
                attributes={
                    "chapter_id": chapter_id,
                    "chapter_number": chapter_number,
                },
            )
        )

    # Evidence fingerprints (parent links + target leaves when present).
    parent_fps_by_chapter: dict[int, list[EvidenceFingerprint]] = {}
    for link in parent.source_links:
        if link.source_kind != "hierarchy":
            continue
        fp = evidence_fingerprint_from_link(link)
        parent_fps_by_chapter.setdefault(fp.chapter_id, []).append(fp)
        vertices.append(
            GraphVertex(
                asset_key=f"evidence:{fp.fingerprint()[:16]}",
                asset_kind=AssetKind.EVIDENCE_LEAF,
                chapter_start=fp.chapter_number,
                chapter_end=fp.chapter_number,
                evidence_fingerprint=fp.fingerprint(),
                attributes={
                    "chapter_id": fp.chapter_id,
                    "chapter_number": fp.chapter_number,
                    "source_start": fp.source_start,
                    "source_end": fp.source_end,
                },
            )
        )

    node_by_id = {n.id: n for n in parent.nodes}
    claim_by_id = {c.id: c for c in parent.claims}

    for node in parent.nodes:
        kind = asset_kind_from_node_kind(node.node_kind)
        # Semantic content checksum excludes DB id / version scope.
        content_cs = node.content_checksum
        if kind == AssetKind.CHAPTER_STATE:
            fps = parent_fps_by_chapter.get(
                # chapter_state nodes use chapter_start as chapter_number; map
                # via source links on claims under this node.
                _chapter_id_for_node(node, parent.source_links, claim_by_id),
                [],
            )
            evid_fp = chapter_evidence_fingerprint(fps) if fps else None
        else:
            evid_fp = None
        stage_key = _stage_key_for_node(node, plan)
        vertices.append(
            GraphVertex(
                asset_key=node.node_key,
                asset_kind=kind,
                chapter_start=node.chapter_start,
                chapter_end=node.chapter_end,
                content_checksum=content_cs,
                evidence_fingerprint=evid_fp,
                compatibility_fingerprint=node.model_lineage_checksum,
                stage_key=stage_key,
                attributes={
                    "node_kind": node.node_kind,
                    "schema_version": node.schema_version,
                },
            )
        )

    # Optional sources actually consumed (non-hierarchy links).
    optional_seen: set[str] = set()
    for link in parent.source_links:
        if link.source_kind == "hierarchy":
            continue
        claim = claim_by_id.get(link.claim_id)
        if claim is None:
            raise DependencyGraphError("optional source link claim missing")
        node = node_by_id.get(claim.node_id)
        if node is None:
            raise DependencyGraphError("optional source claim node missing")
        ref = link.optional_source_ref or {}
        opt_key = (
            f"optional:{link.source_kind}:"
            f"{stable_checksum({'kind': link.source_kind, 'ref': ref})[:16]}"
        )
        if opt_key not in optional_seen:
            optional_seen.add(opt_key)
            vertices.append(
                GraphVertex(
                    asset_key=opt_key,
                    asset_kind=AssetKind.OPTIONAL_SOURCE,
                    optional_fingerprint=stable_checksum(
                        {
                            "source_kind": link.source_kind,
                            "optional_source_ref": ref,
                            "content_hash": link.content_hash,
                        }
                    ),
                    attributes={"source_kind": link.source_kind},
                )
            )
        edges.append(
            GraphEdge(
                edge_kind=EdgeKind.OPTIONAL_TO_CLAIM,
                source_key=opt_key,
                target_key=node.node_key,
                reason=ReasonCode.OPTIONAL_SOURCE_LINEAGE,
            )
        )

    # Edges from source/evidence → chapter_state
    for node in parent.nodes:
        if node.node_kind != "chapter_state":
            continue
        chapter_id = _chapter_id_for_node(node, parent.source_links, claim_by_id)
        if chapter_id is not None:
            edges.append(
                GraphEdge(
                    edge_kind=EdgeKind.SOURCE_TO_CHAPTER_STATE,
                    source_key=f"source_chapter:{chapter_id}",
                    target_key=node.node_key,
                )
            )
        for link in parent.source_links:
            claim = claim_by_id.get(link.claim_id)
            if claim is None or claim.node_id != node.id:
                continue
            if link.source_kind != "hierarchy":
                continue
            fp = evidence_fingerprint_from_link(link)
            edges.append(
                GraphEdge(
                    edge_kind=EdgeKind.EVIDENCE_TO_CHAPTER_STATE,
                    source_key=f"evidence:{fp.fingerprint()[:16]}",
                    target_key=node.node_key,
                )
            )

    # Containment edges from parent edges table
    for edge in parent.edges:
        src = node_by_id.get(edge.source_node_id)
        tgt = node_by_id.get(edge.target_node_id)
        if src is None or tgt is None:
            raise DependencyGraphError("edge references missing node")
        if edge.edge_type == "contains":
            # parent contains child: source is parent
            if (
                src.node_kind in {"story_arc", "volume"}
                and tgt.node_kind == "chapter_state"
            ):
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.CHAPTER_TO_PARENT,
                        source_key=tgt.node_key,
                        target_key=src.node_key,
                    )
                )
            elif src.node_kind == "global_story" and tgt.node_kind in {
                "story_arc",
                "volume",
            }:
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.PARENT_TO_GLOBAL,
                        source_key=tgt.node_key,
                        target_key=src.node_key,
                    )
                )
            elif src.node_kind == "global_story" and tgt.node_kind == "chapter_state":
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.PARENT_TO_GLOBAL,
                        source_key=tgt.node_key,
                        target_key=src.node_key,
                    )
                )

    # Boundary → parents/global
    if plan and plan_cs:
        for node in parent.nodes:
            if node.node_kind in {"story_arc", "volume"}:
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.BOUNDARY_TO_PARENT,
                        source_key="boundary_plan:book",
                        target_key=node.node_key,
                    )
                )
            if node.node_kind == "global_story":
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.BOUNDARY_TO_GLOBAL,
                        source_key="boundary_plan:book",
                        target_key=node.node_key,
                    )
                )

    # If containment edges were not persisted, derive from boundary plan.
    if plan and not any(e.edge_kind == EdgeKind.CHAPTER_TO_PARENT for e in edges):
        chapter_to_parent = plan.get("chapter_to_parent") or {}
        parent_to_global = plan.get("parent_to_global") or {}
        for node in parent.nodes:
            if node.node_kind != "chapter_state":
                continue
            parent_key = chapter_to_parent.get(str(node.chapter_start))
            if parent_key:
                edges.append(
                    GraphEdge(
                        edge_kind=EdgeKind.CHAPTER_TO_PARENT,
                        source_key=node.node_key,
                        target_key=str(parent_key),
                    )
                )
                # Ensure parent vertex exists even if not in parent nodes yet
                # (target-side plan for dirty planning).
        for parent_key, global_key in parent_to_global.items():
            edges.append(
                GraphEdge(
                    edge_kind=EdgeKind.PARENT_TO_GLOBAL,
                    source_key=str(parent_key),
                    target_key=str(global_key),
                )
            )

    # Deduplicate edges by (kind, source, target)
    uniq: dict[tuple[str, str, str], GraphEdge] = {}
    for edge in edges:
        uniq[(edge.edge_kind.value, edge.source_key, edge.target_key)] = edge

    # Deduplicate vertices by asset_key (keep first semantic definition)
    vuniq: dict[str, GraphVertex] = {}
    for vertex in vertices:
        vuniq.setdefault(vertex.asset_key, vertex)

    return DependencyGraph.from_parts(list(vuniq.values()), list(uniq.values()))


def _chapter_id_for_node(
    node: NarrativeMemoryNode,
    links: Sequence[NarrativeMemorySourceLink],
    claim_by_id: dict[int, NarrativeMemoryClaim],
) -> int | None:
    for link in links:
        claim = claim_by_id.get(link.claim_id)
        if claim is not None and claim.node_id == node.id:
            return int(link.chapter_id)
    return None


def _stage_key_for_node(node: NarrativeMemoryNode, plan: dict[str, Any]) -> str | None:
    if node.node_kind == "chapter_state":
        # Prefer chapter_id-based stage keys when recoverable from node_key.
        if node.node_key.startswith("chapter_state:"):
            return node.node_key
        return f"chapter_state:{node.chapter_start}"
    if node.node_kind in {"story_arc", "volume"}:
        return node.node_key
    if node.node_kind == "global_story":
        return "global_story:book"
    return None


def graph_has_provider_capability() -> bool:
    """Static capability marker: always False for this module."""

    return False
