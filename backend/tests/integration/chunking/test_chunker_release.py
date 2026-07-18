"""07-06 end-to-end release path (frozen)."""
from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.eval import run_ab_qualification
from app.services.chunking.promotion import commit_promotion, prepare_promotion
from app.services.chunking.release_verifier import verify_and_qualify

pytestmark = pytest.mark.integration

def test_ab_to_verifier_to_promotion():
    store = InMemoryBuildStore()
    snap = "c" * 64
    policy = "d" * 64
    ch = [{"chapter_id": 1, "chapter_number": 1, "content": "发布路径。" * 40}]
    a = create_candidate_build(store, novel_id=7, chapters=ch, source_snapshot_hash=snap, chunker_name="rule-baseline", force_full=True)
    store.active[7] = a.build_id
    b = create_candidate_build(store, novel_id=7, chapters=ch, source_snapshot_hash=snap, chunker_name="hierarchical-v1", parent_build_id=a.build_id, force_full=True)
    rep = run_ab_qualification(store, novel_id=7, source_snapshot_hash=snap, policy_hash=policy, baseline_build_id=a.build_id, candidate_build_id=b.build_id)
    ev = verify_and_qualify(store, ab_report=rep, candidate_build_id=b.build_id, policy_hash=policy)
    assert ev.status == "qualified"
    prepare_promotion(store, build_id=b.build_id, evidence=ev)
    assert store.active[7] == a.build_id
    r = commit_promotion(store, build_id=b.build_id, evidence=ev)
    assert r["ok"] is True
    assert store.active[7] == b.build_id
