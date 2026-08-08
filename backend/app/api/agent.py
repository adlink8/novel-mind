"""Skill Runtime 智能体 API（25.2-03 / D-09..D-14）——兼容层。

原巨型文件已按资源域拆分为独立模块（均在 ``app/api/`` 下）：
  - ``agent_skills.py``：SkillRegistry（含 route-skill）
  - ``agent_runs.py``：SkillRun（accept/list/cancel/retry/finalize）
  - ``agent_artifacts.py``：Artifact（读取 + approve/reject）
  - ``agent_approvals.py``：ApprovalRequest（CRUD）
  - ``agent_service_runs.py``：service-to-service poller（独立 service_router）

本文件仅作为 re-export 兼容层：``router`` 聚合全部用户侧子路由（与原
``agent.py`` 路由面一致），``service_router`` 直接转发，避免破坏既有
import 面（如 ``from app.api.agent import router``）。
"""

from fastapi import APIRouter

from app.api.agent_approvals import router as _approvals_router
from app.api.agent_artifacts import router as _artifacts_router
from app.api.agent_runs import router as _runs_router
from app.api.agent_service_runs import service_router
from app.api.agent_skills import router as _skills_router

# 聚合 router：路由面与原 agent.py 的 router 完全一致（子 router 已各自带
# ``Depends(require_user)``，此处不再重复叠加）。
router = APIRouter()
router.include_router(_skills_router)
router.include_router(_runs_router)
router.include_router(_artifacts_router)
router.include_router(_approvals_router)

__all__ = ["router", "service_router"]
