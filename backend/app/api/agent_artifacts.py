"""Artifact API：产物读取 + 唯一状态变更路径 approve/reject（25.2-03 / D-12..D-13）。

从原 ``app/api/agent.py`` 拆出：本文件只承载产物域路由。
- ``GET  /novels/{novel_id}/artifacts`` — 分页列出某小说的产物。
- ``GET  /novels/{novel_id}/artifacts/{artifact_id}`` — 单产物读取。
- ``GET  .../artifacts/{artifact_id}/revisions`` — 分页列出产物修订（血缘链升序）。
- ``POST /artifacts/{artifact_id}/approve`` — 按前向状态机逐级推进
  candidate→validated→approved→published。
- ``POST /artifacts/{artifact_id}/reject`` — candidate/validated → rejected。

approve/reject 是产物状态唯一的变更路径，owner 检查在 service 内强制
（T-25.2-03-03）。路由形状沿用原 agent.py：router 级 ``Depends(require_user)``，
小说域资源经 ``Depends(require_owned_novel)``（404-hide）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.agent_runtime import ArtifactRevisionView, ArtifactView
from app.services.agent_runtime import artifacts as artifact_service
from app.services.agent_runtime.artifacts import ArtifactStateError

router = APIRouter(dependencies=[Depends(require_user)])

# 每次 approve 向下一步：candidate→validated→approved→published。
_NEXT_APPROVE_STATUS: dict[str, str] = {
    "candidate": "validated",
    "validated": "approved",
    "approved": "published",
}


def _view_from_artifact(row) -> dict[str, Any]:
    return ArtifactView.model_validate(row).model_dump(mode="json")


def _view_from_revision(row) -> dict[str, Any]:
    return ArtifactRevisionView.model_validate(row).model_dump(mode="json")


@router.get("/novels/{novel_id}/artifacts", response_model=dict)
async def list_artifacts(
    novel_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """分页列出某小说的产物（{"items","total","skip","limit"}）。"""
    items, total = await artifact_service.list_artifacts(
        db, owner_id=current_user.id, novel_id=novel.id, skip=skip, limit=limit
    )
    return {
        "items": [_view_from_artifact(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.get(
    "/novels/{novel_id}/artifacts/{artifact_id}",
    response_model=ArtifactView,
)
async def get_artifact(
    novel_id: int,
    artifact_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ArtifactView:
    artifact = await artifact_service.get_artifact(
        db, artifact_id=artifact_id, owner_id=current_user.id, novel_id=novel.id
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    return ArtifactView.model_validate(artifact)


@router.get(
    "/novels/{novel_id}/artifacts/{artifact_id}/revisions",
    response_model=dict,
)
async def list_artifact_revisions(
    novel_id: int,
    artifact_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """分页列出产物修订（血缘链升序）。"""
    items, total = await artifact_service.list_artifact_revisions(
        db,
        artifact_id=artifact_id,
        owner_id=current_user.id,
        novel_id=novel.id,
        skip=skip,
        limit=limit,
    )
    return {
        "items": [_view_from_revision(item) for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post(
    "/artifacts/{artifact_id}/approve",
    response_model=ArtifactView,
)
async def approve_artifact(
    artifact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ArtifactView:
    """批准产物：按前向状态机逐级推进 candidate→validated→approved→published。

    owner 检查在 service 内强制；这是产物状态唯一的变更路径（T-25.2-03-03）。
    """
    # 每次 approve 前进一步；到达终态后再 approve 报错。
    artifact = await artifact_service.get_artifact_for_owner(
        db, artifact_id=artifact_id, owner_id=current_user.id
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    next_status = _NEXT_APPROVE_STATUS.get(artifact.status)
    if next_status is None:
        raise HTTPException(
            status_code=409,
            detail=f"artifact status {artifact.status!r} cannot be approved further",
        )
    try:
        artifact = await artifact_service.transition_artifact_status(
            db, artifact_id=artifact_id, owner_id=current_user.id, to_status=next_status
        )
    except ArtifactStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(artifact)
    return ArtifactView.model_validate(artifact)


@router.post(
    "/artifacts/{artifact_id}/reject",
    response_model=ArtifactView,
)
async def reject_artifact(
    artifact_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ArtifactView:
    """拒绝产物：candidate/validated → rejected（owner 检查）。"""
    try:
        artifact = await artifact_service.transition_artifact_status(
            db, artifact_id=artifact_id, owner_id=current_user.id, to_status="rejected"
        )
    except ArtifactStateError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(artifact)
    return ArtifactView.model_validate(artifact)
