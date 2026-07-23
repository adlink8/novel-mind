"""Unit tests for NM read-only structure query (Phase 20-01)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.narrative_memory.structure_query import (
    PUBLICATION_STATUS,
    VersionNotFoundError,
    assemble_structure_nodes,
    claim_summary_text,
    compute_readiness,
    filter_claims_by_cutoff,
    filter_nodes_by_cutoff,
    list_versions,
    load_node_claims,
    load_structure_tree,
    resolve_through_chapter,
)


pytestmark = pytest.mark.unit


def _node(
    id_: int,
    key: str,
    kind: str,
    start: int,
    end: int,
    label: str | None = None,
):
    return SimpleNamespace(
        id=id_,
        node_key=key,
        node_kind=kind,
        chapter_start=start,
        chapter_end=end,
        display_label=label,
    )


def _edge(src: int, tgt: int, edge_type: str = "contains"):
    return SimpleNamespace(source_node_id=src, target_node_id=tgt, edge_type=edge_type)


def _claim(
    id_: int,
    node_id: int,
    kind: str = "event_fact",
    vis: int = 1,
    payload: dict | None = None,
    uncertainty: str = "likely",
    confidence: float = 0.8,
):
    return SimpleNamespace(
        id=id_,
        node_id=node_id,
        claim_kind=kind,
        typed_payload=payload or {"event_kind": "arrival"},
        uncertainty=uncertainty,
        confidence=confidence,
        visible_from_chapter=vis,
    )


# ---------------------------------------------------------------------------
# Pure assembly
# ---------------------------------------------------------------------------


def test_no_versions_empty_list_via_assembly():
    """Empty version list is honest — no invented active pointer."""

    # Pure shape: readiness empty when zero nodes
    assert compute_readiness(total_nodes=0) == "empty"
    assert PUBLICATION_STATUS == "candidate_preview"


def test_cutoff_filters_nodes():
    nodes = [
        _node(1, "cs-1", "chapter_state", 1, 1),
        _node(2, "cs-2", "chapter_state", 2, 2),
        _node(3, "arc-1", "story_arc", 1, 5),
        _node(4, "global", "global_story", 1, 10),
    ]
    visible = filter_nodes_by_cutoff(nodes, through_chapter=2)
    ids = {n.id for n in visible}
    assert ids == {1, 2}
    assert 3 not in ids
    assert 4 not in ids


def test_multi_chapter_tree_assembly_cutoff_safe():
    """Multi-chapter arc/global still drop nodes past through_chapter after assembly."""
    nodes = [
        _node(1, "cs-1", "chapter_state", 1, 1, "Ch1"),
        _node(2, "cs-2", "chapter_state", 2, 2, "Ch2"),
        _node(3, "cs-5", "chapter_state", 5, 5, "Ch5"),
        _node(10, "arc-early", "story_arc", 1, 2, "Early arc"),
        _node(11, "arc-late", "story_arc", 3, 5, "Late arc"),
        _node(20, "global", "global_story", 1, 5, "Global"),
    ]
    edges = [
        _edge(20, 10),
        _edge(20, 11),
        _edge(10, 1),
        _edge(10, 2),
        _edge(11, 3),
    ]
    # At through=2: only ch1/ch2 + early arc; late arc/global end past cutoff.
    visible = filter_nodes_by_cutoff(nodes, through_chapter=2)
    product = assemble_structure_nodes(visible, edges)
    ids = {n.id for n in product}
    assert ids == {1, 2, 10}
    assert 3 not in ids
    assert 11 not in ids
    assert 20 not in ids
    by_id = {n.id: n for n in product}
    # Orphan edges to filtered parents drop; early arc keeps visible children
    assert set(by_id[10].child_ids) == {1, 2}
    # Raising cutoff admits multi-chapter parents
    visible5 = filter_nodes_by_cutoff(nodes, through_chapter=5)
    product5 = assemble_structure_nodes(visible5, edges)
    ids5 = {n.id for n in product5}
    assert ids5 == {1, 2, 3, 10, 11, 20}
    by5 = {n.id: n for n in product5}
    assert set(by5[20].child_ids) == {10, 11}


def test_tree_assembly_child_ids_and_order():
    nodes = [
        _node(10, "cs-1", "chapter_state", 1, 1, "Ch1"),
        _node(20, "arc-1", "story_arc", 1, 2, "Arc"),
        _node(30, "g", "global_story", 1, 2, "Global"),
    ]
    edges = [_edge(30, 20), _edge(20, 10)]
    product = assemble_structure_nodes(nodes, edges)
    kinds = [n.node_kind for n in product]
    assert kinds[0] == "global_story"
    assert kinds[1] == "story_arc"
    assert kinds[2] == "chapter_state"
    by_id = {n.id: n for n in product}
    assert by_id[30].child_ids == [20]
    assert by_id[20].child_ids == [10]
    assert by_id[10].child_ids == []


def test_candidate_preview_always_on_product_nodes():
    nodes = assemble_structure_nodes([_node(1, "cs-1", "chapter_state", 1, 1)], [])
    assert PUBLICATION_STATUS == "candidate_preview"
    # tree response field is fixed at construction sites
    from app.schemas.narrative_memory_product import NmStructureTreeResponse

    resp = NmStructureTreeResponse(
        novel_id=1,
        version_id=2,
        through_chapter=3,
        readiness="incomplete",
        nodes=nodes,
    )
    assert resp.publication_status == "candidate_preview"


def test_claims_cutoff():
    claims = [
        _claim(1, 10, vis=1),
        _claim(2, 10, vis=3),
        _claim(3, 10, vis=5),
    ]
    visible = filter_claims_by_cutoff(claims, through_chapter=3)
    assert [c.id for c in visible] == [1, 2]


def test_claim_summary_from_payload():
    assert claim_summary_text({"summary": "  hero leaves  "}) == "hero leaves"
    assert (
        claim_summary_text({"outcome": {"value_kind": "text", "value": "ok"}}) == "ok"
    )
    assert claim_summary_text({"event_kind": "battle"}) == "battle"


def test_readiness_sealed_and_incomplete():
    assert (
        compute_readiness(
            total_nodes=5,
            has_manifest=True,
            validation_verdict="qualified_candidate",
        )
        == "sealed_candidate"
    )
    assert compute_readiness(total_nodes=1, has_manifest=False) == "incomplete"
    assert (
        compute_readiness(
            total_nodes=4,
            has_manifest=False,
            node_counts_by_kind={"chapter_state": 2, "story_arc": 1},
        )
        == "preview_eligible"
    )


def test_resolve_through_chapter_clamp_and_reject():
    assert resolve_through_chapter(None, novel_chapter_count=12) == 12
    assert resolve_through_chapter(99, novel_chapter_count=12) == 12
    assert resolve_through_chapter(5, novel_chapter_count=12) == 5
    with pytest.raises(Exception) as ei:
        resolve_through_chapter(0, novel_chapter_count=10)
    assert ei.value.status_code == 400  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Async service with mocked session
# ---------------------------------------------------------------------------


def _scalar_side_effect(mapping):
    """Build AsyncMock.scalar that returns by sequential SQL type heuristics."""

    async def _scalar(stmt):
        # Rough: inspect entity from statement string
        text = str(stmt)
        for key, value in mapping.items():
            if key in text:
                return value
        return None

    return _scalar


@pytest.mark.asyncio
async def test_list_versions_empty():
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    session.scalars = AsyncMock(return_value=scalars_result)

    resp = await list_versions(session, owner_id=1, novel_id=9)
    assert resp.versions == []
    assert resp.publication_status == "candidate_preview"
    assert resp.message is not None
    assert "no narrative memory" in resp.message


@pytest.mark.asyncio
async def test_load_tree_foreign_version_not_found():
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)

    with pytest.raises(VersionNotFoundError):
        await load_structure_tree(
            session,
            owner_id=1,
            novel_id=2,
            version_id=999,
            through_chapter=3,
        )


@pytest.mark.asyncio
async def test_load_tree_cutoff_and_preview_badge():
    version = SimpleNamespace(id=5, version_key="v1", owner_id=1, novel_id=2)
    nodes = [
        _node(1, "cs-1", "chapter_state", 1, 1, "Ch1"),
        _node(2, "cs-5", "chapter_state", 5, 5, "Ch5"),
        _node(3, "arc", "story_arc", 1, 5, "Arc"),
    ]
    edges = [_edge(3, 1), _edge(3, 2)]

    call_n = {"scalar": 0, "scalars": 0}

    async def scalar(stmt):
        call_n["scalar"] += 1
        text = str(stmt)
        if "narrative_memory_versions" in text or "NarrativeMemoryVersion" in text:
            return version
        if "narrative_memory_manifests" in text or "NarrativeMemoryManifest" in text:
            return None
        if (
            "narrative_memory_validation_reports" in text
            or "NarrativeMemoryValidationReport" in text
        ):
            return None
        return None

    async def scalars(stmt):
        call_n["scalars"] += 1
        text = str(stmt)
        result = MagicMock()
        if "narrative_memory_nodes" in text or "NarrativeMemoryNode" in text:
            result.all.return_value = nodes
        elif "narrative_memory_edges" in text or "NarrativeMemoryEdge" in text:
            result.all.return_value = edges
        else:
            result.all.return_value = []
        return result

    session = AsyncMock()
    session.scalar = scalar
    session.scalars = scalars

    resp = await load_structure_tree(
        session,
        owner_id=1,
        novel_id=2,
        version_id=5,
        through_chapter=1,
    )
    assert resp.publication_status == "candidate_preview"
    assert resp.version_id == 5
    assert resp.through_chapter == 1
    # only chapter_state ending at 1; arc ends at 5 → filtered
    assert [n.id for n in resp.nodes] == [1]
    assert resp.nodes[0].node_kind == "chapter_state"


@pytest.mark.asyncio
async def test_load_node_claims_cutoff():
    version = SimpleNamespace(id=5, version_key="v1")
    node = _node(10, "cs-1", "chapter_state", 1, 1)
    claims = [
        _claim(1, 10, vis=1, payload={"summary": "early"}),
        _claim(2, 10, vis=4, payload={"summary": "late"}),
    ]

    async def scalar(stmt):
        text = str(stmt)
        if "narrative_memory_versions" in text or "NarrativeMemoryVersion" in text:
            return version
        if "narrative_memory_nodes" in text or "NarrativeMemoryNode" in text:
            return node
        return None

    async def scalars(stmt):
        # Service applies SQL filter visible_from_chapter <= through_chapter;
        # mock returns only what SQL would return at cutoff 2.
        result = MagicMock()
        result.all.return_value = [c for c in claims if c.visible_from_chapter <= 2]
        return result

    session = AsyncMock()
    session.scalar = scalar
    session.scalars = scalars

    resp = await load_node_claims(
        session,
        owner_id=1,
        novel_id=2,
        version_id=5,
        node_id=10,
        through_chapter=2,
    )
    assert resp.publication_status == "candidate_preview"
    assert [c.id for c in resp.claims] == [1]
    assert resp.claims[0].summary == "early"
