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
from app.models.chunk_build import ChunkActivePointer, ChunkBuild
from app.models.eval import EvalDataset, EvalRun, QualityRun
from app.models.novel import Novel
from app.models.user import User
from app.schemas.eval import (
    CalibrationReport,
    ChunkerLineage,
    EvalCase,
    EvalDatasetResponse,
    EvalDatasetUpdate,
    EvalRunCreate,
    EvalRunResponse,
    ModelLineage,
    SourceSnapshot,
)
from app.services.eval_service import DEPRECATION_META, eval_service, EvalServiceError
from app.services.rag_quality import (
    BaselineServiceError,
    build_cross_chunker_report,
    canonicalize_chunker_lineage,
    commit_baseline_candidate,
    default_healthy,
    get_active_baseline,
    prepare_baseline_candidate,
    validate_calibrated_lineage,
    validate_fixtures_for_scoring,
)
from app.services.rag_quality_worker import (
    QualityRunRepository,
    QualityWorkerError,
    make_quality_worker,
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
    status: str | None = Query(
        None, description="按状态过滤: candidate/confirmed/rejected"
    ),
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
    # Five-tuple chunker/source lineage (required for comparable runs).
    chunker_lineage: dict[str, Any] | None = None
    run_immediately: bool = True


class QualityRunFromNovelCreate(BaseModel):
    """Start a product quality run from server-owned novel artifacts."""

    novel_id: int = Field(..., ge=1)
    dataset_ids: list[int] | None = None
    run_immediately: bool = True


@router.post("/quality/runs", response_model=dict)
async def create_quality_run(
    body: QualityRunCreate,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create (and optionally run) a durable quality job backed by QualityRun."""
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
        chunker = None
        if body.chunker_lineage:
            chunker = ChunkerLineage.model_validate(body.chunker_lineage)

        # Ownership: snapshot owner must match current user (unless superuser)
        if not current_user.is_superuser and snapshot.owner_id != current_user.id:
            raise HTTPException(status_code=404, detail="snapshot not found")

        worker = make_quality_worker(db)
        job = await worker.create_job(
            owner_id=current_user.id,
            snapshot=snapshot,
            cases=cases,
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline=body.baseline,
            health=body.health if body.health is not None else default_healthy(),
            chunker_lineage=chunker,
        )
        if body.run_immediately:
            job = await worker.resume(job.job_id, owner_id=current_user.id)
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


@router.post("/quality/runs/from-novel", response_model=dict)
async def create_quality_run_from_novel(
    body: QualityRunFromNovelCreate,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a durable run from confirmed datasets and active persisted lineage.

    This endpoint intentionally fails closed.  The browser supplies only a novel
    and optional confirmed dataset ids; signed fixtures, model calibration and
    chunk/source lineage are reloaded from server-owned durable records.
    """
    novel = await _require_owned_novel(db, body.novel_id, current_user)

    dataset_query = select(EvalDataset).where(
        EvalDataset.novel_id == novel.id,
        EvalDataset.status == "confirmed",
    )
    if body.dataset_ids is not None:
        requested_ids = set(body.dataset_ids)
        if not requested_ids:
            raise HTTPException(status_code=422, detail="至少选择一个已确认评测题")
        dataset_query = dataset_query.where(EvalDataset.id.in_(requested_ids))
    datasets = list((await db.scalars(dataset_query)).all())
    if not datasets:
        raise HTTPException(status_code=422, detail="小说没有已确认的评测题")
    if body.dataset_ids is not None and {row.id for row in datasets} != set(
        body.dataset_ids
    ):
        raise HTTPException(status_code=422, detail="评测题不存在或尚未确认")

    pointer = await db.scalar(
        select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel.id)
    )
    if pointer is None:
        raise HTTPException(status_code=422, detail="小说尚无 active chunk build")
    build = await db.scalar(
        select(ChunkBuild).where(
            ChunkBuild.novel_id == novel.id,
            ChunkBuild.build_id == pointer.build_id,
        )
    )
    if build is None or build.status != "committed" or build.is_candidate:
        raise HTTPException(status_code=422, detail="active chunk build 未可信发布")

    # A prior durable run is the only persisted production artifact that owns
    # the complete signed fixture + Generator/Judge calibration payload today.
    # Never reconstruct those identities from aliases or legacy gold chunk ids.
    seed_rows = list(
        (
            await db.scalars(
                select(QualityRun)
                .where(
                    QualityRun.owner_id == novel.owner_id,
                    QualityRun.work_id == novel.id,
                    QualityRun.source_snapshot_hash == build.source_snapshot_hash,
                    QualityRun.chunk_manifest_hash == build.manifest_checksum,
                )
                .order_by(QualityRun.created_at.desc())
            )
        ).all()
    )
    confirmed_questions = {row.question for row in datasets}
    selected_artifacts = None
    failure_reason = "缺少与 active build 匹配的可信质量谱系"

    for seed in seed_rows:
        payload = dict(seed.payload or {})
        try:
            snapshot = SourceSnapshot.model_validate(payload.get("snapshot"))
            if snapshot.owner_id != novel.owner_id or snapshot.work_id != novel.id:
                continue
            cases = [
                EvalCase.model_validate(case)
                for case in (payload.get("cases") or [])
                if case.get("question") in confirmed_questions
            ]
            if not cases or {case.question for case in cases} != confirmed_questions:
                failure_reason = "已确认评测题尚未生成完整冻结 fixture"
                continue
            generator = ModelLineage.model_validate(payload.get("generator_lineage"))
            judge = ModelLineage.model_validate(payload.get("judge_lineage"))
            calibration = CalibrationReport.model_validate(
                payload.get("calibration_report")
            )
            lineage, lineage_error = canonicalize_chunker_lineage(
                payload.get("chunker_lineage"),
                expected_source_snapshot_hash=snapshot.manifest_hash,
            )
            if lineage is None or lineage_error:
                failure_reason = lineage_error or failure_reason
                continue
            five = lineage.five_tuple()
            if five != {
                "chunker_name": build.chunker_name,
                "chunker_version": build.chunker_version,
                "chunker_config_hash": build.chunker_config_hash,
                "chunk_manifest_hash": build.manifest_checksum,
                "source_snapshot_hash": build.source_snapshot_hash,
            }:
                failure_reason = "质量谱系与 active chunk build 不一致"
                continue
            fixture_error = validate_fixtures_for_scoring(
                snapshot=snapshot, cases=cases
            )
            model_error = validate_calibrated_lineage(
                generator_lineage=generator,
                judge_lineage=judge,
                calibration_report=calibration,
            )
            if fixture_error or model_error:
                failure_reason = str(
                    (fixture_error or model_error or {}).get("reason")
                    or "冻结 fixture 或模型谱系无效"
                )
                continue
            selected_artifacts = (
                seed,
                snapshot,
                cases,
                generator,
                judge,
                calibration,
                lineage,
                payload,
            )
            break
        except (TypeError, ValueError, KeyError):
            continue

    if selected_artifacts is None:
        raise HTTPException(status_code=422, detail=failure_reason)

    seed, snapshot, cases, generator, judge, calibration, lineage, payload = (
        selected_artifacts
    )
    try:
        worker = make_quality_worker(db)
        job = await worker.create_job(
            owner_id=current_user.id,
            work_id=novel.id,
            snapshot=snapshot,
            cases=cases,
            generator_lineage=generator,
            judge_lineage=judge,
            calibration_report=calibration,
            baseline=payload.get("baseline"),
            health=payload.get("health"),
            chunker_lineage=lineage,
            extras={
                "origin": "from_novel",
                "dataset_ids": sorted(row.id for row in datasets),
                "seed_job_id": seed.job_id,
                "active_build_id": build.build_id,
            },
        )
        if body.run_immediately:
            job = await worker.resume(job.job_id, owner_id=current_user.id)
        return {
            "status": job.status,
            "job_id": job.job_id,
            "quality_comparable": job.quality_comparable,
            "metrics": job.metrics if job.quality_comparable else None,
            "data": job.to_public(),
            "source": {
                "novel_id": novel.id,
                "dataset_ids": sorted(row.id for row in datasets),
                "active_build_id": build.build_id,
            },
            "deprecation": DEPRECATION_META,
        }
    except QualityWorkerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)


