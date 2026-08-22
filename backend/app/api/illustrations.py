"""Owner-scoped illustration generation API (Phase 33-02, REQ-VIS-04).

Candidate-only, durable-job, provider-neutral endpoints:

- ``POST /api/novels/{novel_id}/illustrations/generate`` — create one durable
  idempotent generation job. The server-side generation gate only accepts an
  **approved** PromptRevision whose Visual Bible / source-snapshot lineage is
  **not stale**; a duplicate idempotency key replays the existing job (one
  charge, one result) and a terminal failed job must be retried explicitly.
- ``GET  /api/novels/{novel_id}/illustrations/jobs`` / ``.../jobs/{job_id}`` —
  durable job read envelopes (explicit status/error/reason).
- ``POST /api/novels/{novel_id}/illustrations/jobs/{job_id}/retry`` — re-queue
  an eligible terminal/paused job with the original frozen lineage.
- ``GET  /api/novels/{novel_id}/illustrations/assets`` / ``.../assets/{asset_id}``
  — candidate-only immutable AssetRevision read envelopes (never canon).
- ``GET  /api/novels/{novel_id}/illustrations/assets/{asset_id}/bytes`` —
  owner-scoped asset bytes for the candidate gallery (raw paths never exposed).
- ``POST .../assets/{asset_id}/consistency/evaluate`` — run the frozen-fixture
  consistency evaluator and persist a versioned evidence report (idempotent
  replay; a duplicate report_key with different evidence fails closed).
- ``GET  .../assets/{asset_id}/consistency`` / ``.../consistency/compare`` —
  read-only consistency evidence and candidate-vs-report compare.
- ``GET  /api/novels/{novel_id}/illustrations/consistency-reports`` — list all
  reports for the novel.
- ``GET  /api/novels/{novel_id}/illustrations/gallery`` — candidate gallery
  for human review (job status/error/retry + consistency + approval gate).
- ``GET  .../assets/{asset_id}/review`` — full review envelope: lineage
  drawer (job/attempt/budget evidence) + compare + review history + gate.
- ``POST .../assets/{asset_id}/review`` — append one explicit human approval
  action (approve/reject/supersede/needs_relink). Approvals re-run the
  fail-closed proposal gate (succeeded job, complete lineage, cleared rights,
  settled budget, visible consistency report); a repeated event_key replays.
  Nothing here publishes: Phase 33 ends at proposal_ready for Phase 34.

Every route uses ``require_owned_novel``; a prompt/job/asset outside the
caller's owner/novel scope is indistinguishable from "not found". No route
promotes a generated candidate and nothing here becomes reader visible
(D-33-03, Phase 33 ends at candidate for Phase 34). Consistency scores are
review signals with evaluator/model/fixture lineage and can never auto-approve
(D-33-04).

本文件只保留路由 + view builders。``IllustrationJobService`` 与资产字节 seam 已
迁至 ``app/services/illustrations/job_service.py``，一致性 evaluator fixture
seam 已迁至 ``app/services/illustrations/consistency.py``，wire DTO 已归位
``app/schemas/illustration.py``。此处保留 ``IllustrationGenerationRequest``
（依赖 worker 的 MOCK 缺省值以稳定 OpenAPI default）及一致性响应 DTO
（依赖服务层 ``ConsistencyReportView``），并 re-export 测试依赖的符号以保持
既有 import 面（``app.api.illustrations``）。
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    Response,
)
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.illustration import AssetRevision
from app.models.illustration_job import IllustrationJob
from app.schemas.illustration import (
    AssetListResponse,
    AssetRevisionView,
    ConsistencyEvaluateRequest,
    IllustrationCreateJobResponse,
    IllustrationJobListResponse,
    IllustrationJobView,
    IllustrationReviewActionRequest,
    IllustrationReviewEventInput,
    StrictIllustrationModel,
)
from app.services.illustrations.consistency import (
    CandidateConsistencyEvidence,
    ConsistencyReportConflict,
    ConsistencyReportNotFound,
    ConsistencyReportService,
    ConsistencyReportView,
    consistency_evaluator,
    report_view,
    set_illustration_consistency_fixtures,
)
from app.services.illustrations.gateway import GenerationGateError
from app.services.illustrations.job_service import (
    IllustrationJobConflict,
    IllustrationJobNotFound,
    IllustrationJobService,
    set_illustration_asset_storage,
    storage,
)
from app.services.illustrations.review import (
    IllustrationGalleryResponse,
    IllustrationReviewActionResponse,
    IllustrationReviewEnvelope,
    IllustrationReviewGateError,
    IllustrationReviewNotFound,
    IllustrationReviewService,
    build_gallery,
    build_review_envelope,
)
from app.services.illustrations.storage import AssetNotFound
from app.services.illustrations.worker import (
    MOCK_ILLUSTRATION_MODEL,
    MOCK_ILLUSTRATION_PROVIDER,
    MOCK_IMAGE_HEIGHT,
    MOCK_IMAGE_WIDTH,
    dispatch_illustration_job,
)

router = APIRouter(dependencies=[Depends(require_user)])


class StrictWireModel(StrictIllustrationModel):
    model_config = ConfigDict(extra="forbid")


class IllustrationGenerationRequest(StrictWireModel):
    """Explicit generation request; scope comes from the path, never the body."""

    prompt_revision_id: int = Field(gt=0)
    job_key: str = Field(min_length=1, max_length=120)
    provider: str = Field(
        default=MOCK_ILLUSTRATION_PROVIDER, min_length=1, max_length=64
    )
    model: str = Field(default=MOCK_ILLUSTRATION_MODEL, min_length=1, max_length=120)
    width: int = Field(default=MOCK_IMAGE_WIDTH, ge=16, le=4096)
    height: int = Field(default=MOCK_IMAGE_HEIGHT, ge=16, le=4096)


class ConsistencyEvaluateResponse(StrictWireModel):
    report: ConsistencyReportView
    replayed: bool = False


class ConsistencyReportListResponse(StrictWireModel):
    items: list[ConsistencyReportView]
    total: int


class ConsistencyCompareResponse(StrictWireModel):
    candidate: AssetRevisionView
    report: ConsistencyReportView | None = None


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------


def _job_view(job: IllustrationJob) -> IllustrationJobView:
    return IllustrationJobView(
        id=job.id,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        job_key=job.job_key,
        idempotency_key=job.idempotency_key,
        status=job.status,
        status_reason=job.status_reason,
        error_code=job.error_code,
        retry_count=job.retry_count,
        scene_spec_hash=job.scene_spec_hash,
        prompt_revision_id=job.prompt_revision_id,
        prompt_revision_hash=job.prompt_revision_hash,
        visual_bible_revision_hash=job.visual_bible_revision_hash,
        source_snapshot_id=job.source_snapshot_id,
        source_snapshot_hash=job.source_snapshot_hash,
        cutoff_chapter=job.cutoff_chapter,
        config_hash=job.config_hash,
        price_snapshot=dict(job.price_snapshot or {}),
    )


def _asset_view(asset: AssetRevision) -> AssetRevisionView:
    return AssetRevisionView(
        id=asset.id,
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        job_id=asset.job_id,
        revision_key=asset.revision_key,
        revision_number=asset.revision_number,
        asset_id=asset.asset_id,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        size_bytes=asset.size_bytes,
        bytes_hash=asset.bytes_hash,
        scene_spec_hash=asset.scene_spec_hash,
        prompt_revision_id=asset.prompt_revision_id,
        prompt_revision_hash=asset.prompt_revision_hash,
        visual_bible_revision_hash=asset.visual_bible_revision_hash,
        source_snapshot_id=asset.source_snapshot_id,
        source_snapshot_hash=asset.source_snapshot_hash,
        cutoff_chapter=asset.cutoff_chapter,
        provider=asset.provider,
        provider_model=asset.provider_model,
        provider_request_id=asset.provider_request_id,
        rights_status=asset.rights_status,
        approval_state=asset.approval_state,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="小说不存在")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _gate_exception(exc: GenerationGateError) -> HTTPException:
    if exc.reason_code == "prompt_revision_not_found":
        return _not_found()
    return _conflict(str(exc))


# ---------------------------------------------------------------------------
# Read routes (owner-scoped, candidate-only)
# ---------------------------------------------------------------------------


@router.get(
    "/{novel_id}/illustrations/jobs",
    response_model=IllustrationJobListResponse,
)
async def list_illustration_jobs(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    jobs = await IllustrationJobService(db).list_jobs(
        owner_id=current_user.id, novel_id=novel.id
    )
    return IllustrationJobListResponse(
        items=[_job_view(job) for job in jobs], total=len(jobs)
    )


@router.get(
    "/{novel_id}/illustrations/jobs/{job_id}",
    response_model=IllustrationJobView,
)
async def get_illustration_job(
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        job = await IllustrationJobService(db).get_job(
            owner_id=current_user.id, novel_id=novel.id, job_id=job_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    return _job_view(job)


@router.get(
    "/{novel_id}/illustrations/assets",
    response_model=AssetListResponse,
)
async def list_illustration_assets(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    assets = await IllustrationJobService(db).list_assets(
        owner_id=current_user.id, novel_id=novel.id
    )
    return AssetListResponse(
        items=[_asset_view(asset) for asset in assets], total=len(assets)
    )


@router.get(
    "/{novel_id}/illustrations/assets/{asset_id}",
    response_model=AssetRevisionView,
)
async def get_illustration_asset(
    asset_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    try:
        asset = await IllustrationJobService(db).get_asset(
            owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    return _asset_view(asset)


@router.get("/{novel_id}/illustrations/assets/{asset_id}/bytes")
async def get_illustration_asset_bytes(
    asset_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Owner-scoped candidate asset bytes; raw paths are never exposed."""
    try:
        asset = await IllustrationJobService(db).get_asset(
            owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    try:
        payload = storage().read(
            owner_id=current_user.id,
            novel_id=novel.id,
            storage_key=asset.storage_key,
        )
    except AssetNotFound:
        raise HTTPException(status_code=404, detail="asset bytes missing") from None
    return Response(content=payload, media_type=asset.mime_type)


# ---------------------------------------------------------------------------
# Write routes (server-gated, idempotent, durable)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/illustrations/generate",
    response_model=IllustrationCreateJobResponse,
    status_code=201,
)
async def generate_illustration(
    payload: IllustrationGenerationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Create one durable idempotent generation job for an approved prompt.

    The server-side gate only accepts an approved + non-stale PromptRevision;
    a duplicate idempotency key replays the existing job (one charge, one
    result). Background dispatch is durable: repeated dispatch is safe.
    """
    owner_id = current_user.id
    novel_id = novel.id
    service = IllustrationJobService(db)
    try:
        job, replayed = await service.create_job(
            owner_id=owner_id, novel_id=novel_id, request=payload
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    except GenerationGateError as exc:
        raise _gate_exception(exc) from exc
    except IllustrationJobConflict as exc:
        raise _conflict(str(exc)) from exc

    # Commit before BackgroundTasks so the worker session can see the new job.
    await db.commit()
    dispatch_enabled = getattr(request.app.state, "illustration_dispatch_enabled", True)
    if dispatch_enabled:
        background_tasks.add_task(dispatch_illustration_job, job.id)
    return IllustrationCreateJobResponse(job=_job_view(job), replayed=replayed)


@router.post(
    "/{novel_id}/illustrations/jobs/{job_id}/retry",
    response_model=IllustrationJobView,
)
async def retry_illustration_job(
    job_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Explicitly re-queue an eligible terminal/paused job (original lineage)."""
    service = IllustrationJobService(db)
    try:
        job = await service.retry_job(
            owner_id=current_user.id, novel_id=novel.id, job_id=job_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    except IllustrationJobConflict as exc:
        raise _conflict(str(exc)) from exc
    await db.commit()
    dispatch_enabled = getattr(request.app.state, "illustration_dispatch_enabled", True)
    if dispatch_enabled:
        background_tasks.add_task(dispatch_illustration_job, job.id)
    return _job_view(job)


# ---------------------------------------------------------------------------
# Consistency evidence routes (D-33-04: review signals, never canon)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/illustrations/assets/{asset_id}/consistency/evaluate",
    response_model=ConsistencyEvaluateResponse,
    status_code=201,
)
async def evaluate_asset_consistency(
    asset_id: int,
    payload: ConsistencyEvaluateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Run the frozen-fixture evaluator and persist one versioned report.

    The candidate is owner-scoped (the asset must belong to the caller's
    novel). A duplicate ``report_key`` with identical evidence replays the
    existing report; a different evaluation under the same key fails closed.
    The score is evidence for human review and can never approve the candidate.
    """
    service = ConsistencyReportService(db, evaluator=consistency_evaluator())
    report_key = payload.report_key or f"{payload.character_key}:{payload.scene_key}"
    evidence = CandidateConsistencyEvidence(
        character_key=payload.character_key,
        scene_key=payload.scene_key,
        identity_attributes=tuple(payload.identity_attributes),
        style_attributes=tuple(payload.style_attributes),
        negative_constraints_present=tuple(payload.negative_constraints_present),
    )
    try:
        report, replayed = await service.evaluate(
            owner_id=current_user.id,
            novel_id=novel.id,
            asset_revision_id=asset_id,
            report_key=report_key,
            evidence=evidence,
        )
    except ConsistencyReportNotFound:
        raise _not_found() from None
    except ConsistencyReportConflict as exc:
        raise _conflict(str(exc)) from exc
    await db.commit()
    return ConsistencyEvaluateResponse(report=report_view(report), replayed=replayed)


@router.get(
    "/{novel_id}/illustrations/assets/{asset_id}/consistency",
    response_model=ConsistencyReportView,
)
async def get_asset_consistency_report(
    asset_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Read the latest consistency evidence for one candidate asset."""
    try:
        await IllustrationJobService(db).get_asset(
            owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    report = await ConsistencyReportService(
        db, evaluator=consistency_evaluator()
    ).get_latest(
        owner_id=current_user.id, novel_id=novel.id, asset_revision_id=asset_id
    )
    if report is None:
        raise HTTPException(
            status_code=404, detail="no consistency report for this asset"
        )
    return report_view(report)


@router.get(
    "/{novel_id}/illustrations/assets/{asset_id}/consistency/compare",
    response_model=ConsistencyCompareResponse,
)
async def compare_asset_consistency(
    asset_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Read-only compare: candidate asset + its latest consistency evidence."""
    service = IllustrationJobService(db)
    try:
        asset = await service.get_asset(
            owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except IllustrationJobNotFound:
        raise _not_found() from None
    report = await ConsistencyReportService(
        db, evaluator=consistency_evaluator()
    ).get_latest(
        owner_id=current_user.id, novel_id=novel.id, asset_revision_id=asset.id
    )
    return ConsistencyCompareResponse(
        candidate=_asset_view(asset),
        report=report_view(report) if report is not None else None,
    )


@router.get(
    "/{novel_id}/illustrations/consistency-reports",
    response_model=ConsistencyReportListResponse,
)
async def list_consistency_reports(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List all consistency evidence reports for the caller's novel."""
    reports = await ConsistencyReportService(
        db, evaluator=consistency_evaluator()
    ).list_reports(owner_id=current_user.id, novel_id=novel.id)
    return ConsistencyReportListResponse(
        items=[report_view(report) for report in reports], total=len(reports)
    )


# ---------------------------------------------------------------------------
# Review / approval routes (Phase 33-04: explicit, append-only, candidate-only)
# ---------------------------------------------------------------------------


@router.get(
    "/{novel_id}/illustrations/gallery",
    response_model=IllustrationGalleryResponse,
)
async def get_illustration_review_gallery(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Candidate gallery for human review (job status/error/retry visible).

    Every item stays candidate-only: job status, consistency evidence and
    approval-gate reason codes are review signals, never an automatic approval.
    """
    return await build_gallery(db, owner_id=current_user.id, novel_id=novel.id)


@router.get(
    "/{novel_id}/illustrations/assets/{asset_id}/review",
    response_model=IllustrationReviewEnvelope,
)
async def get_illustration_review_envelope(
    asset_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Full review envelope: lineage drawer + compare + history + gate."""
    try:
        return await build_review_envelope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            asset_id=asset_id,
        )
    except IllustrationReviewNotFound:
        raise _not_found() from None


@router.post(
    "/{novel_id}/illustrations/assets/{asset_id}/review",
    response_model=IllustrationReviewActionResponse,
)
async def review_illustration_asset(
    asset_id: int,
    payload: IllustrationReviewActionRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Append one explicit human approval action (approve/reject/supersede).

    The server re-verifies owner/novel/asset scope, the current approval state
    and the legal transition, and (for approvals) the persisted proposal gate —
    successful job, complete lineage, cleared rights, settled budget evidence
    and a visible consistency report. A repeated ``event_key`` replays without a
    second event. Nothing here publishes or becomes reader visible (Phase 33
    ends at proposal_ready for Phase 34).
    """
    owner_id = current_user.id
    novel_id = novel.id
    event = IllustrationReviewEventInput(
        owner_id=owner_id,
        novel_id=novel_id,
        asset_revision_id=asset_id,
        event_key=payload.event_key,
        action=payload.action,
        actor_source=payload.actor_source,
        actor=payload.actor,
        reason=payload.reason,
        from_approval_state=payload.from_approval_state,
    )
    service = IllustrationReviewService(db)
    try:
        asset = await service.append_event(
            owner_id=owner_id, novel_id=novel_id, event=event
        )
    except IllustrationReviewNotFound:
        raise _not_found() from None
    except IllustrationReviewGateError as exc:
        raise _conflict(str(exc)) from exc
    await db.commit()
    envelope = await build_review_envelope(
        db, owner_id=owner_id, novel_id=novel_id, asset_id=asset.id
    )
    return IllustrationReviewActionResponse(asset=_asset_view(asset), envelope=envelope)


# ---------------------------------------------------------------------------
# Re-export compat (tests import these from app.api.illustrations)
# ---------------------------------------------------------------------------

__all__ = [
    "IllustrationGenerationRequest",
    "IllustrationJobService",
    "set_illustration_asset_storage",
    "set_illustration_consistency_fixtures",
]
