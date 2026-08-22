"""意图→skill 自动路由（AGENT-RUNTIME-CONTRACT：The Agent selects versioned Skills）。

交互路径（用户提问 → SSE run）默认不携带 skill：由服务端按问题文本（+ 当前
维度可用性）启发式路由到对应分析/生图 skill。这是**服务端决策**，不暴露给用户；
客户端仍可通过 body.skill 显式覆盖（高级选项）。

路由链：question 关键词 → 维度（QueryDimension 词汇或虚拟维度）→
``backfill.DIMENSION_TO_SKILL`` → skill（最多 2 个，去重，按优先级排序）。
无任何意图命中 → 保守回退 ``answer-reading-question``。
``source_status``（维度可用性）作为补充信号：显式意图未命中时，用不足维度
触发与 chat_backfill 同款分析（证据缺失 → 该补分析）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_revision import PromptRevision
from app.services.agent_runtime.backfill import (
    DIMENSION_TO_SKILL,
    MAX_BACKFILL_SKILLS,
)

# 单次自动路由最多选中的 skill 数量（成本上限，与 backfill 一致）。
MAX_ROUTED_SKILLS = MAX_BACKFILL_SKILLS

# 无任何意图命中 / 无可用维度的保守回退（问答 skill）。
DEFAULT_ROUTED_SKILL = "answer-reading-question"

# 问题关键词 → 维度（按优先级排序，先命中的优先）。
# 虚拟维度 "illustration" / "continuation" 由 DIMENSION_TO_SKILL 承载 skill 映射，
# 不入 QueryDimension 枚举（启发式路由的专有词表）。
_QUESTION_DIMENSION_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    # 生图意图：画/插图/配图/绘制/生成图/画面/视觉/图片。注意不包含裸 "场景"，
    # 避免 "关键场景" 被误路由到生图（detect-key-scenes 有专门词表）。
    (("画", "插图", "配图", "绘制", "生成图", "画面", "视觉", "图片"), "illustration"),
    # 人物关系（relations → build-visual-bible）。
    (("关系", "认识", "仇", "敌", "朋友", "谁喜欢", "谁讨厌"), "relations"),
    # 性格/动机（character_state → propose-world-model-candidates）。
    (("性格", "动机", "为什么"), "character_state"),
    # 关键场景/情节（raw_text → detect-key-scenes）。
    (("关键场景", "情节", "桥段", "名场面", "高潮"), "raw_text"),
    # 续写/后续（continuation → continue-derivative-story）。
    (("续写", "接着写", "继续写", "后续", "下回"), "continuation"),
)

# source_status 中代表"证据不足/维度缺失"的状态值（与 reader_chat 一致）。
_AVAILABILITY_UNAVAILABLE = frozenset({"unavailable", "absent"})


def classify_question_dimensions(
    question: str,
    source_status: dict[str, str] | None = None,
) -> list[str]:
    """问题文本 → 候选维度（按优先级去重，最多 MAX_ROUTED_SKILLS 个）。

    优先按显式关键词意图匹配；无命中时回退到 ``source_status`` 中的不足维度
    （与 chat_backfill 的不足维度语义一致）。
    """
    if not question:
        return []
    matched: list[str] = []
    seen: set[str] = set()
    for keywords, dimension in _QUESTION_DIMENSION_RULES:
        if dimension in seen:
            continue
        if any(keyword in question for keyword in keywords):
            matched.append(dimension)
            seen.add(dimension)
        if len(matched) >= MAX_ROUTED_SKILLS:
            break
    if matched:
        return matched
    unavailable = [
        dimension
        for dimension, status in (source_status or {}).items()
        if status in _AVAILABILITY_UNAVAILABLE
    ]
    return unavailable[:MAX_ROUTED_SKILLS]


def route_question_to_skill(
    question: str,
    source_status: dict[str, str] | None = None,
) -> list[str]:
    """按意图自动选出要执行的 skill 列表（最多 2 个，按优先级排序）。

    服务端决策入口：返回 skill 名列表，主 skill 为第一个；无任何命中回退
    ``answer-reading-question``。同一 skill 只出现一次（多维度映射去重）。
    """
    chosen: list[str] = []
    seen_skills: set[str] = set()
    for dimension in classify_question_dimensions(question, source_status):
        mapping = DIMENSION_TO_SKILL.get(dimension)
        if mapping is None:
            continue
        skill_name, _ = mapping
        if skill_name in seen_skills:
            continue
        chosen.append(skill_name)
        seen_skills.add(skill_name)
        if len(chosen) >= MAX_ROUTED_SKILLS:
            break
    return chosen or [DEFAULT_ROUTED_SKILL]


# 需要自动锚定的 skill → 其 input.schema 必需的锚定字段（服务端自动补全）。
# 锚 = 该 novel 最新**已批准** PromptRevision 的血缘（scene_spec / visual bible /
# source snapshot）+ 幂等 job_key。字段名与各 skill 的 input.schema.json 对齐，
# 只包含 schema 允许的字段（additionalProperties:false 防 422）。
SKILL_INPUT_ANCHOR_FIELDS: dict[str, tuple[str, ...]] = {
    "illustrate-scene": (
        "prompt_revision_id",
        "visual_bible_version_id",
        "scene_spec_revision_id",
        "source_snapshot_id",
        "job_key",
    ),
}

# 从 PromptRevision 血缘提取锚的字段映射（锚字段名 → PromptRevision 属性）。
_ANCHOR_FROM_PROMPT_REVISION: dict[str, str] = {
    "prompt_revision_id": "id",
    "visual_bible_version_id": "visual_bible_revision_id",
    "scene_spec_revision_id": "scene_spec_id",
    "source_snapshot_id": "source_snapshot_id",
}


async def resolve_skill_input_anchors(
    db: AsyncSession,
    skill_name: str,
    owner_id: int,
    novel_id: int,
) -> dict[str, Any] | None:
    """服务端自动解析 skill 运行必需的锚定字段（Agent 选锚，不暴露给用户）。

    - 需要锚的 skill（illustrate-scene 等）：查该 novel 最新**已批准** PromptRevision
      （review_state='approved'），从其血缘提取 prompt_revision_id /
      visual_bible_version_id / scene_spec_revision_id / source_snapshot_id，
      并生成幂等 job_key（``auto-<uuid>``）。无已批准 PromptRevision 或血缘不完整
      → None（诚实失败，不伪造锚——宁缺毋滥）。
    - 不需要锚的 skill（answer-reading-question 等）→ {}（原样，调用方用 body.input）。
    """
    anchor_fields = SKILL_INPUT_ANCHOR_FIELDS.get(skill_name)
    if anchor_fields is None:
        return {}
    row = await db.scalar(
        select(PromptRevision)
        .where(
            PromptRevision.owner_id == owner_id,
            PromptRevision.novel_id == novel_id,
            PromptRevision.review_state == "approved",
        )
        .order_by(PromptRevision.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    anchors: dict[str, Any] = {}
    for field in anchor_fields:
        if field == "job_key":
            anchors[field] = f"auto-{uuid.uuid4().hex}"
            continue
        attr = _ANCHOR_FROM_PROMPT_REVISION[field]
        value = getattr(row, attr)
        if value is None:
            # 血缘不完整（如 visual bible / scene spec 未批准）→ 诚实失败。
            return None
        anchors[field] = value
    return anchors