@router.get("/quality/runs/{job_id}", response_model=dict)
async def get_quality_run(
    job_id: str,
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        worker = make_quality_worker(db)
        public = await worker.get_status(job_id, owner_id=current_user.id)
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
    db: AsyncSession = Depends(get_db),
):
    try:
        worker = make_quality_worker(db)
        job = await worker.resume(job_id, owner_id=current_user.id)
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
    db: AsyncSession = Depends(get_db),
):
    try:
        worker = make_quality_worker(db)
        job = await worker.request_cancel(job_id, owner_id=current_user.id)
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
    db: AsyncSession = Depends(get_db),
):
    repo = QualityRunRepository(db)
    jobs = await repo.list_for_owner(current_user.id)
    return [j.to_public() for j in jobs]


# ── Phase 06-09 baseline prepare/commit + cross-chunker report ───────


@router.post("/quality/baseline/prepare", response_model=dict)
async def prepare_quality_baseline(
    body: dict[str, Any],
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Prepare baseline candidate from a durable QualityRun (DB revalidated)."""
    job_id = (body or {}).get("job_id")
    if not job_id or not isinstance(job_id, str):
        raise HTTPException(status_code=400, detail="job_id is required")
    try:
        candidate = await prepare_baseline_candidate(
            db, owner_id=current_user.id, job_id=job_id
        )
        return {
            "status": "prepared",
            "candidate": candidate,
            "deprecation": DEPRECATION_META,
        }
    except BaselineServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.post("/quality/baseline/commit", response_model=dict)
async def commit_quality_baseline(
    body: dict[str, Any],
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Commit prepared candidate after reloading QualityRun lineage/hashes."""
    candidate_id = (body or {}).get("candidate_id")
    prepare_token = (body or {}).get("prepare_token")
    if not isinstance(candidate_id, int) or candidate_id < 1:
        raise HTTPException(status_code=400, detail="candidate_id is required")
    if not prepare_token or not isinstance(prepare_token, str):
        raise HTTPException(status_code=400, detail="prepare_token is required")
    try:
        result = await commit_baseline_candidate(
            db,
            owner_id=current_user.id,
            candidate_id=candidate_id,
            prepare_token=prepare_token,
        )
        if not result.get("ok"):
            return {
                "status": "rejected",
                "candidate": result.get("candidate"),
                "active": result.get("active"),
                "error": result.get("error"),
                "deprecation": DEPRECATION_META,
            }
        return {
            "status": "committed",
            "candidate": result.get("candidate"),
            "active": result.get("active"),
            "idempotent": result.get("idempotent", False),
            "deprecation": DEPRECATION_META,
        }
    except BaselineServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


@router.get("/quality/baseline/active", response_model=dict)
async def get_quality_active_baseline(
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    active = await get_active_baseline(db, owner_id=current_user.id)
    return {
        "active": active,
        "deprecation": DEPRECATION_META,
    }


@router.post("/quality/reports/cross-chunker", response_model=dict)
async def cross_chunker_quality_report(
    body: dict[str, Any],
    current_user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Same-snapshot multi-chunker comparable report with explicit exclusions."""
    snap = (body or {}).get("source_snapshot_hash")
    if not snap or not isinstance(snap, str):
        raise HTTPException(status_code=400, detail="source_snapshot_hash is required")
    try:
        report = await build_cross_chunker_report(
            db, owner_id=current_user.id, source_snapshot_hash=snap
        )
        return {**report, "deprecation": DEPRECATION_META}
    except BaselineServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
