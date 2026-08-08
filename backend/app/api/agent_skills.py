"""SkillRegistry API：技能注册 / 列表 / 意图→skill 自动路由（25.2-03 / D-09..D-11）。

从原 ``app/api/agent.py`` 拆出：本文件只承载技能目录域路由。
- ``GET  /skills`` — 当前用户的技能目录。
- ``POST /skills`` — D-09 契约校验注册技能版本（allowed_tools 白名单 fail-closed）。
- ``GET  /skills/{skill_name}/versions`` — 某技能的版本列表（含契约全文）。
- ``POST /novels/{novel_id}/route-skill`` — 意图→skill 自动路由（AGENT-RUNTIME-CONTRACT：
  Agent 选 skill，用户不选；agent-service 在 body.skill 缺省时调用）。

路由形状沿用原 agent.py：router 级 ``Depends(require_user)``，小说域资源经
``Depends(require_owned_novel)``（404-hide，owner 结构上不可避免）。
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, SkillRegistry, User
from app.schemas.agent_runtime import (
    RouteSkillRequest,
    SkillRegistryView,
    SkillVersionRegister,
    SkillVersionView,
)
from app.services.agent_runtime import registry as registry_service
from app.services.agent_runtime.registry import SkillContractError

router = APIRouter(dependencies=[Depends(require_user)])


def _view_from_registry(row) -> dict[str, Any]:
    return SkillRegistryView.model_validate(row).model_dump(mode="json")


def _view_from_version(row) -> dict[str, Any]:
    return SkillVersionView.model_validate(row).model_dump(mode="json")


@router.get("/skills", response_model=dict)
async def list_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """列出当前用户的技能目录。"""
    items, total = await registry_service.list_skills(
        db, owner_id=current_user.id, skip=skip, limit=limit
    )
    return {
        "items": [_view_from_registry(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post(
    "/skills",
    response_model=SkillVersionView,
    status_code=status.HTTP_201_CREATED,
)
async def register_skill(
    data: SkillVersionRegister,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> SkillVersionView:
    """注册技能版本：D-09 契约校验 + allowed_tools 白名单 fail-closed。

    未知域工具 → 注册拒绝（400），不产生任何 active 行（T-25.2-03-01）。
    """
    novel = await db.scalar(select(Novel).where(Novel.id == data.novel_id))
    if novel is None or (
        novel.owner_id != current_user.id and not current_user.is_superuser
    ):
        raise HTTPException(status_code=404, detail="小说不存在")
    try:
        _, version = await registry_service.register_skill_version(
            db, owner_id=current_user.id, novel_id=novel.id, contract=data
        )
    except SkillContractError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return SkillVersionView.model_validate(version)


@router.get("/skills/{skill_name}/versions", response_model=dict)
async def list_skill_versions(
    skill_name: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """列出某技能的版本（含 D-09 契约全文）。"""
    items, total = await registry_service.list_skill_versions(
        db, owner_id=current_user.id, skill_name=skill_name, skip=skip, limit=limit
    )
    return {
        "items": [_view_from_version(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post("/novels/{novel_id}/route-skill", response_model=dict)
async def route_skill(
    novel_id: int,
    data: RouteSkillRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """意图→skill 自动路由（AGENT-RUNTIME-CONTRACT：Agent 选 skill，用户不选）。

    agent-service 在 body.skill 缺省时调用本端点：按问题文本（+ 可选维度可用性）
    启发式选 skill。只返回该 owner+novel 已注册 active 的 skill，其余诚实剔除；
    无 active 命中回退 answer-reading-question。路由是**服务端决策**——本响应
    只服务 agent-service/未来前端的自动分发，不作为对用户的技能建议。
    """
    from app.services.agent_runtime.skill_router import (
        DEFAULT_ROUTED_SKILL,
        resolve_skill_input_anchors,
        route_question_to_skill,
    )

    candidates = route_question_to_skill(data.question, data.source_status)
    active: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        registry = await db.scalar(
            select(SkillRegistry).where(
                SkillRegistry.owner_id == current_user.id,
                SkillRegistry.novel_id == novel.id,
                SkillRegistry.name == name,
                SkillRegistry.status == "active",
            )
        )
        if registry is not None:
            active.append(name)
    if not active:
        active = [DEFAULT_ROUTED_SKILL]
    # 自动锚解析：主 skill 需要的锚定字段（服务端从已批准 PromptRevision 血缘
    # 自动选锚，不暴露给用户）。无锚（非锚 skill / 无已批准 PromptRevision）→ null。
    anchors = await resolve_skill_input_anchors(
        db, active[0], current_user.id, novel.id
    )
    return {
        "skills": active,
        "primary": active[0],
        "question_hash": hashlib.sha256(data.question.encode("utf-8")).hexdigest(),
        "input_anchor": anchors if anchors else None,
    }
