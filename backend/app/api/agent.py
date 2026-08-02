"""
Skill Runtime 智能体 API（25.2-03 / D-09..D-14）。

路由形状仿 reader_chat.py：
  - router 级 ``Depends(require_user)``。
  - 小说域资源经 ``Depends(require_owned_novel)``（404-hide，owner 结构上不可避免）。
  - ``POST .../skill-runs`` 202 接受 + commit-before-dispatch（只持久化/授权并铸造
    per-run 内部令牌；真正的分发由 agent-service 按 25.2-05 Task 4 调用本端点触发，
    绝不 FastAPI→agent-service）。
  - artifact approve/reject 是产物状态唯一的变更路径，owner 检查在 service 内强制。
  - 分页统一 {"items","total","skip","limit"}。
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, SkillRun, User
from app.schemas.agent_approvals import (
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestView,
)
from app.schemas.agent_runtime import (
    ArtifactRevisionView,
    ArtifactView,
    SkillRegistryView,
    SkillRunAccepted,
    SkillRunCreate,
    SkillRunFinalize,
    SkillRunView,
    SkillVersionRegister,
    SkillVersionView,
)
from app.services.agent_runtime import approvals as approval_service
from app.services.agent_runtime import artifacts as artifact_service
from app.services.agent_runtime import registry as registry_service
from app.services.agent_runtime.approvals import ApprovalStateError
from app.services.agent_runtime.artifacts import ArtifactStateError
from app.services.agent_runtime.registry import SkillContractError, canonical_input_hash

router = APIRouter(dependencies=[Depends(require_user)])

# 每次 approve 前向一步：candidate→validated→approved→published。
_NEXT_APPROVE_STATUS: dict[str, str] = {
    "candidate": "validated",
    "validated": "approved",
    "approved": "published",
}


def _view_from_registry(row) -> dict[str, Any]:
    return SkillRegistryView.model_validate(row).model_dump(mode="json")


def _view_from_version(row) -> dict[str, Any]:
    return SkillVersionView.model_validate(row).model_dump(mode="json")


def _view_from_run(row) -> dict[str, Any]:
    return SkillRunView.model_validate(row).model_dump(mode="json")


def _view_from_artifact(row) -> dict[str, Any]:
    return ArtifactView.model_validate(row).model_dump(mode="json")


def _view_from_revision(row) -> dict[str, Any]:
    return ArtifactRevisionView.model_validate(row).model_dump(mode="json")


def _view_from_approval(row) -> dict[str, Any]:
    return ApprovalRequestView.model_validate(row).model_dump(mode="json")


# ────────────────────────── 技能注册 / 列表 ──────────────────────────


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


# ────────────────────────── 技能运行：接受 / 取消 / 重试 ──────────────────────────


@router.post(
    "/novels/{novel_id}/skill-runs",
    response_model=SkillRunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def accept_skill_run(
    novel_id: int,
    data: SkillRunCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> SkillRunAccepted:
    """接受一次技能运行：持久化 + 授权 + 铸造 per-run 内部令牌。

    commit-before-dispatch：先 commit 让 run 可见，返回 202；分发由
    agent-service 按 25.2-05 驱动（本端点绝不主动调用 agent-service）。
    """
    version = await registry_service.get_skill_version(
        db, owner_id=current_user.id, skill_version_id=data.skill_version_id
    )
    if version is None:
        raise HTTPException(status_code=404, detail="技能版本不存在")
    if version.status != "active":
        raise HTTPException(status_code=422, detail="技能版本不可用")

    # 输入规范化：novel_id 以路径为准，不能被请求体伪造到其它小说。
    input_payload = dict(data.input)
    input_payload["novel_id"] = novel.id
    if (
        not isinstance(input_payload.get("question"), str)
        or not input_payload["question"].strip()
    ):
        raise HTTPException(status_code=422, detail="input.question 必须为非空字符串")

    input_hash = canonical_input_hash(input_payload)
    internal_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(internal_token.encode("utf-8")).hexdigest()

    run = SkillRun(
        owner_id=current_user.id,
        novel_id=novel.id,
        skill_version_id=version.id,
        status="queued",
        branch=data.branch,
        input=input_payload,
        input_hash=input_hash,
        frozen_manifest={},
        budget_snapshot=dict(version.budget or {}),
        internal_token_hash=token_hash,
    )
    db.add(run)
    await db.flush()
    # commit-before-dispatch：run 立即可见（worker / agent-service 新会话可读到）。
    await db.commit()
    await db.refresh(run)
    return SkillRunAccepted(
        run=SkillRunView.model_validate(run), internal_token=internal_token
    )


@router.get("/novels/{novel_id}/skill-runs", response_model=dict)
async def list_skill_runs(
    novel_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    """列出某小说的技能运行。"""
    where = (SkillRun.owner_id == current_user.id, SkillRun.novel_id == novel.id)
    total = await db.scalar(select(func.count()).select_from(SkillRun).where(*where))
    rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(*where)
                .order_by(SkillRun.id.desc())
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return {
        "items": [_view_from_run(item) for item in rows],
        "total": int(total or 0),
        "skip": skip,
        "limit": limit,
    }


@router.post(
    "/novels/{novel_id}/skill-runs/{run_id}/cancel",
    response_model=SkillRunView,
)
async def cancel_skill_run(
    novel_id: int,
    run_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> SkillRunView:
    """请求取消运行：置 cancel_requested；queued 的直接转 cancelled。"""
    run = await _get_run(db, run_id=run_id, owner_id=current_user.id, novel_id=novel.id)
    if run.status in ("cancelled", "completed"):
        return SkillRunView.model_validate(run)
    run.cancel_requested = True
    if run.status == "queued":
        run.status = "cancelled"
        run.status_reason = "cancelled_before_dispatch"
        run.error_code = "user_cancel"
    await db.commit()
    await db.refresh(run)
    return SkillRunView.model_validate(run)


@router.post(
    "/novels/{novel_id}/skill-runs/{run_id}/retry",
    response_model=SkillRunView,
)
async def retry_skill_run(
    novel_id: int,
    run_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> SkillRunView:
    """重试：仅终态（failed/cancelled）可重试 → 重置 queued 并递增重试计数。"""
    run = await _get_run(db, run_id=run_id, owner_id=current_user.id, novel_id=novel.id)
    if run.status in ("queued", "running"):
        return SkillRunView.model_validate(run)
    if run.status == "completed":
        raise HTTPException(status_code=409, detail="已完成运行不可重试")
    run.status = "queued"
    run.status_reason = None
    run.error_code = None
    run.cancel_requested = False
    run.retry_count = (run.retry_count or 0) + 1
    await db.commit()
    await db.refresh(run)
    return SkillRunView.model_validate(run)


@router.post(
    "/novels/{novel_id}/skill-runs/{run_id}/finalize",
    response_model=dict,
)
async def finalize_skill_run_endpoint(
    novel_id: int,
    run_id: int,
    payload: SkillRunFinalize,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> dict:
    """
    确定性 finalizer HTTP 端点（25.2-05 agent-service 在 agent_end 时触发）。

    - 唯一写 artifact/revision 的入口（其余分支 0 写，cancel-no-write 跨服务成立）。
    - 所有引证在 finalize 内用冻结 manifest 白名单校验；未知 ref → run failed、零写入。
    - 幂等：已完成 run 再次 finalize 返回现有状态，不重复写。
    """
    from app.services.agent_runtime.finalize import finalize_skill_run

    try:
        # 用当前请求事务绑定的 async engine 构造 sessionmaker（保持测试/生产同一 DB）。
        bind = db.bind
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        request_factory = async_sessionmaker(
            bind, class_=AsyncSession, expire_on_commit=False
        )
        outcome = await finalize_skill_run(
            request_factory,
            run_id=run_id,
            stop_reason=payload.stop_reason,
            envelope=payload.envelope,
            model_lineage=payload.model_lineage,
            source_versions=payload.source_versions,
            usage=payload.usage,
            frozen_manifest=payload.frozen_manifest,
        )
    except Exception as exc:  # noqa: BLE001 — 冻结码映射到 HTTP
        raise HTTPException(
            status_code=502,
            detail={"error": {"code": "upstream_error", "message": str(exc)}},
        ) from exc
    return {
        "run_id": run_id,
        "status": outcome.status,
        "error_code": outcome.error_code,
        "status_reason": outcome.status_reason,
        "artifact_id": outcome.artifact_id,
        "artifact_revision_id": outcome.artifact_revision_id,
    }


async def _get_run(
    db: AsyncSession,
    *,
    run_id: int,
    owner_id: int,
    novel_id: int,
) -> SkillRun:
    run = await db.scalar(
        select(SkillRun).where(
            SkillRun.id == run_id,
            SkillRun.owner_id == owner_id,
            SkillRun.novel_id == novel_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="技能运行不存在")
    return run


# ────────────────────────── 产物读取 ──────────────────────────


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


# ────────────────────────── 审批：唯一状态变更路径 ──────────────────────────


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


# ────────────────────────── 审批请求：唯一决策权威（D-11/D-15） ──────────────────────────


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
