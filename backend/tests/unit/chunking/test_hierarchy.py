"""07-04 hierarchy unit tests."""

from __future__ import annotations
import pytest
from app.services.chunking.hierarchy import (
    build_chapter_hierarchy,
    expand_evidence_to_scene,
    validate_hierarchy_invariants,
)

pytestmark = pytest.mark.unit


def test_tree_invariants_and_rebuild():
    text = "场景开头。" * 20 + "中间发展。" * 20 + "结局收束。" * 20
    tree = build_chapter_hierarchy(
        novel_id=1, chapter_id=10, chapter_number=1, content=text
    )
    validate_hierarchy_invariants(tree.nodes, chapter_id=10)
    chapters = [n for n in tree.nodes if n.level == "chapter"]
    scenes = [n for n in tree.nodes if n.level == "scene"]
    evidence = [n for n in tree.nodes if n.level == "evidence"]
    assert len(chapters) == 1
    assert scenes
    assert evidence
    for ev in evidence:
        assert ev.parent_id is not None
        parent = next(n for n in tree.nodes if n.node_id == ev.parent_id)
        assert parent.level == "scene"


def test_deterministic_tree_checksum():
    text = "确定性层级。" * 30
    a = build_chapter_hierarchy(
        novel_id=2, chapter_id=1, chapter_number=1, content=text
    )
    b = build_chapter_hierarchy(
        novel_id=2, chapter_id=1, chapter_number=1, content=text
    )
    assert a.tree_checksum == b.tree_checksum
    assert [n.node_id for n in a.nodes] == [n.node_id for n in b.nodes]


def test_scene_expand_and_fallback():
    text = "证据段落甲。" * 15 + "证据段落乙。" * 15
    tree = build_chapter_hierarchy(
        novel_id=3, chapter_id=5, chapter_number=2, content=text
    )
    ev = next(n for n in tree.nodes if n.level == "evidence")
    exp = expand_evidence_to_scene(tree, ev.node_id)
    assert exp["mode"] in ("scene_expand", "evidence_truncated")
    assert "citation" in exp
    missing = expand_evidence_to_scene(tree, "hn_missing_id_xx")
    assert missing["mode"] == "raw_fallback"
