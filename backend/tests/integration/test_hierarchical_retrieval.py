"""07-04 hierarchical retrieval behavior."""

from __future__ import annotations
import pytest
from app.services.chunking.hierarchy import (
    build_chapter_hierarchy,
    expand_evidence_to_scene,
)

pytestmark = pytest.mark.integration


def test_evidence_hit_scene_expand_raw_fallback():
    tree = build_chapter_hierarchy(
        novel_id=1,
        chapter_id=1,
        chapter_number=1,
        content=("检索证据句。" * 12 + "\n") * 5,
    )
    evidence = [n for n in tree.nodes if n.level == "evidence"]
    assert evidence
    hit = expand_evidence_to_scene(tree, evidence[0].node_id, max_chars=5000)
    assert hit["mode"] == "scene_expand"
    assert hit["citation"]["source_start"] >= 0
    # invalid hierarchy path
    fb = expand_evidence_to_scene(tree, "nope_node_id_000", max_chars=100)
    assert fb["mode"] == "raw_fallback"
