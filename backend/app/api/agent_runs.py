"""SkillRun API：技能运行接受 / 列表 / 取消 / 重试 / finalize（25.2-03 / D-09..D-14）。

从原 ``app/api/agent.py`` 拆出：本文件只承载 SkillRun 生命周期域路由。
- ``POST /novels/{novel_id}/skill-runs`` — 202 接受 + commit-before-dispatch
  （只持久化/授权并铸造 per-run 内部令牌；真正的分发由 agent-service 按
  25.2-05 Task 4 调用本端点触发，绝不 FastAPI→agent-service）。
- ``GET  /novels/{novel_id}/skill-runs`` — 分页列出某小说的技能运行。
- ``POST .../skill-runs/{run_id}/cancel`` — 请求取消（queued 直接转 cancelled）。
- ``POST .../skill-runs/{run_id}/retry`` — 仅终态（failed/cancelled）可重试。
- ``POST .../skill-runs/{run_id}/finalize`` — 确定性 finalizer（agent-service
  在 agent_end 时触发；唯一写 artifact/revision 的入口，幂等）。

路由形状沿用原 agent.py：router 级 ``Depends(require_user)``，小说域资源经
``Depends(require_owned_novel)``（404-hide）。分页统一 {"items","total","skip","limit"}。
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_agent_actor, require_user
from app.models import Novel, SkillRun, User
from app.schemas.agent_runtime import (
    SkillRunAccepted,
    SkillRunCreate,
    SkillRunFinalize,
    SkillRunView,
    ConnectorRuntimeManifest,
)
from app.services.agent_runtime import registry as registry_service
from app.services.agent_runtime.registry import canonical_input_hash
from app.services.tool_connectors.policy import ConnectorPolicyError
from app.services.tool_connectors.service import freeze_connector_versions

router = APIRouter(dependencies=[Depends(require_agent_actor)])


def _view_from_run(row) -> dict[str, Any]:
    return SkillRunView.model_validate(row).model_dump(mode="json")


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
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        skill_version_id=data.skill_version_id,
    )
    if version is None:
        raise HTTPException(status_code=404, detail="技能版本不存在")
    if version.status != "active":
        raise HTTPException(status_code=422, detail="技能版本不可用")

    # 输入规范化：novel_id 以路径为准，不能被请求体伪造到其它小说。
    input_payload = dict(data.input)
    input_payload["novel_id"] = novel.id
    # SSE 端点是问答驱动：只有 answer-reading-question 强制 input.question。
    # 分析/生图类 skill（illustrate-scene 等）用锚定字段，question 可缺省。
    if version.name == "answer-reading-question" and (
        not isinstance(input_payload.get("question"), str)
        or not input_payload["question"].strip()
    ):
        raise HTTPException(status_code=422, detail="input.question 必须为非空字符串")

    try:
        runtime_manifest = registry_service.skill_runtime_manifest(version)
        connector_versions = await freeze_connector_versions(
            db, owner_id=current_user.id, allowed_tools=list(version.allowed_tools or [])
        )
        runtime_manifest = runtime_manifest.model_copy(
            update={
                "connector_versions": [
                    ConnectorRuntimeManifest.model_validate(item)
                    for item in connector_versions
                ]
            }
        )
    except (registry_service.SkillContractError, ConnectorPolicyError) as exc:
        raise HTTPException(status_code=409, detail="技能版本契约校验失败") from exc

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
        frozen_manifest={"connector_versions": connector_versions},
        budget_snapshot=dict(version.budget or {}),
        internal_token_hash=token_hash,
    )
    db.add(run)
    await db.flush()
    # commit-before-dispatch：run 立即可见（worker / agent-service 新会话可读到）。
    await db.commit()
    await db.refresh(run)
    return SkillRunAccepted(
        run=SkillRunView.model_validate(run),
        internal_token=internal_token,
        runtime_manifest=runtime_manifest,
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
    actor=Depends(require_agent_actor),
) -> SkillRunView:
    """请求取消运行并立即进入无写入的 cancelled 终态。"""
    run = await _get_run(db, run_id=run_id, owner_id=actor.id, novel_id=novel.id)
    if run.status in ("cancelled", "completed"):
        return SkillRunView.model_validate(run)
    run.cancel_requested = True
    if run.status in ("queued", "running"):
        was_running = run.status == "running"
        run.status = "cancelled"
        run.status_reason = (
            "cancelled_during_execution" if was_running else "cancelled_before_dispatch"
        )
        run.error_code = "user_cancel"
        if run.origin == "chat_backfill":
            from app.services.agent_runtime.reader_bridge import (
                _reconcile_reader_chat_after_backfill_in_session,
            )

            await _reconcile_reader_chat_after_backfill_in_session(db, run)
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
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
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
        # Pi-backed runs: finalize 成功后由 background task 做确定性物化。
        # chat_backfill 只写 candidate 域表；reader_chat 投影回原 ReaderConversation。
        if outcome.status == "completed" and outcome.artifact_id is not None:
            run_row = await db.get(SkillRun, run_id)
            if run_row is not None and run_row.origin in ("chat_backfill", "reader_chat"):
                from app.services.agent_runtime.materialize import (
                    materialize_skill_run,
                )

                background_tasks.add_task(
                    materialize_skill_run, request_factory, run_id
                )
            elif run_row is not None and run_row.origin == "chapter_batch":
                from app.services.agent_runtime.chapter_batch import (
                    continue_chapter_batch_after_finalize,
                )

                background_tasks.add_task(
                    continue_chapter_batch_after_finalize,
                    request_factory,
                    run_id,
                )
        elif outcome.status in ("failed", "cancelled"):
            run_row = await db.get(SkillRun, run_id)
            if run_row is not None and run_row.origin == "chat_backfill":
                from app.services.agent_runtime.reader_bridge import (
                    reconcile_reader_chat_after_backfill,
                )

                background_tasks.add_task(
                    reconcile_reader_chat_after_backfill, run_id, sessions=request_factory
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
