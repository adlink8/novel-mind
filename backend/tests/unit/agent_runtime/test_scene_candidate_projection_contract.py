"""TS 投影产物 ↔ 后端门禁闭环契约测试（Slice A）。

agent-service 的 scene-candidate-projection 产出的 SceneCandidateSetContract
必须原样通过后端确定性门禁（契约校验 + manifest 重放 + evidence 血缘）。
fixture 与 agent-service/tests/scene-candidate-projection.test.ts 的固定
payload 保持同一逻辑内容；manifest_hash 黄金值由后端生成并被 TS 侧钉住。
"""

from __future__ import annotations

import pytest

from app.schemas.key_scene import (
    SceneCandidateSetContract,
    recompute_manifest_hash,
    validate_candidate_set_contract,
)

pytestmark = pytest.mark.unit

# 与 agent-service/tests/scene-candidate-projection.test.ts 的 fixture 一致。
TS_PROJECTED_SET = {
    "schema_version": "key-scene.v1",
    "artifact_kind": "key_scene",
    "owner_id": 2,
    "novel_id": 6,
    "version_key": "ks-backfill-run-1",
    "revision_number": 1,
    "parent_set_id": None,
    "source_snapshot_id": "ks-ks-backfill-run-1",
    "source_snapshot_hash": "b" * 64,
    "cutoff_chapter": 3,
    "schema_hash": "43f633fe5ec7e915d2b5fac0f123792f9988b76c2f11960171b0a2658c30cf5e",
    "policy_hash": "18319fdd57b57e2fb50b53bd225029eb9eb0f9a8cf480b82b81e68870a484839",
    "detector_id": "key-scene.v1",
    "detector_version": "1.0.0",
    "manifest_hash": "f483c67c16cacbe86fc349ea0cf17b90b2e2e14da15603c49a4b888462512d5c",
    "approved_visual_bible_revision_id": None,
    "approved_visual_bible_revision_hash": None,
    "candidates": [
        {
            "candidate_key": "ks-backfill-run-1-0",
            "candidate_order": 0,
            "scene_id": "scene-c7-0-10",
            "chapter_id": 7,
            "chapter_number": 2,
            "source_start": 0,
            "source_end": 10,
            "source_hash": "1" * 64,
            "coordinates": {"cast": ["林安"], "place": "庭院", "time": None, "pov": None},
            "spoiler_cutoff": 3,
            "salience_reasons": [
                {"reason_code": "plot_turn", "detail": "袭击发生", "score": 0.9}
            ],
            "score_total": 1,
            "score_breakdown": {"action": 0.8},
            "diversity_key": "scene-c7-0-10",
            "detector_id": "key-scene.v1",
            "detector_version": "1.0.0",
            "policy_hash": "18319fdd57b57e2fb50b53bd225029eb9eb0f9a8cf480b82b81e68870a484839",
            "evidence_ranges": [
                {
                    "evidence_key": "qp:7:0:10:" + "1" * 64,
                    "source_snapshot_id": "ks-ks-backfill-run-1",
                    "source_snapshot_hash": "b" * 64,
                    "chapter_id": 7,
                    "chapter_number": 2,
                    "source_start": 0,
                    "source_end": 10,
                    "content_hash": "1" * 64,
                    "excerpt": "夜色笼罩着庭院",
                    "cutoff_chapter": 3,
                }
            ],
            "heuristic_signal": None,
            "review_state": "candidate",
        }
    ],
    "review_state": "candidate",
}


def test_ts_projected_set_passes_contract_and_manifest_replay():
    """TS 投影的候选集必须通过后端契约校验，manifest_hash 逐字节重放。"""
    contract = SceneCandidateSetContract.model_validate(TS_PROJECTED_SET)
    validate_candidate_set_contract(contract)
    assert recompute_manifest_hash(contract) == TS_PROJECTED_SET["manifest_hash"]
