"""07-06 A/B and release verifier tests."""
from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.eval import run_ab_qualification
from app.services.chunking.release_verifier import verify_and_qualify
from app.services.rag_fixture import stable_hash

pytestmark = pytest.mark.integration

def _pair(store, novel_id=1, snap=None):
    snap = snap or ("a" * 64)
    ch = [{"chapter_id": 1, "chapter_number": 1, "content": "资格评估章节。" * 35}]
    a = create_candidate_build(store, novel_id=novel_id, chapters=ch, source_snapshot_hash=snap, chunker_name="rule-baseline", force_full=True)
    store.active[novel_id] = a.build_id
    b = create_candidate_build(store, novel_id=novel_id, chapters=ch, source_snapshot_hash=snap, chunker_name="hierarchical-v1", parent_build_id=a.build_id, force_full=True)
    return a, b, snap

def test_ab_comparable_same_snapshot():
    store = InMemoryBuildStore()
    a, b, snap = _pair(store)
    policy = stable_hash({"p": 1})
    rep = run_ab_qualification(
        store,
        novel_id=1,
        source_snapshot_hash=snap,
        policy_hash=policy,
        baseline_build_id=a.build_id,
        candidate_build_id=b.build_id,
    )
    assert rep["quality_comparable"] is True
    assert store.active[1] == a.build_id

def test_ab_snapshot_mismatch_not_comparable():
    store = InMemoryBuildStore()
    a, b, snap = _pair(store)
    rep = run_ab_qualification(
        store,
        novel_id=1,
        source_snapshot_hash="f" * 64,
        policy_hash=stable_hash({"p": 2}),
        baseline_build_id=a.build_id,
        candidate_build_id=b.build_id,
    )
    assert rep["quality_comparable"] is False

def test_release_verifier_qualifies_and_rejects():
    store = InMemoryBuildStore()
    a, b, snap = _pair(store)
    policy = "p" * 64
    rep = run_ab_qualification(
        store,
        novel_id=1,
        source_snapshot_hash=snap,
        policy_hash=policy,
        baseline_build_id=a.build_id,
        candidate_build_id=b.build_id,
    )
    ev = verify_and_qualify(store, ab_report=rep, candidate_build_id=b.build_id, policy_hash=policy)
    assert ev.status == "qualified"
    assert store.active[1] == a.build_id
    bad = verify_and_qualify(
        store,
        ab_report={**rep, "quality_comparable": False, "reason": "x"},
        candidate_build_id=b.build_id,
        policy_hash=policy,
    )
    assert bad.status in ("rejected", "blocked")
