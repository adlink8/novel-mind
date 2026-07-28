"""07-06 release verifier unit."""

from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.release_verifier import verify_and_qualify

pytestmark = pytest.mark.unit


def test_regression_rejects():
    store = InMemoryBuildStore()
    ch = [{"chapter_id": 1, "chapter_number": 1, "content": "回归门。" * 30}]
    a = create_candidate_build(
        store, novel_id=1, chapters=ch, source_snapshot_hash="a" * 64, force_full=True
    )
    b = create_candidate_build(
        store,
        novel_id=1,
        chapters=ch,
        source_snapshot_hash="a" * 64,
        parent_build_id=a.build_id,
        force_full=True,
    )
    policy = "p" * 64
    rep = {
        "quality_comparable": True,
        "status": "comparable",
        "policy_hash": policy,
        "source_snapshot_hash": "a" * 64,
        "report_signature": "sig",
        "metrics": {
            "A": {
                "split_f1": 0.95,
                "recall_at_5": 0.9,
                "mrr": 0.8,
                "ndcg": 0.8,
                "cost_usd_total": 0.01,
                "coverage": 1.0,
                "overlap": 0,
                "critical_false_split": 0,
                "scene_coherence_mean": 4.5,
            },
            "B": {
                "split_f1": 0.80,
                "recall_at_5": 0.5,
                "mrr": 0.5,
                "ndcg": 0.5,
                "cost_usd_total": 0.5,
                "coverage": 1.0,
                "overlap": 0,
                "critical_false_split": 0,
                "scene_coherence_mean": 4.5,
            },
        },
    }
    ev = verify_and_qualify(
        store, ab_report=rep, candidate_build_id=b.build_id, policy_hash=policy
    )
    assert ev.status == "rejected"
    assert ev.quality_comparable is False
