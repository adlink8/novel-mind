"""Database-row manifests, sealing, and structural validation reports."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.chunk_build import ChunkBuild, ChunkHierarchyNode
from app.models.narrative_memory import (
    MEMORY_CLAIM_KINDS,
    MEMORY_EDGE_TYPES,
    MEMORY_NODE_KINDS,
    MEMORY_SOURCE_KINDS,
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryValidationReport,
    NarrativeMemoryVersion,
)
from app.models.novel import Chapter, Novel
from app.services.chunking.manifests import content_hash
from app.services.narrative_memory.contracts import (
    ClaimPayload,
    ModelLineage,
    NodeKind,
    model_lineage_checksum,
)
from app.services.narrative_memory.provenance import (
    GraphClaimView,
    GraphEdgeView,
    GraphLinkView,
    GraphNodeView,
    STRUCTURAL_POLICY_VERSION,
    VALIDATOR_VERSION,
    StructuralReason,
    StructuralValidationResult,
    validate_memory_graph,
)


MANIFEST_SCHEMA_VERSION = "narrative-memory-manifest.v1"
CLAIM_SCHEMA_VERSION = "memory-claim.v1"
NODE_SCHEMA_VERSION = "memory-node.v1"

CLAIM_PAYLOAD_ADAPTER = TypeAdapter(ClaimPayload)


class ManifestError(ValueError):
    """Fail-closed error while loading or sealing a candidate."""


class ScopeLoadError(ManifestError):
    pass


class SealConflictError(ManifestError):
    pass


@dataclass(frozen=True)
class CandidateSnapshot:
    version: NarrativeMemoryVersion
    nodes: tuple[NarrativeMemoryNode, ...]
    claims: tuple[NarrativeMemoryClaim, ...]
    edges: tuple[NarrativeMemoryEdge, ...]
    source_links: tuple[NarrativeMemorySourceLink, ...]


@dataclass(frozen=True)
class ManifestComputation:
    component_counts: dict[str, int]
    component_hashes: dict[str, str]
    manifest_checksum: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class SealResult:
    manifest: NarrativeMemoryManifest
    report: NarrativeMemoryValidationReport
    structural: StructuralValidationResult
    manifest_checksum: str


def _canonical_mapping(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(component: str, value: Any) -> str:
    encoded = f"narrative-memory.v1:{component}\n{_canonical_mapping(value)}"
    return sha256(encoded.encode("utf-8")).hexdigest()


def version_lineage_dict(version: NarrativeMemoryVersion) -> dict[str, Any]:
    return {
        "config_hash": version.config_hash,
        "decoding_hash": version.decoding_hash,
        "eligibility_policy_version": version.eligibility_policy_version,
        "eligibility_report_checksum": version.eligibility_report_checksum,
        "hierarchy_build_id": version.hierarchy_build_id,
        "hierarchy_checksum": version.hierarchy_checksum,
        "model_lineage": version.model_lineage,
        "novel_id": version.novel_id,
        "optional_source_lineage": version.optional_source_lineage,
        "owner_id": version.owner_id,
        "parent_version_id": version.parent_version_id,
        "policy_hash": version.policy_hash,
        "prompt_hash": version.prompt_hash,
        "schema_hash": version.schema_hash,
        "source_snapshot_hash": version.source_snapshot_hash,
        "version_id": version.id,
        "version_key": version.version_key,
    }


def node_row_dict(node: NarrativeMemoryNode) -> dict[str, Any]:
    return {
        "chapter_end": node.chapter_end,
        "chapter_start": node.chapter_start,
        "content_checksum": node.content_checksum,
        "display_label": node.display_label,
        "model_lineage_checksum": node.model_lineage_checksum,
        "node_id": node.id,
        "node_key": node.node_key,
        "node_kind": node.node_kind,
        "schema_version": node.schema_version,
    }


def claim_row_dict(claim: NarrativeMemoryClaim) -> dict[str, Any]:
    return {
        "claim_checksum": claim.claim_checksum,
        "claim_id": claim.id,
        "claim_key": claim.claim_key,
        "claim_kind": claim.claim_kind,
        "confidence": claim.confidence,
        "model_lineage_checksum": claim.model_lineage_checksum,
        "node_id": claim.node_id,
        "schema_version": claim.schema_version,
        "typed_payload": claim.typed_payload,
        "uncertainty": claim.uncertainty,
        "visible_from_chapter": claim.visible_from_chapter,
    }


def edge_row_dict(edge: NarrativeMemoryEdge) -> dict[str, Any]:
    return {
        "edge_checksum": edge.edge_checksum,
        "edge_id": edge.id,
        "edge_type": edge.edge_type,
        "model_lineage_checksum": edge.model_lineage_checksum,
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
    }


def source_link_row_dict(link: NarrativeMemorySourceLink) -> dict[str, Any]:
    return {
        "chapter_id": link.chapter_id,
        "chapter_number": link.chapter_number,
        "claim_id": link.claim_id,
        "content_hash": link.content_hash,
        "evidence_node_id": link.evidence_node_id,
        "hierarchy_build_id": link.hierarchy_build_id,
        "link_checksum": link.link_checksum,
        "link_id": link.id,
        "model_lineage_checksum": link.model_lineage_checksum,
        "optional_source_ref": link.optional_source_ref,
        "source_end": link.source_end,
        "source_kind": link.source_kind,
        "source_snapshot_hash": link.source_snapshot_hash,
        "source_start": link.source_start,
    }


def compute_manifest_from_snapshot(snapshot: CandidateSnapshot) -> ManifestComputation:
    """Build a deterministic manifest from sorted PostgreSQL authority rows."""

    version_payload = version_lineage_dict(snapshot.version)
    nodes_payload = [
        node_row_dict(node)
        for node in sorted(snapshot.nodes, key=lambda row: (row.node_key, row.id))
    ]
    claims_payload = [
        claim_row_dict(claim)
        for claim in sorted(snapshot.claims, key=lambda row: (row.claim_key, row.id))
    ]
    edges_payload = [
        edge_row_dict(edge)
        for edge in sorted(
            snapshot.edges,
            key=lambda row: (
                row.edge_type,
                row.source_node_id,
                row.target_node_id,
                row.id,
            ),
        )
    ]
    links_payload = [
        source_link_row_dict(link)
        for link in sorted(
            snapshot.source_links,
            key=lambda row: (
                row.claim_id,
                row.hierarchy_build_id,
                row.evidence_node_id,
                row.source_start,
                row.source_end,
                row.id,
            ),
        )
    ]
    component_hashes = {
        "claims": _sha("manifest-claims", claims_payload),
        "edges": _sha("manifest-edges", edges_payload),
        "nodes": _sha("manifest-nodes", nodes_payload),
        "source_links": _sha("manifest-source-links", links_payload),
        "version": _sha("manifest-version", version_payload),
    }
    component_counts = {
        "claims": len(claims_payload),
        "edges": len(edges_payload),
        "nodes": len(nodes_payload),
        "source_links": len(links_payload),
        "versions": 1,
    }
    payload = {
        "component_counts": component_counts,
        "component_hashes": component_hashes,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
    }
    checksum = _sha("manifest", payload)
    return ManifestComputation(
        component_counts=component_counts,
        component_hashes=component_hashes,
        manifest_checksum=checksum,
        payload=payload,
    )


async def load_candidate_snapshot(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> CandidateSnapshot:
    if any(
        type(value) is not int or value <= 0
        for value in (owner_id, novel_id, version_id)
    ):
        raise ScopeLoadError("scope identifiers must be explicit positive integers")

    version = await session.scalar(
        select(NarrativeMemoryVersion).where(
            NarrativeMemoryVersion.owner_id == owner_id,
            NarrativeMemoryVersion.novel_id == novel_id,
            NarrativeMemoryVersion.id == version_id,
        )
    )
    if version is None:
        raise ScopeLoadError("candidate version not found in explicit scope")

    nodes = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryNode)
                .where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == version_id,
                )
                .order_by(NarrativeMemoryNode.node_key, NarrativeMemoryNode.id)
            )
        ).all()
    )
    claims = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryClaim)
                .where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == version_id,
                )
                .order_by(NarrativeMemoryClaim.claim_key, NarrativeMemoryClaim.id)
            )
        ).all()
    )
    edges = tuple(
        (
            await session.scalars(
                select(NarrativeMemoryEdge)
                .where(
                    NarrativeMemoryEdge.owner_id == owner_id,
                    NarrativeMemoryEdge.novel_id == novel_id,
                    NarrativeMemoryEdge.version_id == version_id,
                )
                .order_by(
                    NarrativeMemoryEdge.edge_type,
                    NarrativeMemoryEdge.source_node_id,
                    NarrativeMemoryEdge.target_node_id,
                    NarrativeMemoryEdge.id,
                )
            )
        ).all()
    )
    source_links = tuple(
        (
            await session.scalars(
                select(NarrativeMemorySourceLink)
                .where(
                    NarrativeMemorySourceLink.owner_id == owner_id,
                    NarrativeMemorySourceLink.novel_id == novel_id,
                    NarrativeMemorySourceLink.version_id == version_id,
                )
                .order_by(
                    NarrativeMemorySourceLink.claim_id,
                    NarrativeMemorySourceLink.evidence_node_id,
                    NarrativeMemorySourceLink.id,
                )
            )
        ).all()
    )

    _assert_row_integrity(
        nodes=nodes, claims=claims, edges=edges, source_links=source_links
    )
    return CandidateSnapshot(
        version=version,
        nodes=nodes,
        claims=claims,
        edges=edges,
        source_links=source_links,
    )


def _assert_row_integrity(
    *,
    nodes: tuple[NarrativeMemoryNode, ...],
    claims: tuple[NarrativeMemoryClaim, ...],
    edges: tuple[NarrativeMemoryEdge, ...],
    source_links: tuple[NarrativeMemorySourceLink, ...],
) -> None:
    if len({node.node_key for node in nodes}) != len(nodes):
        raise ScopeLoadError("duplicate node keys in scoped load")
    if len({claim.claim_key for claim in claims}) != len(claims):
        raise ScopeLoadError("duplicate claim keys in scoped load")
    for node in nodes:
        if node.node_kind not in MEMORY_NODE_KINDS:
            raise ScopeLoadError(f"unknown node kind: {node.node_kind}")
        if node.schema_version != NODE_SCHEMA_VERSION:
            raise ScopeLoadError(f"unknown node schema: {node.schema_version}")
    for claim in claims:
        if claim.claim_kind not in MEMORY_CLAIM_KINDS:
            raise ScopeLoadError(f"unknown claim kind: {claim.claim_kind}")
        if claim.schema_version != CLAIM_SCHEMA_VERSION:
            raise ScopeLoadError(f"unknown claim schema: {claim.schema_version}")
        # Strict models reject Python enum coercion; reparse via JSON like contracts.
        CLAIM_PAYLOAD_ADAPTER.validate_json(
            json.dumps(claim.typed_payload, ensure_ascii=False, separators=(",", ":")),
            strict=True,
        )
    for edge in edges:
        if edge.edge_type not in MEMORY_EDGE_TYPES:
            raise ScopeLoadError(f"unknown edge type: {edge.edge_type}")
    for link in source_links:
        if link.source_kind not in MEMORY_SOURCE_KINDS:
            raise ScopeLoadError(f"unknown source kind: {link.source_kind}")


async def verify_source_link_closure(
    session: AsyncSession,
    *,
    snapshot: CandidateSnapshot,
) -> tuple[str, ...]:
    """Re-slice authoritative Chapter content and prove exact leaf closure."""

    reasons: set[str] = set()
    version = snapshot.version
    novel = await session.scalar(
        select(Novel).where(
            Novel.id == version.novel_id, Novel.owner_id == version.owner_id
        )
    )
    if novel is None:
        return ("novel_scope_mismatch",)

    build = await session.scalar(
        select(ChunkBuild).where(
            ChunkBuild.build_id == version.hierarchy_build_id,
            ChunkBuild.novel_id == version.novel_id,
        )
    )
    if (
        build is None
        or not build.immutable
        or build.is_candidate
        or build.status not in {"built", "committed"}
        or build.source_snapshot_hash != version.source_snapshot_hash
        or build.manifest_checksum != version.hierarchy_checksum
    ):
        reasons.add("hierarchy_lineage_mismatch")

    claim_ids = {claim.id for claim in snapshot.claims}
    links_by_claim: dict[int, list[NarrativeMemorySourceLink]] = {}
    for link in snapshot.source_links:
        if link.claim_id not in claim_ids:
            reasons.add("package_external_reference")
            continue
        links_by_claim.setdefault(link.claim_id, []).append(link)

    for claim in snapshot.claims:
        if not links_by_claim.get(claim.id):
            reasons.add(StructuralReason.MISSING_CLAIM_SOURCE.value)

    chapter_cache: dict[int, Chapter] = {}
    evidence_cache: dict[tuple[str, str], ChunkHierarchyNode] = {}

    for link in snapshot.source_links:
        if (
            link.hierarchy_build_id != version.hierarchy_build_id
            or link.source_snapshot_hash != version.source_snapshot_hash
        ):
            reasons.add("source_snapshot_mismatch")
            continue

        evidence_key = (link.hierarchy_build_id, link.evidence_node_id)
        evidence = evidence_cache.get(evidence_key)
        if evidence is None:
            evidence = await session.scalar(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.build_id == link.hierarchy_build_id,
                    ChunkHierarchyNode.node_id == link.evidence_node_id,
                )
            )
            if evidence is not None:
                evidence_cache[evidence_key] = evidence
        if evidence is None:
            reasons.add("missing_evidence_leaf")
            continue
        if evidence.novel_id != version.novel_id:
            reasons.add("novel_scope_mismatch")
        if evidence.level != "evidence":
            reasons.add("non_evidence_leaf")
        if (
            evidence.chapter_id != link.chapter_id
            or evidence.chapter_number != link.chapter_number
        ):
            reasons.add("chapter_identity_mismatch")
        if (
            evidence.source_start != link.source_start
            or evidence.source_end != link.source_end
        ):
            reasons.add("offset_mismatch")
        if evidence.content_hash != link.content_hash:
            reasons.add("content_hash_mismatch")
        if evidence.content_hash != content_hash(evidence.content or ""):
            reasons.add("evidence_content_hash_mismatch")

        chapter = chapter_cache.get(link.chapter_id)
        if chapter is None:
            chapter = await session.scalar(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(
                    Chapter.id == link.chapter_id,
                    Chapter.novel_id == version.novel_id,
                )
            )
            if chapter is not None:
                chapter_cache[link.chapter_id] = chapter
        if chapter is None:
            reasons.add("chapter_missing")
            continue
        if chapter.chapter_number != link.chapter_number:
            reasons.add("chapter_identity_mismatch")
        text = chapter.content or ""
        if (
            link.source_start < 0
            or link.source_end > len(text)
            or link.source_end <= link.source_start
        ):
            reasons.add("invalid_offset")
            continue
        sliced = text[link.source_start : link.source_end]
        if sliced != (evidence.content or ""):
            reasons.add("reslice_mismatch")
        if content_hash(sliced) != link.content_hash:
            reasons.add("content_hash_mismatch")

        if link.source_kind == "hierarchy" and link.optional_source_ref:
            reasons.add("optional_source_invalid")
        if link.source_kind != "hierarchy" and not link.optional_source_ref:
            reasons.add("optional_source_missing")

    return tuple(sorted(reasons))


def structural_from_snapshot(snapshot: CandidateSnapshot) -> StructuralValidationResult:
    nodes_by_id = {node.id: node for node in snapshot.nodes}
    node_views = tuple(
        GraphNodeView(
            node_key=node.node_key,
            node_kind=node.node_kind,
            chapter_start=node.chapter_start,
            chapter_end=node.chapter_end,
        )
        for node in snapshot.nodes
    )
    edge_views = tuple(
        GraphEdgeView(
            edge_type=edge.edge_type,
            source_node_key=nodes_by_id[edge.source_node_id].node_key,
            target_node_key=nodes_by_id[edge.target_node_id].node_key,
        )
        for edge in snapshot.edges
        if edge.source_node_id in nodes_by_id and edge.target_node_id in nodes_by_id
    )
    claim_views = tuple(
        GraphClaimView(
            claim_key=claim.claim_key,
            node_key=nodes_by_id[claim.node_id].node_key,
        )
        for claim in snapshot.claims
        if claim.node_id in nodes_by_id
    )
    link_views = tuple(
        GraphLinkView(claim_key=claim.claim_key, source_key=f"link:{link.id}")
        for claim in snapshot.claims
        for link in snapshot.source_links
        if link.claim_id == claim.id
    )
    globals_ = [
        node
        for node in snapshot.nodes
        if node.node_kind == NodeKind.GLOBAL_STORY.value
    ]
    if globals_:
        expected_min = globals_[0].chapter_start
        expected_max = globals_[0].chapter_end
    else:
        chapter_numbers = [
            node.chapter_start
            for node in snapshot.nodes
            if node.node_kind == NodeKind.CHAPTER_STATE.value
        ]
        expected_min = min(chapter_numbers) if chapter_numbers else None
        expected_max = max(chapter_numbers) if chapter_numbers else None
    return validate_memory_graph(
        nodes=node_views,
        edges=edge_views,
        claims=claim_views,
        source_links=link_views,
        expected_chapter_min=expected_min,
        expected_chapter_max=expected_max,
    )


def report_checksum(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    manifest_checksum: str,
    verdict: str,
    reason_codes: Sequence[str],
    observed_counts: dict[str, int],
) -> str:
    payload = {
        "manifest_checksum": manifest_checksum,
        "novel_id": novel_id,
        "observed_counts": observed_counts,
        "owner_id": owner_id,
        "policy_version": STRUCTURAL_POLICY_VERSION,
        "reason_codes": list(reason_codes),
        "validator_version": VALIDATOR_VERSION,
        "verdict": verdict,
        "version_id": version_id,
    }
    return _sha("validation-report", payload)


async def seal_and_report(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> SealResult:
    """Load, validate provenance, seal deterministic manifest, append report."""

    snapshot = await load_candidate_snapshot(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    existing = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == version_id,
        )
    )
    if existing is not None:
        raise SealConflictError("candidate version is already sealed")

    structural = structural_from_snapshot(snapshot)
    link_reasons = await verify_source_link_closure(session, snapshot=snapshot)
    all_reasons = tuple(sorted(set(structural.reason_codes) | set(link_reasons)))
    ok = not all_reasons
    observed = dict(structural.observed_counts)
    observed["link_reason_count"] = len(link_reasons)
    observed["total_reason_count"] = len(all_reasons)
    structural = StructuralValidationResult(
        ok=ok,
        reason_codes=all_reasons,
        observed_counts=observed,
    )

    computation = compute_manifest_from_snapshot(snapshot)
    manifest = NarrativeMemoryManifest(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        component_counts=computation.component_counts,
        component_hashes=computation.component_hashes,
        manifest_checksum=computation.manifest_checksum,
    )
    session.add(manifest)
    await session.flush()

    verdict = structural.verdict
    checksum = report_checksum(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        manifest_checksum=computation.manifest_checksum,
        verdict=verdict,
        reason_codes=all_reasons,
        observed_counts=observed,
    )
    report = NarrativeMemoryValidationReport(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        manifest_checksum=computation.manifest_checksum,
        validator_version=VALIDATOR_VERSION,
        policy_version=STRUCTURAL_POLICY_VERSION,
        verdict=verdict,
        reason_codes=list(all_reasons),
        observed_counts=observed,
        report_checksum=checksum,
    )
    session.add(report)
    await session.flush()
    return SealResult(
        manifest=manifest,
        report=report,
        structural=structural,
        manifest_checksum=computation.manifest_checksum,
    )


def expected_model_lineage_checksum(version: NarrativeMemoryVersion) -> str:
    lineage = ModelLineage.model_validate(version.model_lineage)
    return model_lineage_checksum(lineage)
