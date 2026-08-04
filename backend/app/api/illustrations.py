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

Every route uses ``require_owned_novel``; a prompt/job/asset outside the
caller's owner/novel scope is indistinguishable from "not found". No route
promotes a generated candidate and nothing here becomes reader visible
(D-33-03, Phase 33 ends at candidate for Phase 34).
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.illustration import AssetRevision
from app.models.illustration_job import (
    ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
    IllustrationJob,
)
from app.schemas.illustration import (
    AssetRevisionView,
    IllustrationJobContract,
    IllustrationJobView,
    PriceSnapshot,
    StrictIllustrationModel,
    build_illustration_idempotency_key,
    validate_illustration_job_contract,
)
from app.services.illustrations.gateway import (
    GenerationGateError,
    build_illustration_lineage,
    check_generation_prompt_gate,
)
from app.services.illustrations.storage import AssetStorage, AssetNotFound
from app.services.illustrations.worker import (
    DEFAULT_MAX_INPUT_TOKENS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    MOCK_ILLUSTRATION_MODEL,
    MOCK_ILLUSTRATION_PROVIDER,
    MOCK_IMAGE_HEIGHT,
    MOCK_IMAGE_WIDTH,
    dispatch_illustration_job,
)

router = APIRouter(dependencies=[Depends(require_user)])

# Only the deterministic mock provider is configured in this slice. A
# provider-neutral API means the request surface is provider-independent; a
# non-configured provider fails closed before any job is created.
SUPPORTED_ILLUSTRATION_PROVIDERS = frozenset({MOCK_ILLUSTRATION_PROVIDER})


class StrictWireModel(StrictIllustrationModel):
    model_config = ConfigDict(extra="forbid")


class IllustrationGenerationRequest(StrictWireModel):
    """Explicit generation request; scope comes from the path, never the body."""

    prompt_revision_id: int = Field(gt=0)
    job_key: str = Field(min_length=1, max_length=120)
    provider: str = Field(default=MOCK_ILLUSTRATION_PROVIDER, min_length=1, max_length=64)
    model: str = Field(default=MOCK_ILLUSTRATION_MODEL, min_length=1, max_length=120)
    width: int = Field(default=MOCK_IMAGE_WIDTH, ge=16, le=4096)
    height: int = Field(default=MOCK_IMAGE_HEIGHT, ge=16, le=4096)


class IllustrationJobListResponse(StrictWireModel):
    items: list[IllustrationJobView]
    total: int


class IllustrationCreateJobResponse(StrictWireModel):
    job: IllustrationJobView
    replayed: bool = False


class AssetListResponse(StrictWireModel):
    items: list[AssetRevisionView]
    total: int


class IllustrationJobConflict(ValueError):
    """A conflicting durable-job state that cannot replay."""


class IllustrationJobNotFound(ValueError):
    """A job is outside the explicit owner/novel scope (404-equivalent)."""


# ---------------------------------------------------------------------------
# Illustration job service (durable, idempotent, owner-scoped)
# ---------------------------------------------------------------------------


