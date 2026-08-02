"""ApprovalRequest 持久化与唯一决策门禁（25.3-04 / D-11 / D-15 / T-25.3-04-02）。

规则（镜像 artifacts.py 的 25.2-03 状态迁移门禁哲学）:
  - pending→approved | approved_for_session | rejected | expired | cancelled；
    已决（非 pending）为终态，拒绝重复决策（稳定 conflict）。
  - confirm/reject/expire_request 是**唯一**写 status 的函数；任何其它代码路径
    直接改 status 都是伪造（approval forgery threat）——由 grep 验收 + 测试证明。
  - owner 检查在 service 内强制：非 owner → 返回 None（404-hide，绝不 403 oracle）。
  - 过期：expires_at 已过且仍 pending → 就地标记 expired（决策被拒绝）。
  - SSE 帧只通知、浏览器只渲染；决策权威只在 FastAPI（D-11）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest
from app.schemas.agent_approvals import ApprovalRequestCreate


class ApprovalStateError(RuntimeError):
    """非 pending 的重复决策 / 过期决策被拒绝（稳定 conflict）。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_past_due(req: ApprovalRequest) -> bool:
    return req.status == "pending" and req.expires_at is not None and _utcnow() > req.expires_at


async def create(
    db: AsyncSession,
    *,
    owner_id: int,
    payload: ApprovalRequestCreate,
) -> ApprovalRequest:
    """铸造审批请求（agent-service 触发；owner 以当前认证用户为权威）。

    显式 payload.owner_id 与认证用户不符 → ApprovalStateError（伪造防御）。
    """
    if payload.owner_id is not None and payload.owner_id != owner_id:
        raise ApprovalStateError("approval owner 与认证用户不一致（拒绝铸造）")
    request = ApprovalRequest(
        owner_id=owner_id,
        run_id=payload.run_id,
        skill_version_id=payload.skill_version_id,
        artifact_id=payload.artifact_id,
        artifact_revision_id=payload.artifact_revision_id,
        novel_id=payload.novel_id,
        branch_id=payload.branch_id,
        fork_id=payload.fork_id,
        action=payload.action,
        payload_summary=dict(payload.payload_summary),
        payload_hash=payload.payload_hash,
        status="pending",
        expires_at=payload.expires_at,
    )
    db.add(request)
    await db.flush()
    return request


async def confirm(
    db: AsyncSession,
    *,
    request_id: int,
    owner_id: int,
    mode: str,
) -> ApprovalRequest | None:
    """owner 确认：pending → approved | approved_for_session（D-11 一次/会话）。

    非 owner → None（404-hide）；非 pending / 已过期 → ApprovalStateError。
    """
    request = await _get_for_owner(db, request_id=request_id, owner_id=owner_id)
    if request is None:
        return None
    if _is_past_due(request):
        request.status = "expired"  # expire 决策：过期先行，拒绝决定
        request.decided_at = _utcnow()
        await db.flush()
        raise ApprovalStateError(f"approval request {request_id} has expired")
    if request.status != "pending":
        raise ApprovalStateError(
            f"approval request {request_id} is {request.status!r} (terminal, no re-decision)"
        )
    request.status = "approved" if mode == "once" else "approved_for_session"  # confirm 决策
    request.decided_at = _utcnow()
    request.decision_actor_id = owner_id
    await db.flush()
    return request


async def reject(
    db: AsyncSession,
    *,
    request_id: int,
    owner_id: int,
) -> ApprovalRequest | None:
    """owner 拒绝：pending → rejected（终态）。非 owner → None（404-hide）。"""
    request = await _get_for_owner(db, request_id=request_id, owner_id=owner_id)
    if request is None:
        return None
    if _is_past_due(request):
        request.status = "expired"  # expire 决策：过期先行，拒绝决定
        request.decided_at = _utcnow()
        await db.flush()
        raise ApprovalStateError(f"approval request {request_id} has expired")
    if request.status != "pending":
        raise ApprovalStateError(
            f"approval request {request_id} is {request.status!r} (terminal, no re-decision)"
        )
    request.status = "rejected"  # reject 决策
    request.decided_at = _utcnow()
    request.decision_actor_id = owner_id
    await db.flush()
    return request


async def expire_request(
    db: AsyncSession,
    *,
    request_id: int,
    owner_id: int,
) -> ApprovalRequest | None:
    """run 结束/超时主动过期：pending → expired（agent-service 或 run 清理调用）。"""
    request = await _get_for_owner(db, request_id=request_id, owner_id=owner_id)
    if request is None:
        return None
    if request.status != "pending":
        return request  # 已决为终态，无需再动
    request.status = "expired"  # expire 决策
    request.decided_at = _utcnow()
    await db.flush()
    return request


async def get_for_owner(
    db: AsyncSession,
    *,
    request_id: int,
    owner_id: int,
) -> ApprovalRequest | None:
    """owner 隔离读取（短轮询端点用）。pending 且已过 expires_at → 就地标记 expired。"""
    request = await _get_for_owner(db, request_id=request_id, owner_id=owner_id)
    if request is None:
        return None
    if _is_past_due(request):
        request.status = "expired"  # expire 决策：短轮询看到过期即停
        request.decided_at = _utcnow()
        await db.flush()
    return request


async def list_for_owner(
    db: AsyncSession,
    *,
    owner_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[ApprovalRequest], int]:
    """分页列出 owner 的审批请求（{"items","total","skip","limit"}）。"""
    where = (ApprovalRequest.owner_id == owner_id,)
    total = await db.scalar(
        select(func.count()).select_from(ApprovalRequest).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(ApprovalRequest)
                .where(*where)
                .order_by(ApprovalRequest.id.desc())
                .offset(skip)
                .limit(limit)
            )
        ).all()
    )
    return rows, int(total or 0)


async def _get_for_owner(
    db: AsyncSession,
    *,
    request_id: int,
    owner_id: int,
) -> ApprovalRequest | None:
    return await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == request_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
