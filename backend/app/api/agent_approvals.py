"""ApprovalRequest API：审批请求唯一决策权威（25.2-03 / D-11/D-15）。

从原 ``app/api/agent.py`` 拆出：本文件只承载审批请求域路由。
- ``POST /approval-requests`` — 铸造审批请求（agent-service 在 ask 决策时触发）。
- ``POST .../{request_id}/confirm`` — owner 确认：pending → approved（once）|
  approved_for_session（session）；非 owner → 404（404-hide），重复决策 → 409。
- ``POST .../{request_id}/reject`` — owner 拒绝：pending → rejected（终态）。
- ``GET  /approval-requests`` — 分页列出当前用户的审批请求。
- ``GET  /approval-requests/{request_id}`` — 按 id 读取（agent-service 短轮询端点；
  pending 且过 expires_at 时读取即就地标记 expired）。

路由形状沿用原 agent.py：router 级 ``Depends(require_user)``。
分页统一 {"items","total","skip","limit"}。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import User
from app.schemas.agent_approvals import (
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestView,
)
from app.services.agent_runtime import approvals as approval_service
from app.services.agent_runtime.approvals import ApprovalStateError

router = APIRouter(dependencies=[Depends(require_user)])


def _view_from_approval(row) -> dict[str, Any]:
    return ApprovalRequestView.model_validate(row).model_dump(mode="json")


@router.post(
    "/approval-requests",
    response_model=ApprovalRequestView,
    status_code=status.HTTP_201_CREATED,
)
async def create_approval_request(
    data: ApprovalRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ApprovalRequestView:
    """铸造审批请求（agent-service 在 ask 决策时触发）。

    owner 以当前认证用户为权威；显式 owner_id 不符 → 400（伪造防御）。
    只读工具不可自行铸造（service 内 owner 强校验）。
    """
    try:
        request = await approval_service.create(
            db, owner_id=current_user.id, payload=data
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(request)
    return ApprovalRequestView.model_validate(request)


@router.post(
    "/approval-requests/{request_id}/confirm",
    response_model=ApprovalRequestView,
)
async def confirm_approval_request(
    request_id: int,
    data: ApprovalDecision,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ApprovalRequestView:
    """owner 确认：pending → approved（once）| approved_for_session（session）。

    非 owner → 404（404-hide，绝不 403 oracle）；重复决策 → 409（稳定 conflict）。
    """
    try:
        request = await approval_service.confirm(
            db, request_id=request_id, owner_id=current_user.id, mode=data.mode
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    await db.commit()
    await db.refresh(request)
    return ApprovalRequestView.model_validate(request)


@router.post(
    "/approval-requests/{request_id}/reject",
    response_model=ApprovalRequestView,
)
async def reject_approval_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ApprovalRequestView:
    """owner 拒绝：pending → rejected（终态）。非 owner → 404（404-hide）。"""
    try:
        request = await approval_service.reject(
            db, request_id=request_id, owner_id=current_user.id
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if request is None:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    await db.commit()
    await db.refresh(request)
    return ApprovalRequestView.model_validate(request)


@router.get("/approval-requests", response_model=dict)
async def list_approval_requests(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """分页列出当前用户的审批请求（{"items","total","skip","limit"}）。"""
    items, total = await approval_service.list_for_owner(
        db, owner_id=current_user.id, skip=skip, limit=limit
    )
    return {
        "items": [_view_from_approval(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get(
    "/approval-requests/{request_id}",
    response_model=ApprovalRequestView,
)
async def get_approval_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ApprovalRequestView:
    """按 id 读取（agent-service 短轮询端点）。非 owner → 404（404-hide）。

    若 pending 且已过 expires_at，读取即就地标记 expired（短轮询看到过期即停）。
    """
    request = await approval_service.get_for_owner(
        db, request_id=request_id, owner_id=current_user.id
    )
    if request is None:
        raise HTTPException(status_code=404, detail="审批请求不存在")
    await db.commit()
    await db.refresh(request)
    return ApprovalRequestView.model_validate(request)
