"""Service-to-service 端点：agent-service queued-run poller（25.2-05）。

从原 ``app/api/agent.py`` 拆出：本文件只承载 agent-service poller 专用端点，
使用独立的 ``service_router`` APIRouter 对象（gateway token 认证，无用户 JWT）。
只暴露 queued-runs 列表与 claim——绝不暴露用户数据，run 归属经 service 内校验兜底。
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_gateway_token
from app.models import SkillRun, SkillVersion

# Service-to-service router：agent-service poller 专用（gateway token 认证）。
service_router = APIRouter()

_LEASE_WINDOW_MINUTES = 30


@service_router.get("/queued-runs", response_model=dict)
async def list_queued_runs(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_gateway_token),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """agent-service poller：列出 queued 的 chat_backfill 运行（service token）。

    返回 run 上下文（含 input / skill_version_id / input_hash / branch），
    但不返回 internal_token（库中只存 hash）。claim 时铸造新 token。
    """
    rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(
                    SkillRun.origin == "chat_backfill",
                    SkillRun.status == "queued",
                )
                .order_by(SkillRun.id.asc())
                .limit(limit)
            )
        ).all()
    )
    items = []
    for r in rows:
        items.append(
            {
                "run_id": r.id,
                "owner_id": r.owner_id,
                "novel_id": r.novel_id,
                "skill_version_id": r.skill_version_id,
                "input": dict(r.input or {}),
                "input_hash": r.input_hash,
                "branch": r.branch,
                "backfill_dimension": r.backfill_dimension,
            }
        )
    return {"items": items, "total": len(items)}


@service_router.post("/queued-runs/{run_id}/claim", response_model=dict)
async def claim_queued_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_gateway_token),
) -> dict:
    """agent-service poller：原子 claim 一个 queued 的 chat_backfill 运行。

    - 原子转变 queued → running（冲突者 409）；running 且 updated_at 超过
      lease 窗口（poller crash 恢复）可重新 claim。
    - 铸造新 per-run internal_token（只存 hash），返回明文给 poller。
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    lease_cutoff = now - timedelta(minutes=_LEASE_WINDOW_MINUTES)

    row = await db.execute(
        text(
            """
            UPDATE skill_runs
            SET status = 'running',
                internal_token_hash = :token_hash,
                updated_at = now()
            WHERE id = :run_id
              AND origin = 'chat_backfill'
              AND (
                status = 'queued'
                OR (status = 'running' AND updated_at < :lease_cutoff)
              )
            RETURNING id, owner_id, novel_id, skill_version_id, input, input_hash,
                      branch, backfill_dimension, frozen_manifest, budget_snapshot
            """
        ),
        {
            "run_id": run_id,
            "token_hash": "",
            "lease_cutoff": lease_cutoff,
        },
    )
    claimed = row.mappings().first()
    if claimed is None:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="运行不可 claim（非 queued 或已在途）"
        )

    internal_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(internal_token.encode("utf-8")).hexdigest()
    await db.execute(
        text("UPDATE skill_runs SET internal_token_hash = :h WHERE id = :id"),
        {"h": token_hash, "id": run_id},
    )
    skill_name = await db.scalar(
        select(SkillVersion).where(SkillVersion.id == claimed["skill_version_id"])
    )
    skill_name_value = skill_name.name if skill_name is not None else None
    await db.commit()

    return {
        "run_id": claimed["id"],
        "owner_id": claimed["owner_id"],
        "novel_id": claimed["novel_id"],
        "skill_version_id": claimed["skill_version_id"],
        "skill_name": skill_name_value,
        "input": dict(claimed["input"] or {}),
        "input_hash": claimed["input_hash"],
        "branch": claimed["branch"],
        "backfill_dimension": claimed["backfill_dimension"],
        "frozen_manifest": dict(claimed["frozen_manifest"] or {}),
        "budget_snapshot": dict(claimed["budget_snapshot"] or {}),
        "internal_token": internal_token,
    }
