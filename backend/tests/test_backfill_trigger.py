"""问答按需分析（chat_backfill）触发单元测试（Phase 40）。

覆盖：
- 维度→skill 映射正确性
- 优先级排序与 MAX_BACKFILL_SKILLS 上限
- 同一 skill 去重（多维度映射同一 skill 只触发一次）
- input_hash 确定性
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.services.agent_runtime.backfill import (
    DIMENSION_TO_SKILL,
    MAX_BACKFILL_SKILLS,
    pick_backfill_skills,
)
from app.services.agent_runtime.registry import canonical_input_hash


class TestDimensionToSkillMapping:
    def test_all_mapped_dimensions_have_known_skills(self):
        for dim, (skill, _materializes) in DIMENSION_TO_SKILL.items():
            assert isinstance(dim, str) and dim
            assert isinstance(skill, str) and skill
            assert isinstance(_materializes, bool)

    def test_world_projection_maps_to_world_model_candidates(self):
        skill, materializes = DIMENSION_TO_SKILL["world_projection"]
        assert skill == "propose-world-model-candidates"
        assert materializes is True

    def test_raw_text_maps_to_detect_key_scenes(self):
        skill, materializes = DIMENSION_TO_SKILL["raw_text"]
        assert skill == "detect-key-scenes"
        assert materializes is True


class TestPickBackfillSkills:
    def test_single_dimension_picks_correct_skill(self):
        chosen = pick_backfill_skills(["world_projection"])
        assert chosen == [("propose-world-model-candidates", "world_projection")]

    def test_priority_order_respected(self):
        chosen = pick_backfill_skills(
            ["raw_text", "world_projection", "character_state"]
        )
        # world_projection 优先级最高，raw_text 次之。
        assert chosen[0][0] == "propose-world-model-candidates"
        assert chosen[0][1] == "world_projection"
        assert chosen[1][0] == "detect-key-scenes"
        assert chosen[1][1] == "raw_text"

    def test_max_backfill_skills_cap(self):
        chosen = pick_backfill_skills(
            [
                "world_projection",
                "character_state",
                "raw_text",
                "relations",
                "events_causality",
                "timeline",
            ]
        )
        assert len(chosen) <= MAX_BACKFILL_SKILLS

    def test_same_skill_dedup_across_dimensions(self):
        # character_state 和 world_projection 都映射 propose-world-model-candidates，
        # 但只触发一次。
        chosen = pick_backfill_skills(["character_state", "world_projection"])
        skills = [s for s, _ in chosen]
        assert skills.count("propose-world-model-candidates") == 1

    def test_unknown_dimensions_ignored(self):
        chosen = pick_backfill_skills(["not_a_dimension", "raw_text"])
        assert chosen == [("detect-key-scenes", "raw_text")]

    def test_empty_dimensions_no_trigger(self):
        assert pick_backfill_skills([]) == []


class TestInputHashDeterminism:
    def test_same_payload_same_hash(self):
        a = canonical_input_hash(
            {
                "novel_id": 6,
                "question": "主角是谁",
                "dimension": "raw_text",
                "branch": None,
            }
        )
        b = canonical_input_hash(
            {
                "novel_id": 6,
                "question": "主角是谁",
                "dimension": "raw_text",
                "branch": None,
            }
        )
        assert a == b

    def test_different_payload_different_hash(self):
        a = canonical_input_hash({"novel_id": 6, "question": "主角是谁"})
        b = canonical_input_hash({"novel_id": 7, "question": "主角是谁"})
        assert a != b
