"""Package construction and script rebinding for bottom-up builder stages."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.services.narrative_memory.builder_contracts import (
    ChapterStateInputPackage,
    ChapterStateModelOutput,
    EvidenceLeafRef,
    OptionalSourceSignal,
    SourceStatus,
    assert_no_forbidden_keys,
    exact_cache_key,
    package_checksum,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    ChapterStateNode,
    EdgeType,
    ExactSourceLink,
    GlobalStoryNode,
    MemoryClaim,
    MemoryEdge,
    ModelLineage,
    NodeKind,
    SourceKind,
    StoryArcNode,
    VolumeNode,
)


class PackageBuildError(ValueError):
    pass


async def load_chapter_evidence_leaves(
    session: AsyncSession,
    *,
    hierarchy_build_id: str,
    novel_id: int,
    chapter_id: int,
    chapter_number: int,
    source_snapshot_hash: str,
) -> tuple[EvidenceLeafRef, ...]:
    rows = (
        await session.scalars(
            select(ChunkHierarchyNode)
            .where(
                ChunkHierarchyNode.build_id == hierarchy_build_id,
                ChunkHierarchyNode.novel_id == novel_id,
                ChunkHierarchyNode.chapter_id == chapter_id,
                ChunkHierarchyNode.level == "evidence",
            )
            .order_by(
                ChunkHierarchyNode.order_index,
                ChunkHierarchyNode.source_start,
                ChunkHierarchyNode.node_id,
            )
        )
    ).all()
    if not rows:
        raise PackageBuildError("chapter has no evidence leaves under frozen hierarchy")
    leaves: list[EvidenceLeafRef] = []
    for row in rows:
        if row.chapter_number != chapter_number and chapter_number != 0:
            raise PackageBuildError("evidence leaf chapter_number mismatch")
        leaves.append(
            EvidenceLeafRef(
                hierarchy_build_id=hierarchy_build_id,
                evidence_node_id=row.node_id,
                chapter_id=int(row.chapter_id),
                chapter_number=int(row.chapter_number),
                source_start=int(row.source_start),
                source_end=int(row.source_end),
                content_hash=row.content_hash,
                source_snapshot_hash=source_snapshot_hash,
            )
        )
    return tuple(leaves)


def build_chapter_state_input(
    *,
    version: NarrativeMemoryVersion,
    chapter_id: int,
    chapter_number: int,
    evidence_leaves: Sequence[EvidenceLeafRef],
    optional_signals: Sequence[OptionalSourceSignal] = (),
    prompt_hash: str,
    schema_hash: str,
    model_lineage: ModelLineage,
    decoding_hash: str,
    config_hash: str,
    policy_hash: str,
) -> ChapterStateInputPackage:
    package = ChapterStateInputPackage(
        stage_key=f"chapter_state:{chapter_id}",
        owner_id=version.owner_id,
        novel_id=version.novel_id,
        version_id=version.id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        hierarchy_build_id=version.hierarchy_build_id,
        source_snapshot_hash=version.source_snapshot_hash,
        hierarchy_checksum=version.hierarchy_checksum,
        eligibility_report_checksum=version.eligibility_report_checksum,
        evidence_leaves=tuple(evidence_leaves),
        optional_signals=tuple(optional_signals),
        prompt_hash=prompt_hash,
        schema_hash=schema_hash,
        model_lineage=model_lineage,
        decoding_hash=decoding_hash,
        config_hash=config_hash,
        policy_hash=policy_hash,
    )
    assert_no_forbidden_keys(package.model_dump(mode="json"))
    return package


def chapter_cache_identity(package: ChapterStateInputPackage) -> tuple[str, str]:
    checksum = package_checksum(package)
    optional_lineage = {
        signal.source_kind: {
            "status": signal.status.value,
            "reason_code": signal.reason_code,
            "signal_keys": list(signal.signal_keys),
            "lineage": signal.lineage,
        }
        for signal in sorted(package.optional_signals, key=lambda s: s.source_kind)
    }
    cache_key = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=checksum,
        prompt_hash=package.prompt_hash,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage=optional_lineage,
    )
    return checksum, cache_key


def rebind_chapter_state_package(
    *,
    input_package: ChapterStateInputPackage,
    model_output: ChapterStateModelOutput | dict[str, Any],
) -> CandidatePackage:
    if isinstance(model_output, dict):
        cleaned = {
            key: value
            for key, value in model_output.items()
            if key
            in {
                "node_key",
                "display_label",
                "summary",
                "key_elements",
                "narrative_progress",
                "claims",
                "source_bindings",
            }
        }
        model_output = ChapterStateModelOutput.model_validate(cleaned)
    assert_no_forbidden_keys(model_output.model_dump(mode="json"))

    node_key = f"chapter_state:{input_package.chapter_number}"
    node = ChapterStateNode(
        node_kind=NodeKind.CHAPTER_STATE,
        node_key=node_key,
        chapter_start=input_package.chapter_number,
        chapter_end=input_package.chapter_number,
        schema_version="memory-node.v1",
        display_label=model_output.summary or model_output.display_label,
    )

    leaves_by_id = {
        leaf.evidence_node_id: leaf for leaf in input_package.evidence_leaves
    }
    source_links: list[ExactSourceLink] = []
    claims: list[MemoryClaim] = []

    for index, raw_claim in enumerate(model_output.claims, start=1):
        # Claim keys are candidate-authority identities, not model-authored
        # content.  A model can accidentally copy the previous chapter's key
        # (for example, ``chapter_state:301:claim:1`` while processing chapter
        # 302).  Keeping that key would make an otherwise valid retry collide
        # with an immutable claim already stored for the previous chapter.
        # Use the current chapter and deterministic claim position as the
        # authority key, while still accepting the model key below for source
        # binding compatibility.
        model_claim_key = str(raw_claim.get("claim_key") or "")
        claim_key = f"{node_key}:claim:{index}"
        binding_claim_keys = {claim_key}
        if model_claim_key:
            binding_claim_keys.add(model_claim_key)
        bindings = [
            item
            for item in model_output.source_bindings
            if str(item.get("claim_key", "")) in binding_claim_keys
            or (
                index == 1 and "claim_key" not in item and len(model_output.claims) == 1
            )
        ]
        if not bindings:
            # Bind first leaf deterministically when model omitted explicit binding.
            leaf = input_package.evidence_leaves[0]
            bindings = [
                {
                    "claim_key": claim_key,
                    "evidence_node_id": leaf.evidence_node_id,
                    "source_key": f"{claim_key}:src:1",
                }
            ]

        claim_source_keys: list[str] = []
        for bind_index, binding in enumerate(bindings, start=1):
            evidence_node_id = str(binding.get("evidence_node_id") or "")
            leaf = leaves_by_id.get(evidence_node_id)
            if leaf is None:
                raise PackageBuildError(
                    f"model referenced unknown evidence leaf {evidence_node_id}"
                )
            # Source keys are package-local as well.  Re-key them so a stale
            # model key cannot leak into the persisted provenance identity.
            source_key = f"{claim_key}:src:{bind_index}"
            claim_source_keys.append(source_key)
            source_links.append(
                ExactSourceLink(
                    source_key=source_key,
                    claim_key=claim_key,
                    source_kind=SourceKind.HIERARCHY,
                    hierarchy_build_id=input_package.hierarchy_build_id,
                    evidence_node_id=leaf.evidence_node_id,
                    chapter_id=leaf.chapter_id,
                    chapter_number=leaf.chapter_number,
                    source_start=leaf.source_start,
                    source_end=leaf.source_end,
                    content_hash=leaf.content_hash,
                    source_snapshot_hash=leaf.source_snapshot_hash,
                )
            )

        payload = dict(raw_claim.get("payload") or raw_claim)
        if "claim_kind" not in payload and "payload" in raw_claim:
            payload = dict(raw_claim["payload"])
        # Strip claim envelope keys if model nested incorrectly.
        for drop in (
            "claim_key",
            "node_key",
            "uncertainty",
            "confidence",
            "visible_from_chapter",
            "source_keys",
            "non_authoritative_statement",
            "payload",
        ):
            payload.pop(drop, None)
        if "claim_kind" not in payload:
            raise PackageBuildError("claim payload missing claim_kind")

        claim_body = {
            "claim_key": claim_key,
            "node_key": node_key,
            "payload": payload,
            "uncertainty": raw_claim.get("uncertainty", "certain"),
            "confidence": float(raw_claim.get("confidence", 0.9)),
            "visible_from_chapter": int(
                raw_claim.get("visible_from_chapter", input_package.chapter_number)
            ),
            "source_keys": claim_source_keys,
            "non_authoritative_statement": raw_claim.get("non_authoritative_statement"),
        }
        claims.append(
            MemoryClaim.model_validate_json(
                json.dumps(claim_body, ensure_ascii=False, separators=(",", ":"))
            )
        )

    package = CandidatePackage(
        nodes=(node,),
        claims=tuple(claims),
        edges=(),
        source_links=tuple(source_links),
    )
    assert_no_forbidden_keys(package.model_dump(mode="json"))
    return package


def rebind_cached_chapter_state_package(
    *,
    input_package: ChapterStateInputPackage,
    cached_package: CandidatePackage,
) -> CandidatePackage:
    """Rebind a cached chapter package through the current chapter identity.

    Older builder attempts may have cached a validated package before the
    authority transaction rejected it.  Reconstructing the model-shaped
    envelope here makes those cache entries safe to resume instead of
    replaying stale model-owned claim/source keys.
    """
    claims: list[dict[str, Any]] = []
    source_bindings: list[dict[str, str]] = []
    links_by_claim: dict[str, list[ExactSourceLink]] = {}
    for link in cached_package.source_links:
        links_by_claim.setdefault(link.claim_key, []).append(link)
    for claim in cached_package.claims:
        claims.append(
            {
                "claim_key": claim.claim_key,
                "payload": claim.payload.model_dump(mode="json"),
                "uncertainty": claim.uncertainty.value,
                "confidence": float(claim.confidence),
                "visible_from_chapter": claim.visible_from_chapter,
            }
        )
        for link in links_by_claim.get(claim.claim_key, []):
            source_bindings.append(
                {
                    "claim_key": claim.claim_key,
                    "evidence_node_id": link.evidence_node_id,
                    "source_key": link.source_key,
                }
            )
    cached_node = next(
        (
            node
            for node in cached_package.nodes
            if node.node_key == f"chapter_state:{input_package.chapter_number}"
        ),
        None,
    )
    return rebind_chapter_state_package(
        input_package=input_package,
        model_output={
            "node_key": f"chapter_state:{input_package.chapter_number}",
            "display_label": cached_node.display_label if cached_node else None,
            "claims": claims,
            "source_bindings": source_bindings,
        },
    )


async def load_child_chapter_authority(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    chapter_numbers: Sequence[int],
) -> tuple[
    list[NarrativeMemoryNode],
    list[NarrativeMemoryClaim],
    list[NarrativeMemorySourceLink],
]:
    nodes = (
        await session.scalars(
            select(NarrativeMemoryNode)
            .where(
                NarrativeMemoryNode.owner_id == owner_id,
                NarrativeMemoryNode.novel_id == novel_id,
                NarrativeMemoryNode.version_id == version_id,
                NarrativeMemoryNode.node_kind == NodeKind.CHAPTER_STATE.value,
                NarrativeMemoryNode.chapter_start.in_(tuple(chapter_numbers)),
            )
            .order_by(NarrativeMemoryNode.chapter_start, NarrativeMemoryNode.node_key)
        )
    ).all()
    if len(nodes) != len(set(chapter_numbers)):
        raise PackageBuildError("missing completed chapter state authority")
    node_ids = [node.id for node in nodes]
    claims = (
        await session.scalars(
            select(NarrativeMemoryClaim)
            .where(
                NarrativeMemoryClaim.owner_id == owner_id,
                NarrativeMemoryClaim.novel_id == novel_id,
                NarrativeMemoryClaim.version_id == version_id,
                NarrativeMemoryClaim.node_id.in_(node_ids),
            )
            .order_by(NarrativeMemoryClaim.claim_key)
        )
    ).all()
    claim_ids = [claim.id for claim in claims]
    links = (
        await session.scalars(
            select(NarrativeMemorySourceLink)
            .where(
                NarrativeMemorySourceLink.owner_id == owner_id,
                NarrativeMemorySourceLink.novel_id == novel_id,
                NarrativeMemorySourceLink.version_id == version_id,
                NarrativeMemorySourceLink.claim_id.in_(claim_ids),
            )
            .order_by(
                NarrativeMemorySourceLink.claim_id,
                NarrativeMemorySourceLink.evidence_node_id,
            )
        )
    ).all()
    return list(nodes), list(claims), list(links)


def build_arc_volume_candidate(
    *,
    node_kind: NodeKind,
    node_key: str,
    chapter_start: int,
    chapter_end: int,
    child_nodes: Sequence[NarrativeMemoryNode],
    child_claims: Sequence[NarrativeMemoryClaim],
    child_links: Sequence[NarrativeMemorySourceLink],
    model_claims: Sequence[dict[str, Any]],
    display_label: str | None = None,
) -> CandidatePackage:
    if node_kind not in {NodeKind.STORY_ARC, NodeKind.VOLUME}:
        raise PackageBuildError("parent node must be story_arc or volume")
    if not child_nodes:
        raise PackageBuildError("parent requires completed children")

    parent_ctor = StoryArcNode if node_kind == NodeKind.STORY_ARC else VolumeNode
    parent = parent_ctor(
        node_kind=node_kind,
        node_key=node_key,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        schema_version="memory-node.v1",
        display_label=display_label,
    )
    child_package_nodes = tuple(
        ChapterStateNode(
            node_kind=NodeKind.CHAPTER_STATE,
            node_key=child.node_key,
            chapter_start=child.chapter_start,
            chapter_end=child.chapter_end,
            schema_version=child.schema_version,
            display_label=child.display_label,
        )
        for child in child_nodes
    )
    edges = tuple(
        MemoryEdge(
            edge_type=EdgeType.CONTAINS,
            source_node_key=node_key,
            target_node_key=child.node_key,
        )
        for child in child_nodes
    )

    links_by_claim_id = {}
    for link in child_links:
        links_by_claim_id.setdefault(link.claim_id, []).append(link)

    claims: list[MemoryClaim] = []
    source_links: list[ExactSourceLink] = []
    # Default: re-express child claims under parent with direct leaf links.
    if not model_claims:
        for index, child_claim in enumerate(child_claims, start=1):
            claim_key = f"{node_key}:claim:{index}"
            leaf_links = links_by_claim_id.get(child_claim.id, [])
            if not leaf_links:
                raise PackageBuildError("child claim missing direct leaf links")
            source_keys: list[str] = []
            for link_index, link in enumerate(leaf_links, start=1):
                source_key = f"{claim_key}:src:{link_index}"
                source_keys.append(source_key)
                source_links.append(
                    ExactSourceLink(
                        source_key=source_key,
                        claim_key=claim_key,
                        source_kind=SourceKind(link.source_kind),
                        hierarchy_build_id=link.hierarchy_build_id,
                        evidence_node_id=link.evidence_node_id,
                        chapter_id=link.chapter_id,
                        chapter_number=link.chapter_number,
                        source_start=link.source_start,
                        source_end=link.source_end,
                        content_hash=link.content_hash,
                        source_snapshot_hash=link.source_snapshot_hash,
                        optional_domain_source_key=(
                            (link.optional_source_ref or {}).get("source_key")
                            if link.optional_source_ref
                            else None
                        ),
                    )
                )
            claim_body = {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": child_claim.typed_payload,
                "uncertainty": child_claim.uncertainty,
                "confidence": float(child_claim.confidence),
                "visible_from_chapter": max(
                    chapter_start, int(child_claim.visible_from_chapter)
                ),
                "source_keys": source_keys,
            }
            claims.append(
                MemoryClaim.model_validate_json(
                    json.dumps(claim_body, ensure_ascii=False, separators=(",", ":"))
                )
            )
    else:
        all_links = list(child_links)
        if not all_links:
            raise PackageBuildError("no leaf links available for parent claims")
        for index, raw in enumerate(model_claims, start=1):
            claim_key = str(raw.get("claim_key") or f"{node_key}:claim:{index}")
            leaf = all_links[min(index - 1, len(all_links) - 1)]
            source_key = f"{claim_key}:src:1"
            source_links.append(
                ExactSourceLink(
                    source_key=source_key,
                    claim_key=claim_key,
                    source_kind=SourceKind(leaf.source_kind),
                    hierarchy_build_id=leaf.hierarchy_build_id,
                    evidence_node_id=leaf.evidence_node_id,
                    chapter_id=leaf.chapter_id,
                    chapter_number=leaf.chapter_number,
                    source_start=leaf.source_start,
                    source_end=leaf.source_end,
                    content_hash=leaf.content_hash,
                    source_snapshot_hash=leaf.source_snapshot_hash,
                )
            )
            payload = dict(raw.get("payload") or raw)
            for drop in (
                "claim_key",
                "node_key",
                "uncertainty",
                "confidence",
                "visible_from_chapter",
                "source_keys",
                "payload",
            ):
                payload.pop(drop, None)
            claim_body = {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": payload,
                "uncertainty": raw.get("uncertainty", "certain"),
                "confidence": float(raw.get("confidence", 0.9)),
                "visible_from_chapter": int(
                    raw.get("visible_from_chapter", chapter_start)
                ),
                "source_keys": [source_key],
            }
            claims.append(
                MemoryClaim.model_validate_json(
                    json.dumps(claim_body, ensure_ascii=False, separators=(",", ":"))
                )
            )

    package = CandidatePackage(
        nodes=(parent, *child_package_nodes),
        claims=tuple(claims),
        edges=edges,
        source_links=tuple(source_links),
    )
    assert_no_forbidden_keys(package.model_dump(mode="json"))
    return package


def build_global_candidate(
    *,
    chapter_start: int,
    chapter_end: int,
    parent_nodes: Sequence[NarrativeMemoryNode],
    parent_claims: Sequence[NarrativeMemoryClaim],
    parent_links: Sequence[NarrativeMemorySourceLink],
    model_claims: Sequence[dict[str, Any]] | None = None,
) -> CandidatePackage:
    if not parent_nodes:
        raise PackageBuildError("global requires validated parents")
    node_key = "global_story:book"
    global_node = GlobalStoryNode(
        node_kind=NodeKind.GLOBAL_STORY,
        node_key=node_key,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        schema_version="memory-node.v1",
        display_label="Global Story",
    )
    middle_nodes = []
    for parent in parent_nodes:
        if parent.node_kind == NodeKind.STORY_ARC.value:
            middle_nodes.append(
                StoryArcNode(
                    node_kind=NodeKind.STORY_ARC,
                    node_key=parent.node_key,
                    chapter_start=parent.chapter_start,
                    chapter_end=parent.chapter_end,
                    schema_version=parent.schema_version,
                    display_label=parent.display_label,
                )
            )
        elif parent.node_kind == NodeKind.VOLUME.value:
            middle_nodes.append(
                VolumeNode(
                    node_kind=NodeKind.VOLUME,
                    node_key=parent.node_key,
                    chapter_start=parent.chapter_start,
                    chapter_end=parent.chapter_end,
                    schema_version=parent.schema_version,
                    display_label=parent.display_label,
                )
            )
        else:
            raise PackageBuildError("global parents must be story_arc or volume")

    edges = tuple(
        MemoryEdge(
            edge_type=EdgeType.CONTAINS,
            source_node_key=node_key,
            target_node_key=parent.node_key,
        )
        for parent in parent_nodes
    )

    claims_src = list(model_claims or [])
    claims: list[MemoryClaim] = []
    source_links: list[ExactSourceLink] = []
    if not claims_src:
        for index, parent_claim in enumerate(parent_claims, start=1):
            claim_key = f"{node_key}:claim:{index}"
            matching = [
                link for link in parent_links if link.claim_id == parent_claim.id
            ]
            if not matching:
                matching = list(parent_links[:1])
            if not matching:
                raise PackageBuildError("parent claim missing leaf links")
            source_keys: list[str] = []
            for link_index, link in enumerate(matching, start=1):
                source_key = f"{claim_key}:src:{link_index}"
                source_keys.append(source_key)
                source_links.append(
                    ExactSourceLink(
                        source_key=source_key,
                        claim_key=claim_key,
                        source_kind=SourceKind(link.source_kind),
                        hierarchy_build_id=link.hierarchy_build_id,
                        evidence_node_id=link.evidence_node_id,
                        chapter_id=link.chapter_id,
                        chapter_number=link.chapter_number,
                        source_start=link.source_start,
                        source_end=link.source_end,
                        content_hash=link.content_hash,
                        source_snapshot_hash=link.source_snapshot_hash,
                    )
                )
            claim_body = {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": parent_claim.typed_payload,
                "uncertainty": parent_claim.uncertainty,
                "confidence": float(parent_claim.confidence),
                "visible_from_chapter": max(
                    chapter_start, int(parent_claim.visible_from_chapter)
                ),
                "source_keys": source_keys,
            }
            claims.append(
                MemoryClaim.model_validate_json(
                    json.dumps(claim_body, ensure_ascii=False, separators=(",", ":"))
                )
            )
    else:
        if not parent_links:
            raise PackageBuildError("no leaf links for global claims")
        for index, raw in enumerate(claims_src, start=1):
            claim_key = str(raw.get("claim_key") or f"{node_key}:claim:{index}")
            leaf = parent_links[min(index - 1, len(parent_links) - 1)]
            source_key = f"{claim_key}:src:1"
            source_links.append(
                ExactSourceLink(
                    source_key=source_key,
                    claim_key=claim_key,
                    source_kind=SourceKind(leaf.source_kind),
                    hierarchy_build_id=leaf.hierarchy_build_id,
                    evidence_node_id=leaf.evidence_node_id,
                    chapter_id=leaf.chapter_id,
                    chapter_number=leaf.chapter_number,
                    source_start=leaf.source_start,
                    source_end=leaf.source_end,
                    content_hash=leaf.content_hash,
                    source_snapshot_hash=leaf.source_snapshot_hash,
                )
            )
            payload = dict(raw.get("payload") or raw)
            for drop in (
                "claim_key",
                "node_key",
                "uncertainty",
                "confidence",
                "visible_from_chapter",
                "source_keys",
                "payload",
            ):
                payload.pop(drop, None)
            claim_body = {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": payload,
                "uncertainty": raw.get("uncertainty", "uncertain"),
                "confidence": float(raw.get("confidence", 0.8)),
                "visible_from_chapter": int(
                    raw.get("visible_from_chapter", chapter_start)
                ),
                "source_keys": [source_key],
            }
            claims.append(
                MemoryClaim.model_validate_json(
                    json.dumps(claim_body, ensure_ascii=False, separators=(",", ":"))
                )
            )

    package = CandidatePackage(
        nodes=(global_node, *middle_nodes),
        claims=tuple(claims),
        edges=edges,
        source_links=tuple(source_links),
    )
    assert_no_forbidden_keys(package.model_dump(mode="json"))
    return package


def artifact_checksum_for_package(package: CandidatePackage) -> str:
    return sha256(
        package.model_dump_json(exclude_none=False).encode("utf-8")
    ).hexdigest()


def default_optional_signal(
    *,
    source_kind: str,
    status: SourceStatus = SourceStatus.HEALTHY_EMPTY,
    reason_code: str | None = None,
) -> OptionalSourceSignal:
    return OptionalSourceSignal(
        source_kind=source_kind,  # type: ignore[arg-type]
        status=status,
        reason_code=reason_code,
        signal_keys=(),
        lineage={},
    )