class IllustrationJobService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_job(
        self, *, owner_id: int, novel_id: int, request: IllustrationGenerationRequest
    ) -> tuple[IllustrationJob, bool]:
        """Server-side gated, idempotent durable job creation (D-33-01).

        Only an approved + non-stale PromptRevision may generate. A duplicate
        idempotency key replays the existing nonterminal/succeeded job; a
        terminal failed job must go through the explicit retry route.
        """
        if request.provider not in SUPPORTED_ILLUSTRATION_PROVIDERS:
            raise IllustrationJobConflict(
                f"illustration provider {request.provider!r} is not configured; "
                f"supported: {sorted(SUPPORTED_ILLUSTRATION_PROVIDERS)}"
            )
        prompt_row = await check_generation_prompt_gate(
            self._session,
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_revision_id=request.prompt_revision_id,
        )
        lineage = build_illustration_lineage(
            prompt_revision=prompt_row,
            provider=request.provider,
            model=request.model,
            width=request.width,
            height=request.height,
            max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )
        idempotency_key = build_illustration_idempotency_key(
            owner_id, novel_id, lineage
        )
        price_snapshot = _price_snapshot_for(request.provider, request.model)
        job_contract = IllustrationJobContract(
            schema_version="illustration.v1",
            artifact_kind="illustration_job",
            owner_id=owner_id,
            novel_id=novel_id,
            job_key=request.job_key,
            lineage=lineage,
            price_snapshot=price_snapshot.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        validate_illustration_job_contract(job_contract)

        existing = await self._job_by_idempotency_key(idempotency_key)
        if existing is not None:
            if (
                existing.status in ILLUSTRATION_JOB_NONTERMINAL_STATUSES
                or existing.status == "succeeded"
            ):
                return existing, True
            raise IllustrationJobConflict(
                "a terminal job with this lineage already exists; retry it "
                "explicitly instead of resubmitting"
            )

        row = IllustrationJob(
            owner_id=owner_id,
            novel_id=novel_id,
            job_key=request.job_key,
            idempotency_key=idempotency_key,
            status="queued",
            status_reason=None,
            error_code=None,
            lease_id=None,
            lease_expires_at=None,
            heartbeat_at=None,
            cancel_requested=False,
            retry_count=0,
            scene_spec_hash=lineage.scene_spec_hash,
            prompt_revision_id=lineage.prompt_revision_id,
            prompt_revision_hash=lineage.prompt_revision_hash,
            visual_bible_revision_id=lineage.visual_bible_revision_id,
            visual_bible_revision_hash=lineage.visual_bible_revision_hash,
            source_snapshot_id=lineage.source_snapshot_id,
            source_snapshot_hash=lineage.source_snapshot_hash,
            cutoff_chapter=lineage.cutoff_chapter,
            model_lineage=dict(lineage.model_lineage),
            config_hash=lineage.config_hash,
            price_snapshot=price_snapshot.model_dump(mode="json"),
            response_hash=None,
            schema_version="illustration.v1",
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._job_by_idempotency_key(idempotency_key)
            if existing is None:
                raise IllustrationJobConflict(
                    "job create race: existing row not found after rollback"
                ) from None
            return existing, True
        return row, False

    async def list_jobs(self, *, owner_id: int, novel_id: int) -> list[IllustrationJob]:
        rows = (
            await self._session.scalars(
                select(IllustrationJob)
                .where(
                    IllustrationJob.owner_id == owner_id,
                    IllustrationJob.novel_id == novel_id,
                )
                .order_by(IllustrationJob.id.desc())
            )
        ).all()
        return list(rows)

    async def get_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob:
        job = await self._job_by_id(
            owner_id=owner_id, novel_id=novel_id, job_id=job_id
        )
        if job is None:
            raise IllustrationJobNotFound("illustration job not found in the owner/novel scope")
        return job

    async def retry_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob:
        """Re-queue an eligible terminal/paused job with the frozen lineage."""
        job = await self._job_by_id(
            owner_id=owner_id, novel_id=novel_id, job_id=job_id
        )
        if job is None:
            raise IllustrationJobNotFound("illustration job not found in the owner/novel scope")
        eligible = {
            "failed",
            "cancelled",
            "outcome_unknown",
            "paused_budget",
            "paused_dependency",
        }
        if job.status not in eligible:
            raise IllustrationJobConflict(
                f"job status {job.status!r} is not eligible for retry"
            )
        job.status = "queued"
        job.error_code = None
        job.status_reason = None
        await self._session.flush()
        return job

    async def list_assets(
        self, *, owner_id: int, novel_id: int
    ) -> list[AssetRevision]:
        rows = (
            await self._session.scalars(
                select(AssetRevision)
                .where(
                    AssetRevision.owner_id == owner_id,
                    AssetRevision.novel_id == novel_id,
                )
                .order_by(AssetRevision.id.desc())
            )
        ).all()
        return list(rows)

    async def get_asset(
        self, *, owner_id: int, novel_id: int, asset_id: int
    ) -> AssetRevision:
        asset = await self._session.scalar(
            select(AssetRevision).where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
                AssetRevision.id == asset_id,
            )
        )
        if asset is None:
            raise IllustrationJobNotFound(
                "illustration asset not found in the owner/novel scope"
            )
        return asset

    async def _job_by_id(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob | None:
        return await self._session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.owner_id == owner_id,
                IllustrationJob.novel_id == novel_id,
                IllustrationJob.id == job_id,
            )
        )

    async def _job_by_idempotency_key(
        self, idempotency_key: str
    ) -> IllustrationJob | None:
        return await self._session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.idempotency_key == idempotency_key
            )
        )


def _price_snapshot_for(provider: str, model: str) -> PriceSnapshot:
    """Frozen mock pricing; cost is always settled against this snapshot."""
    return PriceSnapshot(
        provider=provider,
        model=model,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.10"),
        image_price_per_image=Decimal("0.04"),
    )


# ---------------------------------------------------------------------------
# Asset storage seam (test override + deployment default)
# ---------------------------------------------------------------------------

_asset_storage: AssetStorage | None = None


def set_illustration_asset_storage(storage: AssetStorage | None) -> None:
    """Override the bytes backend (used by integration tests)."""
    global _asset_storage
    _asset_storage = storage


def _storage() -> AssetStorage:
    if _asset_storage is not None:
        return _asset_storage
    return AssetStorage(AssetStorage.default_storage_root())


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
    return AssetListResponse(items=[_asset_view(asset) for asset in assets], total=len(assets))


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
        payload = _storage().read(
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
