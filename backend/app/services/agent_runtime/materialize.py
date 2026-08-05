"""问答按需分析（chat_backfill）产物物化器（Phase 40）。

把 agent-service finalize 落库的分析 artifact（candidate）确定性物化到
对应域表 candidate 行，使下一轮检索（retrieve_visible_evidence /
queryplan world_projection）可见候选证据。

安全契约：
- 绝不自动 promotion：只写域表 candidate，approve/freeze/approval 仍用户驱动；
- 幂等：以 run_id 为键，重复物化不重复写；
- 诚实边界：digest/摘要类产物（chapter_analysis / story_arc）不可作检索证据，
  记录 skipped_digest_not_evidence，不写域表；gate 前提不满足记录 skipped，
  绝不伪造通过。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.models.agent_runtime import Artifact, ArtifactRevision, SkillRun

logger = logging.getLogger(__name__)

# artifact.type → 是否可物化为检索证据（leaf evidence）。
_LEAF_EVIDENCE_TYPES = {
    "scene_candidate": True,
    "world_model_candidate": True,
    "visual_bible": True,
    # digest/摘要不可作证据：只落 artifact，不写域表。
    "chapter_analysis": False,
    "story_arc": False,
}


async def materialize_skill_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: int,
) -> str:
    async with sessions.begin() as session:
        run = await session.get(SkillRun, run_id)
        if run is None or run.origin != "chat_backfill":
            return "skipped:not_backfill"
        if run.status_reason and run.status_reason.startswith("materialized:"):
            return run.status_reason
        if run.status != "completed":
            return "skipped:not_completed"

        artifact = await _latest_artifact(session, run_id)
        if artifact is None:
            return "no_artifact"
        revision = await _latest_revision(session, artifact.id)
        if revision is None:
            return "no_revision"

        artifact_type = artifact.type
        content = dict(revision.content or {})

        if not _LEAF_EVIDENCE_TYPES.get(artifact_type, False):
            run.status_reason = "skipped_digest_not_evidence"
            return run.status_reason

        # 域写入（candidate-only）：当前实现优先记录候选已物化，具体域表
        # 写入由各 skill 的确定性 gate 路径承接（后续迭代细化）。
        # 诚实边界：本阶段把「产物已就绪」记录在 run 上，域表 candidate
        # 写入依赖既有 gate（CandidateService / EpistemicGate /
        # VisualBibleAuthorityService）的前提满足情况。
        try:
            from app.services.agent_runtime.materializers import (
                materialize_to_domain,
            )

            outcome = await materialize_to_domain(
                session, run=run, artifact_type=artifact_type, content=content
            )
        except Exception as exc:  # noqa: BLE001 - 物化失败记录 skipped，不阻断
            logger.warning(
                "backfill materialize run_id=%s type=%s failed: %s",
                run_id,
                artifact_type,
                exc,
            )
            run.status_reason = f"skipped:{type(exc).__name__}"
            return run.status_reason

        if outcome == "ok":
            run.status_reason = f"materialized:{artifact_type}"
        else:
            run.status_reason = outcome
        return run.status_reason


async def _latest_artifact(
    session: AsyncSession, run_id: int
) -> Artifact | None:
    from sqlalchemy import select

    return await session.scalar(
        select(Artifact)
        .where(Artifact.run_id == run_id)
        .order_by(Artifact.id.desc())
        .limit(1)
    )


async def _latest_revision(
    session: AsyncSession, artifact_id: int
) -> ArtifactRevision | None:
    from sqlalchemy import select

    return await session.scalar(
        select(ArtifactRevision)
        .where(ArtifactRevision.artifact_id == artifact_id)
        .order_by(ArtifactRevision.revision_no.desc())
        .limit(1)
    )


# 兼容：materialize.py 的旧入口签名（background task 调用），
# 新实现委托 materializers 模块；保持单文件即可满足 Phase 2 验证。
async def materialize_skill_run_sync(
    sessions: async_sessionmaker[AsyncSession], run_id: int
) -> str:
    return await materialize_skill_run(sessions, run_id)
