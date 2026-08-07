"""问答按需分析（chat_backfill）触发服务（Phase 40 / 用户扩展决策）。

问答证据不足（abstain）时，按 QueryDimension 维度映射触发对应分析 skill，
写 SkillRun(queued, origin='chat_backfill')。agent-service 的 queued-run
poller 负责 claim + 执行；FastAPI 侧 materializer 把产物物化到域表 candidate。

安全契约：
- 绝不 FastAPI→agent-service（poller 由 agent-service 主动 pull）
- 每次问答最多触发 2 个 skill；部分唯一索引防同 novel+维度在途重复
- 不 promotion：物化只写域表 candidate，approve 仍用户驱动
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import SkillRegistry, SkillRun, SkillVersion
from app.services.agent_runtime.registry import canonical_input_hash

# 每次问答最多触发的 backfill skill 数量（成本上限）。
MAX_BACKFILL_SKILLS = 2

# 维度→skill 映射（QueryDimension 词汇，queryplan/schemas.py）。
# 值：(skill_name, materializes_domain_table)
# 虚拟维度 "illustration"/"continuation" 是 skill_router 启发式路由的专有词表，
# 不入 QueryDimension 枚举；它们只服务意图→skill 自动路由，不被 queryplan 使用。
DIMENSION_TO_SKILL: dict[str, tuple[str, bool]] = {
    # character_state / world_projection 主补足：世界模型候选（EpistemicGate→candidate）
    "character_state": ("propose-world-model-candidates", True),
    "world_projection": ("propose-world-model-candidates", True),
    # 实体/外观补充：Visual Bible 候选版本
    "relations": ("build-visual-bible", True),
    # raw_text 0 命中：关键场景候选（带 leaf evidence_ranges）
    "raw_text": ("detect-key-scenes", True),
    # events/timeline：story arc 摘要（非 leaf，artifact-only，不写域表）
    "events_causality": ("build-story-arc", False),
    "timeline": ("build-story-arc", False),
    # 生图/续写意图（虚拟维度；skill_router 自动路由用，不进 queryplan）
    "illustration": ("illustrate-scene", False),
    "continuation": ("continue-derivative-story", False),
}

# 优先级：主补足维度优先；同一维度映射到同一 skill 时去重。
_DIMENSION_PRIORITY = [
    "world_projection",
    "character_state",
    "raw_text",
    "relations",
    "events_causality",
    "timeline",
]


def pick_backfill_skills(
    unavailable_dimensions: list[str],
) -> list[tuple[str, str]]:
    """按优先级从不足维度选最多 MAX_BACKFILL_SKILLS 个 (skill, dimension)。

    同一 skill 只触发一次（即使多个维度都映射到它）；返回按优先级排序。
    """
    chosen: list[tuple[str, str]] = []
    seen_skills: set[str] = set()
    for dim in _DIMENSION_PRIORITY:
        if dim not in unavailable_dimensions:
            continue
        mapping = DIMENSION_TO_SKILL.get(dim)
        if mapping is None:
            continue
        skill_name, _ = mapping
        if skill_name in seen_skills:
            continue
        chosen.append((skill_name, dim))
        seen_skills.add(skill_name)
        if len(chosen) >= MAX_BACKFILL_SKILLS:
            break
    return chosen


async def _resolve_active_skill_version(
    db: AsyncSession, *, owner_id: int, novel_id: int, skill_name: str
) -> SkillVersion | None:
    """按 owner+novel+name 找 active skill registry 的最新 active version。"""
    registry = await db.scalar(
        select(SkillRegistry).where(
            SkillRegistry.owner_id == owner_id,
            SkillRegistry.novel_id == novel_id,
            SkillRegistry.name == skill_name,
            SkillRegistry.status == "active",
        )
    )
    if registry is None:
        return None
    return await db.scalar(
        select(SkillVersion)
        .where(
            SkillVersion.registry_id == registry.id,
            SkillVersion.status == "active",
        )
        .order_by(SkillVersion.id.desc())
    )


async def create_backfill_runs(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    user_message_id: int,
    question: str,
    unavailable_dimensions: list[str],
    snapshot_hash: str | None = None,
    cutoff: str | None = None,
) -> list[SkillRun]:
    """问答 abstain 后触发 backfill skill runs（幂等、去重）。

    只触发已注册 active version 的 skill；在途（queued/running）同维度已有 run
    则跳过（部分唯一索引兜底 + 显式检查给出确定性跳过原因）。
    返回新建的 SkillRun 列表（未 commit；由调用方事务提交）。
    """
    created: list[SkillRun] = []
    for skill_name, dimension in pick_backfill_skills(unavailable_dimensions):
        version = await _resolve_active_skill_version(
            db, owner_id=owner_id, novel_id=novel_id, skill_name=skill_name
        )
        if version is None:
            # 未注册该 skill：诚实跳过（不触发未知技能）。
            continue
        # 在途去重（部分唯一索引兜底，此处显式检查给确定性结果）。
        existing = await db.scalar(
            select(SkillRun).where(
                SkillRun.owner_id == owner_id,
                SkillRun.novel_id == novel_id,
                SkillRun.backfill_dimension == dimension,
                SkillRun.origin == "chat_backfill",
                SkillRun.status.in_(("queued", "running")),
            )
        )
        if existing is not None:
            continue

        input_payload: dict[str, Any] = {
            "novel_id": novel_id,
            "question": question[:1000],
            "dimension": dimension,
            "branch": None,
        }
        if snapshot_hash:
            input_payload["source_snapshot"] = {"snapshot_hash": snapshot_hash}
        if cutoff:
            input_payload["cutoff"] = cutoff

        input_hash = canonical_input_hash(input_payload)
        internal_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(internal_token.encode("utf-8")).hexdigest()

        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=version.id,
            status="queued",
            branch=None,
            input=input_payload,
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot=dict(version.budget or {}),
            internal_token_hash=token_hash,
            origin="chat_backfill",
            backfill_dimension=dimension,
            user_message_id=user_message_id,
        )
        db.add(run)
        created.append(run)
    if created:
        await db.flush()
    return created
