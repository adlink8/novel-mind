"""07-05 promotion tests."""
from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.promotion import commit_promotion, prepare_promotion, PromotionError
from app.services.chunking.schemas import QualifiedChunkerEvidence
from app.services.rag_fixture import stable_hash

pytestmark = pytest.mark.integration

def _evidence(rec, *, status="qualified", comparable=True, sig=None):
    body = {"b": rec.build_id, "m": rec.manifest_checksum, "s": status}
    return QualifiedChunkerEvidence(
        build_id=rec.build_id,
        manifest_checksum=rec.manifest_checksum,
        source_snapshot_hash=rec.source_snapshot_hash,
        chunker_name=rec.chunker_name,
        chunker_version=rec.chunker_version,
        chunker_config_hash=rec.chunker_config_hash,
        chunk_manifest_hash=rec.manifest_checksum,
        policy_hash="p" * 64,
        quality_comparable=comparable,
        status=status,
        report_signature=sig or stable_hash(body),
        metrics={"split_f1": 0.95},
        reasons=[],
    )

def test_prepare_commit_moves_active_only_when_qualified():
    store = InMemoryBuildStore()
    chapters = [{"chapter_id": 1, "chapter_number": 1, "content": "晋升测试。" * 30}]
    a = create_candidate_build(store, novel_id=1, chapters=chapters, source_snapshot_hash="e" * 64, force_full=True)
    store.active[1] = a.build_id
    b = create_candidate_build(store, novel_id=1, chapters=chapters, source_snapshot_hash="e" * 64, parent_build_id=a.build_id, force_full=True)
    ev = _evidence(b)
    prepare_promotion(store, build_id=b.build_id, evidence=ev)
    assert store.active[1] == a.build_id
    r = commit_promotion(store, build_id=b.build_id, evidence=ev)
    assert r["ok"] is True
    assert store.active[1] == b.build_id

def test_reject_incomparable_leaves_active():
    store = InMemoryBuildStore()
    chapters = [{"chapter_id": 1, "chapter_number": 1, "content": "拒绝晋升。" * 30}]
    a = create_candidate_build(store, novel_id=2, chapters=chapters, source_snapshot_hash="f" * 64, force_full=True)
    store.active[2] = a.build_id
    b = create_candidate_build(store, novel_id=2, chapters=chapters, source_snapshot_hash="f" * 64, parent_build_id=a.build_id, force_full=True)
    bad = _evidence(b, comparable=False, status="rejected")
    r = commit_promotion(store, build_id=b.build_id, evidence=bad)
    assert r["ok"] is False
    assert store.active[2] == a.build_id
