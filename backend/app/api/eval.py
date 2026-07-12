"""
评测 API 端点 — RAG 评测运行与报告 + 06-04 质量 job 适配

端点 (legacy — 保留字段 + deprecation):
  POST /api/eval/runs
  GET  /api/eval/runs/{id}
  GET  /api/eval/runs
  GET  /api/eval/datasets
  PATCH /api/eval/datasets/{id}

端点 (quality durable jobs):
  POST /api/eval/quality/runs
  GET  /api/eval/quality/runs/{job_id}
  POST /api/eval/quality/runs/{job_id}/resume
  POST /api/eval/quality/runs/{job_id}/cancel
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_user
from app.core.database import get_db
from app.models.eval import EvalDataset, EvalRun
from app.models.novel import Novel
from app.models.user import User
from app.schemas.eval import (
    CalibrationReport,
    EvalCase,
    EvalDatasetResponse,
    EvalDatasetUpdate,
    EvalRunCreate,
    EvalRunResponse,
    ModelLineage,
    SourceSnapshot,
)
from app.services.eval_service import DEPRECATION_META, eval_service, EvalServiceError
from app.services.rag_quality import default_healthy
from app.services.rag_quality_worker import (
    QualityWorkerError,
    rag_quality_worker,
    quality_job_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eval", tags=["评测"])


def _owned_novel_clause(current_user: User):
    if current_user.is_superuser:
        return True
    return Novel.owner_id == current_user.id


async def _require_owned_novel(
    db: AsyncSession, novel_id: int, current_user: User
) -> Novel:
    result = await db.execute(
        select(Novel).where(
            Novel.id == novel_id,
            _owned_novel_clause(current_user),
        )
    )
    novel = result.scalar_one_or_none()
    if novel is None:
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


# ── Legacy endpoints ─────────────────────────────────────────────────


@router.post("/runs", response_model=dict)
async def trigger_eval_run(
    body: EvalRunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """触发一次评测运行（legacy retrieval；返回 deprecation + quality_comparable=false）"""
    await _require_owned_novel(db, body.novel_id, current_user)
    try:
        report = await eval_service.run_eval(
            db=db,
            run_name=body.run_name,
            strategy=body.strategy,
            novel_id=body.novel_id,
            dataset_ids=body.dataset_ids,
            quality_mode=False,
        )
        return {
            "status": "completed",
            "data": report,
            "job_id": report.get("job_id"),
            "quality_comparable": False,
            "deprecation": DEPRECATION_META,
        }
    except EvalServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("评测运行失败")
        raise HTTPException(status_code=500, detail=f"评测运行失败: {str(e)}")


@router.get("/runs/{run_id}", response_model=dict)
async def get_eval_report(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取评测运行报告"""
    owned_run = await db.execute(
        select(EvalRun.id)
        .join(Novel, Novel.id == EvalRun.novel_id)
        .where(
            EvalRun.id == run_id,
            _owned_novel_clause(current_user),
        )
    )
    if owned_run.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="评测运行不存在")
    try:
        report = await eval_service.get_run_report(db=db, run_id=run_id)
        return {
            "status": "ok",
            "data": report,
            "job_id": report.get("job_id"),
            "quality_comparable": report.get("quality_comparable", False),
            "deprecation": DEPRECATION_META,
        }
    except EvalServiceError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/runs", response_model=list[EvalRunResponse])
