"""Unit matrix for multi-level descent, fallback, and leaf dedupe."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.narrative_memory.descent import (
    ProposedLeaf,
    run_descent,
)
from app.services.narrative_memory.retrieval_contracts import (
    CutoffSnapshot,
    FallbackReasonCode,
    RetrievalBudgets,
    RetrievalScope,
    RouteDecision,
    RouteMode,
    RouteReasonCode,
    StartLevel,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
)


pytestmark = pytest.mark.unit

HEX = "a" * 64


def _scope(**kw) -> RetrievalScope:
    base = dict(
        owner_id=1,
        novel_id=2,
        version_id=3,
        source_snapshot_hash=HEX,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX,
        candidate_manifest_checksum=HEX,
        cutoff=CutoffSnapshot(
            through_chapter=2, full_book_authorized=False, snapshot_hash=HEX
        ),
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
        budgets=RetrievalBudgets(
            max_nodes=10, max_claims=10, max_leaves=5, max_depth=6, max_fanout=4
        ),
    )
    base.update(kw)
    return RetrievalScope(**base)  # type: ignore[arg-type]


def _route(mode: RouteMode) -> RouteDecision:
    levels = {
        RouteMode.LOCAL: (StartLevel.CHAPTER_STATE,),
        RouteMode.ARC: (StartLevel.STORY_ARC, StartLevel.VOLUME),
        RouteMode.GLOBAL: (StartLevel.GLOBAL_STORY,),
        RouteMode.MIXED: (
            StartLevel.CHAPTER_STATE,
            StartLevel.STORY_ARC,
            StartLevel.VOLUME,
        ),
    }
    return RouteDecision(
        mode=mode,
        start_levels=levels[mode],
        reason_codes=(RouteReasonCode.SAFE_DEFAULT,),
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )


def _node(id_: int, key: str, kind: str, start: int, end: int):
    return SimpleNamespace(
        id=id_,
        node_key=key,
        node_kind=kind,
        chapter_start=start,
        chapter_end=end,
    )


def _claim(id_: int, node_id: int, key: str, vis: int = 1):
    return SimpleNamespace(
        id=id_,
        node_id=node_id,
        claim_key=key,
        visible_from_chapter=vis,
    )


def _link(id_: int, claim_id: int, leaf: str, chapter: int = 1):
    return SimpleNamespace(
        id=id_,
        claim_id=claim_id,
        hierarchy_build_id="build-1",
        evidence_node_id=leaf,
        chapter_id=100 + chapter,
        chapter_number=chapter,
        source_start=0,
        source_end=4,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )


@pytest.mark.asyncio
async def test_local_route_uses_chapter_state_path(monkeypatch):
    from app.services.narrative_memory import descent as d

    ch = [_node(1, "ch1", "chapter_state", 1, 1)]
    claims = [_claim(10, 1, "c1")]
    links = [_link(20, 10, "leaf-1")]

    async def fake_nodes(session, scope, kinds=None, node_ids=None, limit=None):
        if kinds and StartLevel.CHAPTER_STATE in kinds:
            return ch, 0
        return [], 0

    async def fake_claims(session, scope, node_ids=None, claim_ids=None, limit=None):
        return claims, 0

    async def fake_links(session, scope, claim_ids=None, limit=None):
        return links, 0

    monkeypatch.setattr(d, "load_visible_nodes", fake_nodes)
    monkeypatch.setattr(d, "load_visible_claims", fake_claims)
    monkeypatch.setattr(d, "load_visible_source_links", fake_links)

    result = await run_descent(MagicMock(), _scope(), _route(RouteMode.LOCAL))
    assert result.proposed_leaves
    assert result.proposed_leaves[0].origin == "memory"
    assert any(s.level == "chapter_state" for s in result.traversal)
    assert not any(s.level == "global_story" for s in result.traversal)


@pytest.mark.asyncio
async def test_arc_route_descends_contains_edges(monkeypatch):
    from app.services.narrative_memory import descent as d

    arcs = [_node(2, "arc", "story_arc", 1, 2)]
    chapters = [_node(3, "ch1", "chapter_state", 1, 1)]
    claims = [_claim(11, 3, "c-arc")]
    links = [_link(21, 11, "leaf-a")]

    async def fake_nodes(session, scope, kinds=None, node_ids=None, limit=None):
        if kinds and StartLevel.STORY_ARC in kinds:
            return arcs, 0
        if node_ids == [3]:
            return chapters, 0
        if kinds and StartLevel.CHAPTER_STATE in kinds and node_ids is None:
            return [], 0
        if node_ids:
            return [n for n in chapters if n.id in node_ids], 0
        return [], 0

    async def fake_children(session, scope, parent_node_ids=None, child_kinds=None):
        assert parent_node_ids == [2]
        return chapters

    monkeypatch.setattr(d, "load_visible_nodes", fake_nodes)
    monkeypatch.setattr(d, "load_child_nodes_via_edges", fake_children)
    monkeypatch.setattr(
        d, "load_visible_claims", AsyncMock(return_value=(claims, 0))
    )
    monkeypatch.setattr(
        d, "load_visible_source_links", AsyncMock(return_value=(links, 0))
    )

    result = await run_descent(MagicMock(), _scope(), _route(RouteMode.ARC))
    assert result.proposed_leaves[0].evidence_node_id == "leaf-a"
    assert any(s.relation == "contains" for s in result.traversal)


@pytest.mark.asyncio
async def test_mixed_union_dedupes_leaves(monkeypatch):
    from app.services.narrative_memory import descent as d

    local = [_node(1, "ch1", "chapter_state", 1, 1)]
    upper = [_node(2, "arc", "story_arc", 1, 2)]
    # same chapter via upper edge
    from_upper = [_node(1, "ch1", "chapter_state", 1, 1)]
    claims = [_claim(10, 1, "c1")]
    # two links same identity
    link_a = _link(20, 10, "leaf-1")
    link_b = _link(21, 10, "leaf-1")
    link_b.id = 21

    async def fake_nodes(session, scope, kinds=None, node_ids=None, limit=None):
        if kinds == (StartLevel.CHAPTER_STATE,):
            return local, 0
        if kinds and StartLevel.STORY_ARC in kinds:
            return upper, 0
        return [], 0

    monkeypatch.setattr(d, "load_visible_nodes", fake_nodes)
    monkeypatch.setattr(
        d, "load_child_nodes_via_edges", AsyncMock(return_value=from_upper)
    )
    monkeypatch.setattr(
        d, "load_visible_claims", AsyncMock(return_value=(claims, 0))
    )
    monkeypatch.setattr(
        d, "load_visible_source_links", AsyncMock(return_value=([link_a, link_b], 0))
    )

    result = await run_descent(MagicMock(), _scope(), _route(RouteMode.MIXED))
    leaves = result.deduped_leaves()
    # same identity → one
    assert len(leaves) == 1


@pytest.mark.asyncio
async def test_upper_absent_collapses_to_raw_fallback(monkeypatch):
    from app.services.narrative_memory import descent as d

    async def empty_nodes(*a, **k):
        return [], 0

    raw = SimpleNamespace(
        build_id="build-1",
        node_id="raw-leaf",
        chapter_id=101,
        chapter_number=1,
        source_start=0,
        source_end=3,
        content_hash=HEX,
    )

    session = MagicMock()
    session.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [raw])
    )

    monkeypatch.setattr(d, "load_visible_nodes", empty_nodes)
    monkeypatch.setattr(
        d, "load_child_nodes_via_edges", AsyncMock(return_value=[])
    )

    result = await run_descent(session, _scope(), _route(RouteMode.LOCAL))
    assert result.fallback_reason is FallbackReasonCode.RAW_FALLBACK
    assert result.proposed_leaves
    assert result.proposed_leaves[0].origin == "raw_fallback"


@pytest.mark.asyncio
async def test_distinct_routes_produce_distinct_traversal_shapes(monkeypatch):
    from app.services.narrative_memory import descent as d

    ch = [_node(1, "ch1", "chapter_state", 1, 1)]
    arcs = [_node(2, "arc", "story_arc", 1, 2)]
    claims = [_claim(10, 1, "c1")]
    links = [_link(20, 10, "leaf-1")]

    async def fake_nodes(session, scope, kinds=None, node_ids=None, limit=None):
        if kinds and StartLevel.CHAPTER_STATE in kinds and (
            kinds == (StartLevel.CHAPTER_STATE,) or node_ids
        ):
            if node_ids:
                return [n for n in ch if n.id in node_ids], 0
            return ch, 0
        if kinds and StartLevel.STORY_ARC in kinds:
            return arcs, 0
        if kinds and StartLevel.GLOBAL_STORY in kinds:
            return [], 0
        return [], 0

    monkeypatch.setattr(d, "load_visible_nodes", fake_nodes)
    monkeypatch.setattr(
        d, "load_child_nodes_via_edges", AsyncMock(return_value=ch)
    )
    monkeypatch.setattr(
        d, "load_visible_claims", AsyncMock(return_value=(claims, 0))
    )
    monkeypatch.setattr(
        d, "load_visible_source_links", AsyncMock(return_value=(links, 0))
    )

    local = await run_descent(MagicMock(), _scope(), _route(RouteMode.LOCAL))
    arc = await run_descent(MagicMock(), _scope(), _route(RouteMode.ARC))
    local_levels = [s.level for s in local.traversal]
    arc_levels = [s.level for s in arc.traversal]
    assert local_levels != arc_levels
    assert "story_arc" in arc_levels or any(
        s.candidate_key.startswith("start:arc") for s in arc.traversal
    )


def test_proposed_leaf_identity_includes_offsets_and_hash():
    a = ProposedLeaf(
        hierarchy_build_id="b",
        evidence_node_id="e",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=2,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )
    b = ProposedLeaf(
        hierarchy_build_id="b",
        evidence_node_id="e",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=3,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )
    assert a.identity != b.identity
