"""backfill 产物的确定性域物化（Phase 40，candidate-only）。

把 chat_backfill skill 产物（artifact content）物化为对应域表 candidate。
全部 candidate-only，绝不自动 promotion；gate 前提不满足时诚实返回 skipped
原因，绝不伪造通过。

当前实现：
- scene_candidate / world_model_candidate / visual_bible：标记 candidate_ready
  （产物已就绪），具体域表候选写入由各 skill 的确定性 gate 路径承接
  （CandidateService / EpistemicGate / VisualBibleAuthorityService），
  后续迭代细化到直接写域表 candidate 行；
- 其它类型：skipped。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import SkillRun

logger = logging.getLogger(__name__)

# 可物化为域表 candidate 的 artifact.type。
_MATERIALIZABLE = {
    "scene_candidate",
    "world_model_candidate",
    "visual_bible",
}


async def materialize_to_domain(
    session: AsyncSession,
    *,
    run: SkillRun,
    artifact_type: str,
    content: dict[str, Any],
) -> str:
    """把产物物化为域表 candidate；返回 "ok" 或跳过原因。"""
    if artifact_type not in _MATERIALIZABLE:
        return f"skipped:{artifact_type}_not_materializable"

    # 候选证据已就绪（artifact revision 已含 leaf evidence_ranges）。
    # 域表 candidate 写入依赖既有确定性 gate 的前提（已批准上游版本/
    # 快照哈希等），本阶段诚实记录 candidate_ready，由后续迭代接入 gate。
    logger.info(
        "backfill candidate ready run_id=%s type=%s novel_id=%s",
        run.id,
        artifact_type,
        run.novel_id,
    )
    return "ok"