async def list_eval_runs(
    novel_id: int | None = Query(None, description="按小说 ID 过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """列出评测运行历史"""
    query = (
        select(EvalRun)
        .join(Novel, Novel.id == EvalRun.novel_id)
        .where(_owned_novel_clause(current_user))
        .order_by(EvalRun.created_at.desc())
    )
    if novel_id is not None:
        query = query.where(EvalRun.novel_id == novel_id)

    result = await db.execute(query)
    runs = result.scalars().all()
    return runs


@router.get("/datasets", response_model=list[EvalDatasetResponse])
async def list_eval_datasets(
    novel_id: int | None = Query(None, description="按小说 ID 过滤"),
    status: str | None = Query(None, description="按状态过滤: candidate/confirmed/rejected"),
    question_type: str | None = Query(None, description="按题型过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """列出评测数据集"""
    query = (
        select(EvalDataset)
        .join(Novel, Novel.id == EvalDataset.novel_id)
        .where(_owned_novel_clause(current_user))
    )

    if novel_id is not None:
        query = query.where(EvalDataset.novel_id == novel_id)
    if status is not None:
        query = query.where(EvalDataset.status == status)
    if question_type is not None:
        query = query.where(EvalDataset.question_type == question_type)

    query = query.order_by(EvalDataset.created_at.desc())
    result = await db.execute(query)
    datasets = result.scalars().all()
    return datasets


@router.patch("/datasets/{dataset_id}", response_model=EvalDatasetResponse)
async def update_eval_dataset(
    dataset_id: int,
    body: EvalDatasetUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """人工审核评测题（更新状态、gold_chunks 等）"""
    dataset = await db.get(EvalDataset, dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="评测题不存在")
    await _require_owned_novel(db, dataset.novel_id, current_user)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(dataset, field, value)

    await db.commit()
    await db.refresh(dataset)
    return dataset


# ── Quality durable job endpoints (06-04) ────────────────────────────


class QualityRunCreate(BaseModel):
    """Create a durable RAG quality evaluation job from signed fixtures."""

    snapshot: dict[str, Any]
    cases: list[dict[str, Any]] = Field(..., min_length=1)
    generator_lineage: dict[str, Any] | None = None
    judge_lineage: dict[str, Any] | None = None
    calibration_report: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None
    health: dict[str, Any] | None = None
    run_immediately: bool = True


@router.post("/quality/runs", response_model=dict)
async def create_quality_run(
    body: QualityRunCreate,
    current_user: User = Depends(require_user),
):
    """Create (and optionally run) a durable quality job."""
    try:
        snapshot = SourceSnapshot.model_validate(body.snapshot)
        cases = [EvalCase.model_validate(c) for c in body.cases]
        g = (
            ModelLineage.model_validate(body.generator_lineage)
            if body.generator_lineage
            else None
        )
        j = (
            ModelLineage.model_validate(body.judge_lineage)
            if body.judge_lineage
            else None
        )
        cal = body.calibration_report
        if isinstance(cal, dict):
            try:
                cal = CalibrationReport.model_validate(cal)
            except Exception:
                pass

        # Ownership: snapshot owner must match current user (unless superuser)
        if not current_user.is_superuser and snapshot.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="snapshot not found")

        job = rag_quality_worker.create_job(
            owner_id=current_user.id,
            snapshot=snapshot,
            cases=cases,
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline=body.baseline,
            health=body.health if body.health is not None else default_healthy(),
        )
        if body.run_immediately:
            job = rag_quality_worker.resume(job.job_id, owner_id=current_user.id)
        return {
            "status": job.status,
            "job_id": job.job_id,
            "quality_comparable": job.quality_comparable,
            "metrics": job.metrics if job.quality_comparable else None,
            "data": job.to_public(),
            "deprecation": DEPRECATION_META,
        }
    except HTTPException:
        raise
    except QualityWorkerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        logger.exception("quality run create failed")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/quality/runs/{job_id}", response_model=dict)
async def get_quality_run(
    job_id: str,
    current_user: User = Depends(require_user),
):
    try:
        public = rag_quality_worker.get_status(job_id, owner_id=current_user.id)
        return {
            "status": public["status"],
            "job_id": public["job_id"],
            "quality_comparable": public["quality_comparable"],
            "metrics": public["metrics"],
            "data": public,
            "deprecation": DEPRECATION_META,
        }
    except QualityWorkerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/quality/runs/{job_id}/resume", response_model=dict)
async def resume_quality_run(
    job_id: str,
    current_user: User = Depends(require_user),
):
    try:
        job = rag_quality_worker.resume(job_id, owner_id=current_user.id)
        return {
            "status": job.status,
            "job_id": job.job_id,
            "quality_comparable": job.quality_comparable,
            "metrics": job.metrics if job.quality_comparable else None,
            "data": job.to_public(),
            "deprecation": DEPRECATION_META,
        }
    except QualityWorkerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/quality/runs/{job_id}/cancel", response_model=dict)
async def cancel_quality_run(
    job_id: str,
    current_user: User = Depends(require_user),
):
    try:
        job = rag_quality_worker.request_cancel(job_id, owner_id=current_user.id)
        return {
            "status": job.status,
            "job_id": job.job_id,
            "quality_comparable": False,
            "metrics": None,
            "data": job.to_public(),
            "deprecation": DEPRECATION_META,
        }
    except QualityWorkerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/quality/runs", response_model=list[dict])
async def list_quality_runs(
    current_user: User = Depends(require_user),
):
    jobs = quality_job_store.list_for_owner(current_user.id)
    return [j.to_public() for j in jobs]
