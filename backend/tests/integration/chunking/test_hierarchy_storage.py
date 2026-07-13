"""07-04 hierarchy storage (in-memory contract) tests."""
from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.hierarchy import build_chapter_hierarchy

pytestmark = pytest.mark.integration

def test_persist_hierarchy_in_build_store():
    store = InMemoryBuildStore()
    chapters = [{"chapter_id": 1, "chapter_number": 1, "content": "存储层级测试。" * 40}]
    rec = create_candidate_build(
        store,
        novel_id=9,
        chapters=chapters,
        source_snapshot_hash="b" * 64,
        force_full=True,
    )
    assert rec.is_candidate is True
    assert store.active.get(9) is None  # never moves active
    trees = store.hierarchies[rec.build_id]
    assert trees
    assert any(n.level == "evidence" for t in trees for n in t.nodes)
    assert store.vector_ids[rec.build_id]
