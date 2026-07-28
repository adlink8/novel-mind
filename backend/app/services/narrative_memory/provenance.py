"""Structural provenance validation for candidate narrative memory.

Pure graph/range/source-closure checks only. Does not promote, select
production versions, call providers, or emit Phase 17 quality verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping, Sequence

from app.services.narrative_memory.contracts import MEMORY_NODE_ADAPTER, NodeKind


VALIDATOR_VERSION = "memory-provenance.v1"
STRUCTURAL_POLICY_VERSION = "memory-structural-policy.v1"

MIDDLE_KINDS = frozenset({NodeKind.STORY_ARC.value, NodeKind.VOLUME.value})
ALLOWED_TRANSITIONS = frozenset(
    {
        (NodeKind.GLOBAL_STORY.value, NodeKind.STORY_ARC.value),
        (NodeKind.GLOBAL_STORY.value, NodeKind.VOLUME.value),
        (NodeKind.STORY_ARC.value, NodeKind.CHAPTER_STATE.value),
        (NodeKind.VOLUME.value, NodeKind.CHAPTER_STATE.value),
    }
)
KNOWN_NODE_KINDS = frozenset(kind.value for kind in NodeKind)
KNOWN_EDGE_TYPES = frozenset({"contains", "derives_from"})


class StructuralReason(StrEnum):
    UNKNOWN_NODE_KIND = "unknown_node_kind"
    UNKNOWN_EDGE_TYPE = "unknown_edge_type"
    ILLEGAL_TRANSITION = "illegal_transition"
    RANGE_NOT_CONTAINED = "range_not_contained"
    CYCLE_DETECTED = "cycle_detected"
    CHAPTER_STATE_GAP = "chapter_state_gap"
    CHAPTER_STATE_OVERLAP = "chapter_state_overlap"
    MIDDLE_RANGE_GAP = "middle_range_gap"
    MIDDLE_RANGE_OVERLAP = "middle_range_overlap"
    GLOBAL_RANGE_MISMATCH = "global_range_mismatch"
    GLOBAL_BYPASS = "global_bypass"
    MISSING_CLAIM_SOURCE = "missing_claim_source"
    DUPLICATE_NODE_KEY = "duplicate_node_key"
    DUPLICATE_CLAIM_KEY = "duplicate_claim_key"
    ORPHAN_EDGE_ENDPOINT = "orphan_edge_endpoint"
    EMPTY_GRAPH = "empty_graph"


@dataclass(frozen=True)
class GraphNodeView:
    node_key: str
    node_kind: str
    chapter_start: int
    chapter_end: int


@dataclass(frozen=True)
class GraphEdgeView:
    edge_type: str
    source_node_key: str
    target_node_key: str


@dataclass(frozen=True)
class GraphClaimView:
    claim_key: str
    node_key: str


@dataclass(frozen=True)
class GraphLinkView:
    claim_key: str
    source_key: str


@dataclass(frozen=True)
class StructuralValidationResult:
    ok: bool
    reason_codes: tuple[str, ...]
    observed_counts: dict[str, int]

    @property
    def verdict(self) -> str:
        return "qualified_candidate" if self.ok else "blocked"


def validate_memory_graph(
    *,
    nodes: Sequence[GraphNodeView],
    edges: Sequence[GraphEdgeView],
    claims: Sequence[GraphClaimView],
    source_links: Sequence[GraphLinkView],
    expected_chapter_min: int | None = None,
    expected_chapter_max: int | None = None,
) -> StructuralValidationResult:
    """Deterministic pure structural validation for a candidate memory package."""

    reasons: set[str] = set()
    if not nodes:
        reasons.add(StructuralReason.EMPTY_GRAPH.value)

    nodes_by_key: dict[str, GraphNodeView] = {}
    for node in nodes:
        if node.node_key in nodes_by_key:
            reasons.add(StructuralReason.DUPLICATE_NODE_KEY.value)
        nodes_by_key[node.node_key] = node
        if node.node_kind not in KNOWN_NODE_KINDS:
            reasons.add(StructuralReason.UNKNOWN_NODE_KIND.value)
        if node.chapter_end < node.chapter_start:
            reasons.add(StructuralReason.RANGE_NOT_CONTAINED.value)
        if (
            node.node_kind == NodeKind.CHAPTER_STATE.value
            and node.chapter_start != node.chapter_end
        ):
            reasons.add(StructuralReason.CHAPTER_STATE_OVERLAP.value)

    claims_by_key: dict[str, GraphClaimView] = {}
    for claim in claims:
        if claim.claim_key in claims_by_key:
            reasons.add(StructuralReason.DUPLICATE_CLAIM_KEY.value)
        claims_by_key[claim.claim_key] = claim
        if claim.node_key not in nodes_by_key:
            reasons.add(StructuralReason.ORPHAN_EDGE_ENDPOINT.value)

    links_by_claim: dict[str, list[GraphLinkView]] = {}
    for link in source_links:
        links_by_claim.setdefault(link.claim_key, []).append(link)

    for claim in claims:
        if not links_by_claim.get(claim.claim_key):
            reasons.add(StructuralReason.MISSING_CLAIM_SOURCE.value)

    adjacency: dict[str, list[str]] = {key: [] for key in nodes_by_key}
    for edge in edges:
        if edge.edge_type not in KNOWN_EDGE_TYPES:
            reasons.add(StructuralReason.UNKNOWN_EDGE_TYPE.value)
        parent = nodes_by_key.get(edge.source_node_key)
        child = nodes_by_key.get(edge.target_node_key)
        if parent is None or child is None:
            reasons.add(StructuralReason.ORPHAN_EDGE_ENDPOINT.value)
            continue
        adjacency.setdefault(parent.node_key, []).append(child.node_key)
        if (
            parent.node_kind == NodeKind.GLOBAL_STORY.value
            and child.node_kind == NodeKind.CHAPTER_STATE.value
        ):
            reasons.add(StructuralReason.GLOBAL_BYPASS.value)
        transition = (parent.node_kind, child.node_kind)
        if transition not in ALLOWED_TRANSITIONS:
            reasons.add(StructuralReason.ILLEGAL_TRANSITION.value)
        if (
            parent.chapter_start > child.chapter_start
            or parent.chapter_end < child.chapter_end
        ):
            reasons.add(StructuralReason.RANGE_NOT_CONTAINED.value)

    if _has_cycle(adjacency):
        reasons.add(StructuralReason.CYCLE_DETECTED.value)

    chapter_states = sorted(
        (
            node
            for node in nodes_by_key.values()
            if node.node_kind == NodeKind.CHAPTER_STATE.value
        ),
        key=lambda node: (node.chapter_start, node.node_key),
    )
    if chapter_states:
        chapter_min = (
            expected_chapter_min
            if expected_chapter_min is not None
            else chapter_states[0].chapter_start
        )
        chapter_max = (
            expected_chapter_max
            if expected_chapter_max is not None
            else max(node.chapter_end for node in chapter_states)
        )
        covered: list[int] = []
        for node in chapter_states:
            if covered and node.chapter_start <= covered[-1]:
                reasons.add(StructuralReason.CHAPTER_STATE_OVERLAP.value)
            if covered and node.chapter_start > covered[-1] + 1:
                reasons.add(StructuralReason.CHAPTER_STATE_GAP.value)
            if not covered and node.chapter_start > chapter_min:
                reasons.add(StructuralReason.CHAPTER_STATE_GAP.value)
            covered.append(node.chapter_end)
        if covered and covered[-1] < chapter_max:
            reasons.add(StructuralReason.CHAPTER_STATE_GAP.value)
        if covered and chapter_states[0].chapter_start < chapter_min:
            reasons.add(StructuralReason.CHAPTER_STATE_OVERLAP.value)

    globals_ = [
        node
        for node in nodes_by_key.values()
        if node.node_kind == NodeKind.GLOBAL_STORY.value
    ]
    for global_node in globals_:
        if expected_chapter_min is not None and expected_chapter_max is not None:
            if (
                global_node.chapter_start != expected_chapter_min
                or global_node.chapter_end != expected_chapter_max
            ):
                reasons.add(StructuralReason.GLOBAL_RANGE_MISMATCH.value)
        children = [
            nodes_by_key[child_key]
            for child_key in adjacency.get(global_node.node_key, [])
            if child_key in nodes_by_key
            and nodes_by_key[child_key].node_kind in MIDDLE_KINDS
        ]
        children = sorted(
            children, key=lambda node: (node.chapter_start, node.node_key)
        )
        if children:
            cursor = global_node.chapter_start
            for index, child in enumerate(children):
                if child.chapter_start > cursor:
                    reasons.add(StructuralReason.MIDDLE_RANGE_GAP.value)
                if child.chapter_start < cursor and index > 0:
                    reasons.add(StructuralReason.MIDDLE_RANGE_OVERLAP.value)
                if (
                    child.chapter_start < global_node.chapter_start
                    or child.chapter_end > global_node.chapter_end
                ):
                    reasons.add(StructuralReason.RANGE_NOT_CONTAINED.value)
                if index > 0 and child.chapter_start <= children[index - 1].chapter_end:
                    reasons.add(StructuralReason.MIDDLE_RANGE_OVERLAP.value)
                cursor = child.chapter_end + 1
            if children[-1].chapter_end < global_node.chapter_end:
                reasons.add(StructuralReason.MIDDLE_RANGE_GAP.value)

    observed = {
        "nodes": len(nodes),
        "edges": len(edges),
        "claims": len(claims),
        "source_links": len(source_links),
        "chapter_states": len(chapter_states),
        "middle_nodes": sum(
            1 for node in nodes_by_key.values() if node.node_kind in MIDDLE_KINDS
        ),
        "global_nodes": len(globals_),
        "reason_count": len(reasons),
    }
    ordered = tuple(sorted(reasons))
    return StructuralValidationResult(
        ok=not ordered,
        reason_codes=ordered,
        observed_counts=observed,
    )


def _has_cycle(adjacency: Mapping[str, Sequence[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in adjacency.get(node, ()):
            if dfs(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(dfs(node) for node in adjacency)


def node_views_from_payloads(
    rows: Iterable[Mapping[str, object]],
) -> tuple[GraphNodeView, ...]:
    """Strictly parse node mappings into graph views for pure tests."""

    views: list[GraphNodeView] = []
    for row in rows:
        node = MEMORY_NODE_ADAPTER.validate_python(row)
        views.append(
            GraphNodeView(
                node_key=node.node_key,
                node_kind=node.node_kind.value,
                chapter_start=node.chapter_start,
                chapter_end=node.chapter_end,
            )
        )
    return tuple(views)
