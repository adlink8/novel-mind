"""Pure structural provenance validation for candidate memory graphs."""

from __future__ import annotations

import pytest

from app.services.narrative_memory.provenance import (
    GraphClaimView,
    GraphEdgeView,
    GraphLinkView,
    GraphNodeView,
    StructuralReason,
    validate_memory_graph,
)


pytestmark = pytest.mark.unit


def _legal_graph() -> tuple[
    tuple[GraphNodeView, ...],
    tuple[GraphEdgeView, ...],
    tuple[GraphClaimView, ...],
    tuple[GraphLinkView, ...],
]:
    nodes = (
        GraphNodeView("global", "global_story", 1, 2),
        GraphNodeView("arc:1", "story_arc", 1, 2),
        GraphNodeView("chapter:1", "chapter_state", 1, 1),
        GraphNodeView("chapter:2", "chapter_state", 2, 2),
    )
    edges = (
        GraphEdgeView("contains", "global", "arc:1"),
        GraphEdgeView("contains", "arc:1", "chapter:1"),
        GraphEdgeView("contains", "arc:1", "chapter:2"),
    )
    claims = (
        GraphClaimView("claim:1", "chapter:1"),
        GraphClaimView("claim:2", "chapter:2"),
        GraphClaimView("claim:g", "global"),
    )
    links = (
        GraphLinkView("claim:1", "source:1"),
        GraphLinkView("claim:2", "source:2"),
        GraphLinkView("claim:g", "source:g"),
    )
    return nodes, edges, claims, links


def test_legal_continuous_dag_with_direct_leaf_closure_passes() -> None:
    nodes, edges, claims, links = _legal_graph()
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=links,
        expected_chapter_min=1,
        expected_chapter_max=2,
    )
    assert result.ok is True
    assert result.reason_codes == ()
    assert result.verdict == "qualified_candidate"
    assert result.observed_counts["claims"] == 3
    assert result.observed_counts["source_links"] == 3


def test_missing_claim_source_fails_even_with_graph_ancestry() -> None:
    nodes, edges, claims, links = _legal_graph()
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=links[:-1],
        expected_chapter_min=1,
        expected_chapter_max=2,
    )
    assert result.ok is False
    assert StructuralReason.MISSING_CLAIM_SOURCE.value in result.reason_codes


def test_global_bypass_to_chapter_state_fails() -> None:
    nodes, edges, claims, links = _legal_graph()
    edges = edges + (GraphEdgeView("contains", "global", "chapter:1"),)
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=links,
        expected_chapter_min=1,
        expected_chapter_max=2,
    )
    assert StructuralReason.GLOBAL_BYPASS.value in result.reason_codes
    assert StructuralReason.ILLEGAL_TRANSITION.value in result.reason_codes


def test_cycle_and_range_containment_fail() -> None:
    nodes = (
        GraphNodeView("global", "global_story", 1, 2),
        GraphNodeView("arc:1", "story_arc", 1, 2),
        GraphNodeView("chapter:1", "chapter_state", 1, 1),
    )
    edges = (
        GraphEdgeView("contains", "global", "arc:1"),
        GraphEdgeView("contains", "arc:1", "chapter:1"),
        GraphEdgeView("derives_from", "chapter:1", "global"),
    )
    claims = (GraphClaimView("claim:1", "chapter:1"),)
    links = (GraphLinkView("claim:1", "source:1"),)
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=links,
        expected_chapter_min=1,
        expected_chapter_max=1,
    )
    assert StructuralReason.CYCLE_DETECTED.value in result.reason_codes
    assert StructuralReason.ILLEGAL_TRANSITION.value in result.reason_codes


def test_middle_gap_and_overlap_fail() -> None:
    nodes = (
        GraphNodeView("global", "global_story", 1, 4),
        GraphNodeView("arc:1", "story_arc", 1, 1),
        GraphNodeView("arc:2", "story_arc", 3, 4),
        GraphNodeView("chapter:1", "chapter_state", 1, 1),
        GraphNodeView("chapter:3", "chapter_state", 3, 3),
        GraphNodeView("chapter:4", "chapter_state", 4, 4),
    )
    edges = (
        GraphEdgeView("contains", "global", "arc:1"),
        GraphEdgeView("contains", "global", "arc:2"),
        GraphEdgeView("contains", "arc:1", "chapter:1"),
        GraphEdgeView("contains", "arc:2", "chapter:3"),
        GraphEdgeView("contains", "arc:2", "chapter:4"),
    )
    claims = tuple(
        GraphClaimView(f"claim:{key}", key)
        for key in ("chapter:1", "chapter:3", "chapter:4")
    )
    links = tuple(
        GraphLinkView(claim.claim_key, f"s:{claim.claim_key}") for claim in claims
    )
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=links,
        expected_chapter_min=1,
        expected_chapter_max=4,
    )
    assert StructuralReason.MIDDLE_RANGE_GAP.value in result.reason_codes
    assert StructuralReason.CHAPTER_STATE_GAP.value in result.reason_codes

    overlap_nodes = (
        GraphNodeView("global", "global_story", 1, 3),
        GraphNodeView("arc:1", "story_arc", 1, 2),
        GraphNodeView("arc:2", "story_arc", 2, 3),
        GraphNodeView("chapter:1", "chapter_state", 1, 1),
        GraphNodeView("chapter:2", "chapter_state", 2, 2),
        GraphNodeView("chapter:3", "chapter_state", 3, 3),
    )
    overlap_edges = (
        GraphEdgeView("contains", "global", "arc:1"),
        GraphEdgeView("contains", "global", "arc:2"),
        GraphEdgeView("contains", "arc:1", "chapter:1"),
        GraphEdgeView("contains", "arc:1", "chapter:2"),
        GraphEdgeView("contains", "arc:2", "chapter:2"),
        GraphEdgeView("contains", "arc:2", "chapter:3"),
    )
    claims = tuple(
        GraphClaimView(f"c:{key}", key)
        for key in ("chapter:1", "chapter:2", "chapter:3")
    )
    links = tuple(
        GraphLinkView(claim.claim_key, f"s:{claim.claim_key}") for claim in claims
    )
    overlap = validate_memory_graph(
        nodes=overlap_nodes,
        edges=overlap_edges,
        claims=claims,
        source_links=links,
        expected_chapter_min=1,
        expected_chapter_max=3,
    )
    assert StructuralReason.MIDDLE_RANGE_OVERLAP.value in overlap.reason_codes


def test_reason_codes_are_sorted_and_stable() -> None:
    nodes = (
        GraphNodeView("global", "global_story", 1, 1),
        GraphNodeView("chapter:1", "chapter_state", 1, 1),
    )
    edges = (GraphEdgeView("contains", "global", "chapter:1"),)
    claims = (GraphClaimView("claim:1", "chapter:1"),)
    result = validate_memory_graph(
        nodes=nodes,
        edges=edges,
        claims=claims,
        source_links=(),
        expected_chapter_min=1,
        expected_chapter_max=1,
    )
    assert result.reason_codes == tuple(sorted(result.reason_codes))
    assert StructuralReason.GLOBAL_BYPASS.value in result.reason_codes
    assert StructuralReason.MISSING_CLAIM_SOURCE.value in result.reason_codes
