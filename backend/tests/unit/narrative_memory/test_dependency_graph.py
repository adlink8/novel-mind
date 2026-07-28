"""Unit tests for Phase 16 dependency graph and rebuild contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.rebuild_contracts import (
    AssetKind,
    EdgeKind,
    FORBIDDEN_IDENTITY_FIELDS,
    GraphEdge,
    GraphVertex,
    DependencyGraph,
    OraclePolicy,
    ReasonCode,
    RebuildDecision,
    RebuildItemDecision,
    RebuildPlanSpec,
    CompatibilityPolicy,
    assert_no_forbidden_identity,
    stable_checksum,
    stage_key_for_asset,
)
from app.services.narrative_memory.dependency_graph import (
    build_dependency_graph,
    chapter_evidence_fingerprint,
    evidence_fingerprint_from_link,
    graph_has_provider_capability,
)
from app.services.narrative_memory.rebuild_contracts import EvidenceFingerprint

pytestmark = pytest.mark.unit

HEX = "a" * 64


def _node(**kwargs):
    defaults = dict(
        id=1,
        node_key="chapter_state:10",
        node_kind="chapter_state",
        chapter_start=1,
        chapter_end=1,
        schema_version="v1",
        content_checksum=HEX,
        model_lineage_checksum=HEX,
        display_label="Ch1",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _claim(**kwargs):
    defaults = dict(id=1, claim_key="c1", node_id=1)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _link(**kwargs):
    defaults = dict(
        claim_id=1,
        chapter_id=10,
        chapter_number=1,
        source_start=0,
        source_end=5,
        content_hash=HEX,
        source_kind="hierarchy",
        optional_source_ref=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _parent_snapshot(*, nodes=None, claims=None, edges=None, links=None, plan=None):
    plan = plan or {
        "source_kind": "explicit_volume",
        "chapter_min": 1,
        "chapter_max": 3,
        "chapter_to_parent": {
            "1": "story_arc:1-2",
            "2": "story_arc:1-2",
            "3": "story_arc:3-3",
        },
        "parent_to_global": {
            "story_arc:1-2": "global_story:book",
            "story_arc:3-3": "global_story:book",
        },
    }
    nodes = nodes or [
        _node(id=1, node_key="chapter_state:10", chapter_start=1, chapter_end=1),
        _node(
            id=2,
            node_key="chapter_state:20",
            chapter_start=2,
            chapter_end=2,
            content_checksum="b" * 64,
        ),
        _node(
            id=3,
            node_key="story_arc:1-2",
            node_kind="story_arc",
            chapter_start=1,
            chapter_end=2,
            content_checksum="c" * 64,
        ),
        _node(
            id=4,
            node_key="global_story:book",
            node_kind="global_story",
            chapter_start=1,
            chapter_end=3,
            content_checksum="d" * 64,
        ),
    ]
    claims = claims or [
        _claim(id=1, claim_key="c1", node_id=1),
        _claim(id=2, claim_key="c2", node_id=2),
    ]
    links = links or [
        _link(claim_id=1, chapter_id=10, chapter_number=1),
        _link(
            claim_id=2,
            chapter_id=20,
            chapter_number=2,
            source_start=0,
            source_end=4,
            content_hash="e" * 64,
        ),
    ]
    edges = edges or []
    return SimpleNamespace(
        version=SimpleNamespace(id=1),
        nodes=tuple(nodes),
        claims=tuple(claims),
        edges=tuple(edges),
        source_links=tuple(links),
        manifest=SimpleNamespace(manifest_checksum=HEX),
        validation_report=None,
        boundary_plan=plan,
        boundary_plan_checksum=stable_checksum(plan),
    )


def test_graph_checksum_stable_under_insertion_order() -> None:
    parent_a = _parent_snapshot()
    g1 = build_dependency_graph(parent_a)
    # reverse nodes/links order
    parent_b = _parent_snapshot(
        nodes=list(reversed(parent_a.nodes)),
        links=list(reversed(parent_a.source_links)),
        claims=list(reversed(parent_a.claims)),
    )
    g2 = build_dependency_graph(parent_b)
    assert g1.graph_checksum == g2.graph_checksum
    assert [v.asset_key for v in g1.vertices] == [v.asset_key for v in g2.vertices]


def test_graph_excludes_db_ids_from_vertex_attributes_identity() -> None:
    parent = _parent_snapshot()
    g = build_dependency_graph(parent)
    body = {
        "vertices": [v.model_dump(mode="json") for v in g.vertices],
        "edges": [e.model_dump(mode="json") for e in g.edges],
    }
    # Asset keys are semantic; graph checksum is independent of ORM ids
    assert g.graph_checksum == stable_checksum(body)
    for v in g.vertices:
        assert "id" not in v.attributes
        assert "display_label" not in v.attributes


def test_forbidden_identity_fields_rejected() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        assert_no_forbidden_identity({"embedding": "x", "node_key": "ok"})
    assert FORBIDDEN_IDENTITY_FIELDS.issuperset({"embedding", "score", "promotion"})


def test_evidence_fingerprint_order_independent() -> None:
    fps = [
        EvidenceFingerprint(
            chapter_id=1,
            chapter_number=1,
            source_start=0,
            source_end=3,
            content_hash=HEX,
        ),
        EvidenceFingerprint(
            chapter_id=1,
            chapter_number=1,
            source_start=3,
            source_end=6,
            content_hash="b" * 64,
        ),
    ]
    a = chapter_evidence_fingerprint(fps)
    b = chapter_evidence_fingerprint(list(reversed(fps)))
    assert a == b


def test_dependency_graph_has_expected_edge_kinds() -> None:
    g = build_dependency_graph(_parent_snapshot())
    kinds = {e.edge_kind for e in g.edges}
    assert EdgeKind.SOURCE_TO_CHAPTER_STATE in kinds
    assert EdgeKind.EVIDENCE_TO_CHAPTER_STATE in kinds
    assert EdgeKind.CHAPTER_TO_PARENT in kinds or EdgeKind.BOUNDARY_TO_PARENT in kinds
    assert any(v.asset_kind == AssetKind.BOUNDARY_PLAN for v in g.vertices)
    assert any(v.asset_kind == AssetKind.GLOBAL_STORY for v in g.vertices)


def test_stage_key_mapping() -> None:
    assert stage_key_for_asset(AssetKind.CHAPTER_STATE, "chapter_state:99") == (
        "chapter_state:99"
    )
    assert stage_key_for_asset(AssetKind.GLOBAL_STORY, "global_story:book") == (
        "global_story:book"
    )
    assert stage_key_for_asset(AssetKind.SOURCE_CHAPTER, "source_chapter:1") is None


def test_plan_checksum_byte_stable() -> None:
    policy = OraclePolicy()
    compat = CompatibilityPolicy(schema_hash=HEX, policy_hash=HEX)
    item = RebuildItemDecision(
        asset_key="chapter_state:1",
        asset_kind=AssetKind.CHAPTER_STATE,
        decision=RebuildDecision.DIRTY,
        direct_reasons=(ReasonCode.CHAPTER_EDITED,),
        propagated_reasons=(),
        predecessor_keys=("source_chapter:1",),
        chapter_start=1,
        chapter_end=1,
        stage_key="chapter_state:1",
    )
    plan = RebuildPlanSpec(
        owner_id=1,
        novel_id=2,
        parent_version_id=3,
        target_version_id=4,
        old_source_snapshot_hash=HEX,
        new_source_snapshot_hash="b" * 64,
        old_hierarchy_build_id="h1",
        new_hierarchy_build_id="h2",
        old_hierarchy_checksum=HEX,
        new_hierarchy_checksum="b" * 64,
        boundary_plan={"chapter_min": 1, "chapter_max": 1},
        boundary_plan_checksum=HEX,
        oracle_policy=policy,
        compatibility_policy=compat,
        eligibility_report_checksum=HEX,
        graph_checksum=HEX,
        items=(item,),
        change_summary={"dirty_count": 1},
    )
    assert plan.plan_checksum() == plan.plan_checksum()
    assert len(plan.plan_checksum()) == 64


def test_no_provider_capability() -> None:
    assert graph_has_provider_capability() is False


def test_duplicate_edges_collapsed() -> None:
    v = [
        GraphVertex(asset_key="a", asset_kind=AssetKind.SOURCE_CHAPTER),
        GraphVertex(asset_key="b", asset_kind=AssetKind.CHAPTER_STATE),
    ]
    e = [
        GraphEdge(
            edge_kind=EdgeKind.SOURCE_TO_CHAPTER_STATE,
            source_key="a",
            target_key="b",
        ),
        GraphEdge(
            edge_kind=EdgeKind.SOURCE_TO_CHAPTER_STATE,
            source_key="a",
            target_key="b",
        ),
    ]
    g = DependencyGraph.from_parts(v, e)
    assert len(g.edges) == 1


def test_evidence_fingerprint_from_link_uses_offsets() -> None:
    link = _link(source_start=2, source_end=9, content_hash="f" * 64)
    fp = evidence_fingerprint_from_link(link)
    assert fp.source_start == 2
    assert fp.fingerprint() != HEX
