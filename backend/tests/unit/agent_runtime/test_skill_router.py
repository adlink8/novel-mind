"""意图→skill 自动路由单元测试（AGENT-RUNTIME-CONTRACT：Agent 选 skill）。

覆盖：
- 问题关键词 → 维度 → skill 的启发式映射
- 优先级排序与最多 2 个的上限
- 同一 skill 去重（多维度映射同一 skill）
- 无意图命中回退 answer-reading-question
- source_status 不足维度回退（与 chat_backfill 语义一致）
- DIMENSION_TO_SKILL 已扩展生图/续写虚拟维度
- 自动锚解析：illustrate-scene 从已批准 PromptRevision 血缘提取锚；
  无已批准 PromptRevision → None；非锚 skill → {}
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

from app.services.agent_runtime.backfill import DIMENSION_TO_SKILL
from app.services.agent_runtime.skill_router import (
    DEFAULT_ROUTED_SKILL,
    MAX_ROUTED_SKILLS,
    SKILL_INPUT_ANCHOR_FIELDS,
    classify_question_dimensions,
    resolve_skill_input_anchors,
    route_question_to_skill,
)


class TestIllustrationRouting:
    def test_draw_scene_routes_to_illustrate_scene(self):
        # 冒烟：画场景 → illustrate-scene
        skills = route_question_to_skill("帮我画第1章的竹林场景")
        assert skills[0] == "illustrate-scene"

    def test_illustration_keywords_route_to_illustrate_scene(self):
        for question in ("给阿宁配一张插图", "生成图：林默的玉佩", "画面怎么构图"):
            skills = route_question_to_skill(question)
            assert skills[0] == "illustrate-scene", question

    def test_key_scene_question_not_routed_to_illustration(self):
        # 裸 "场景" 不触发生图：避免 "关键场景" 误路由。
        skills = route_question_to_skill("第1章有哪些关键场景")
        assert skills[0] == "detect-key-scenes"


class TestOtherIntents:
    def test_relations_routes_to_build_visual_bible(self):
        skills = route_question_to_skill("阿宁和使者是什么关系")
        assert skills[0] == "build-visual-bible"

    def test_character_motivation_routes_to_world_model(self):
        skills = route_question_to_skill("林默为什么去找爷爷")
        assert skills[0] == "propose-world-model-candidates"

    def test_key_scenes_routes_to_detect_key_scenes(self):
        skills = route_question_to_skill("故事里有哪些名场面")
        assert skills[0] == "detect-key-scenes"

    def test_continuation_routes_to_continue_derivative_story(self):
        skills = route_question_to_skill("帮我续写接下来发生的事")
        assert skills[0] == "continue-derivative-story"


class TestRoutingBounds:
    def test_default_fallback_when_no_intent(self):
        skills = route_question_to_skill("主角是谁")
        assert skills == [DEFAULT_ROUTED_SKILL]

    def test_empty_question_defaults(self):
        assert route_question_to_skill("") == [DEFAULT_ROUTED_SKILL]

    def test_max_routed_skills_cap(self):
        # 高优先级规则先命中，最多 MAX_ROUTED_SKILLS 个。
        skills = route_question_to_skill("画一幅图，分析角色性格，讲关键情节")
        assert len(skills) <= MAX_ROUTED_SKILLS
        assert len(skills) == MAX_ROUTED_SKILLS

    def test_primary_is_priority_order(self):
        skills = route_question_to_skill("画一幅图，分析角色性格")
        # illustration 规则在 character_state 之前。
        assert skills[0] == "illustrate-scene"

    def test_no_duplicate_skills(self):
        skills = route_question_to_skill("画一幅图")
        assert len(skills) == len(set(skills))


class TestSourceStatusFallback:
    def test_unavailable_dimensions_used_when_no_intent(self):
        skills = route_question_to_skill(
            "随便聊聊", {"raw_text": "unavailable", "relations": "available"}
        )
        assert skills[0] == "detect-key-scenes"

    def test_absent_status_also_triggers(self):
        skills = route_question_to_skill("随便聊聊", {"relations": "absent"})
        assert skills[0] == "build-visual-bible"

    def test_intent_wins_over_source_status(self):
        # 显式意图优先；即使 source_status 指向别的不足维度也不覆盖。
        skills = route_question_to_skill(
            "画一幅图", {"raw_text": "unavailable", "relations": "unavailable"}
        )
        assert skills[0] == "illustrate-scene"

    def test_no_unavailable_dimension_falls_back_to_default(self):
        skills = route_question_to_skill("随便聊聊", {"raw_text": "available"})
        assert skills == [DEFAULT_ROUTED_SKILL]


class TestDimensionVocabulary:
    def test_illustration_virtual_dimension_mapped(self):
        skill, materializes = DIMENSION_TO_SKILL["illustration"]
        assert skill == "illustrate-scene"
        assert materializes is False

    def test_continuation_virtual_dimension_mapped(self):
        skill, materializes = DIMENSION_TO_SKILL["continuation"]
        assert skill == "continue-derivative-story"
        assert materializes is False

    def test_classify_returns_dimensions_in_priority_order(self):
        dims = classify_question_dimensions("画一幅图，分析人物性格")
        assert dims[0] == "illustration"
        assert dims[1] == "character_state"


class TestAnchorResolution:
    """自动锚解析：illustrate-scene 从已批准 PromptRevision 血缘提取锚。"""

    @staticmethod
    def _fake_db(row) -> SimpleNamespace:
        return SimpleNamespace(scalar=AsyncMock(return_value=row))

    @staticmethod
    def _approved_row(**overrides):
        values = {
            "id": 7,
            "scene_spec_id": 3,
            "visual_bible_revision_id": 2,
            "source_snapshot_id": "ss-real",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    async def test_illustrate_scene_returns_anchors_from_approved_prompt(self):
        anchors = await resolve_skill_input_anchors(
            self._fake_db(self._approved_row()),
            "illustrate-scene",
            owner_id=1,
            novel_id=6,
        )
        assert anchors is not None
        assert anchors["prompt_revision_id"] == 7
        assert anchors["visual_bible_version_id"] == 2
        assert anchors["scene_spec_revision_id"] == 3
        assert anchors["source_snapshot_id"] == "ss-real"
        assert anchors["job_key"].startswith("auto-")
        assert len(anchors["job_key"]) > len("auto-")

    async def test_no_approved_prompt_returns_none(self):
        anchors = await resolve_skill_input_anchors(
            self._fake_db(None),
            "illustrate-scene",
            owner_id=1,
            novel_id=6,
        )
        assert anchors is None

    async def test_incomplete_lineage_returns_none(self):
        # 血缘不完整（scene_spec_id 为 NULL）→ 诚实失败，不注入 null 锚。
        anchors = await resolve_skill_input_anchors(
            self._fake_db(self._approved_row(scene_spec_id=None)),
            "illustrate-scene",
            owner_id=1,
            novel_id=6,
        )
        assert anchors is None

    async def test_non_anchor_skill_returns_empty(self):
        anchors = await resolve_skill_input_anchors(
            self._fake_db(None),
            "answer-reading-question",
            owner_id=1,
            novel_id=6,
        )
        assert anchors == {}

    def test_illustrate_scene_anchor_fields_declared(self):
        assert "illustrate-scene" in SKILL_INPUT_ANCHOR_FIELDS
        assert set(SKILL_INPUT_ANCHOR_FIELDS["illustrate-scene"]) == {
            "prompt_revision_id",
            "visual_bible_version_id",
            "scene_spec_revision_id",
            "source_snapshot_id",
            "job_key",
        }

