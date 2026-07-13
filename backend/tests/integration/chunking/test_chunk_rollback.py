"""07-05 rollback tests."""
from __future__ import annotations
import pytest
from app.services.chunking.builds import InMemoryBuildStore, create_candidate_build
from app.services.chunking.promotion import commit_promotion, prepare_promotion
from app.services.chunking.rollback import rollback_to_build
from app.services.chunking.schemas import QualifiedChunkerEvidence
from app.services.rag_fixture import stable_hash

pytestmark = pytest.mark.integration

def test_rollback_restores_previous_active():
    store = InMemoryBuildStore()
    chapters = [{"chapter_id": 1, "chapter_number": 1, "content": "回滚测试。" * 30}]
    a = create_candidate_build(store, novel_id=1, chapters=chapters, source_snapshot_hash="1" * 64, force_full=True)
    store.active[1] = a.build_id
    b = create_candidate_build(store, novel_id=1, chapters=chapters, source_snapshot_hash="1" * 64, parent_build_id=a.build_id, force_full=True)
    ev = QualifiedChunkerEvidence(
        build_id=b.build_id,
        manifest_checksum=b.manifest_checksum,
        source_snapshot_hash=b.source_snapshot_hash,
        chunker_name=b.chunker_name,
        chunker_version=b.chunker_version,
        chunker_config_hash=b.chunker_config_hash,
        chunk_manifest_hash=b.manifest_checksum,
        policy_hash="q" * 64,
        quality_comparable=True,
        status="qualified",
        report_signature=stable_hash({"x": b.build_id}),
        metrics={},
        reasons=[],
    )
    prepare_promotion(store, build_id=b.build_id, evidence=ev)
    commit_promotion(store, build_id=b.build_id, evidence=ev)
    assert store.active[1] == b.build_id
    r = rollback_to_build(store, novel_id=1, target_build_id=a.build_id)
    assert r["ok"] is True
    assert store.active[1] == a.build_id
