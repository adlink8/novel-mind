"""Owner-scoped constrained derivative generation API (Phase 37-02, D-37-02).

Agent-candidate / script-publish boundary: a generation job accepts only an
owned sealed context package and a job idempotency key; the runner routes one
provider call through ai_router/ai_service, parses the strict-schema candidate
and persists only a candidate row (``candidate | blocked | needs_override``).
No route can write Original Canon or an active pointer, and a terminal job is
never silently re-called (recovery is an explicit run of a paused/queued job).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.derivative_context import ContextPackageRecord
from app.models.derivative_generation_job import (
    DERIVATIVE_GENERATION_NONTERMINAL_STATUSES,
    DERIVATIVE_GENERATION_SCHEMA_VERSION,
    DerivativeGenerationAttempt,
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.schemas.derivative_generation import (
    AttemptView,
    CandidateView,
    GenerationIntent,
    GenerationJobCancelResponse,
    GenerationJobCreateRequest,
    GenerationJobCreateResponse,
    GenerationJobDetailResponse,
    GenerationJobListResponse,
    GenerationJobRunResponse,
    GenerationJobSummary,
    GenerationJobView,
)
from app.services.derivative_generation.candidate import schema_hash
from app.services.derivative_generation.context_package import (
    ContextPackageError,
    verify_package_hash,
)
from app.services.derivative_generation.runner import (
    AIServiceTransport,
    BudgetExceeded,
    CandidateRunError,
    CandidateRunResult,
    DEFAULT_DERIVATIVE_BUDGET,
    DerivativeBudgetGate,
    DerivativeCandidateRunner,
    ModelTransport,
    UnknownPricing,
    build_generation_idempotency_key,
    config_hash,
    prompt_hash,
)

router = APIRouter(dependencies=[Depends(require_user)])


# ---------------------------------------------------------------------------
# FastAPI dependencies (test seams for the fake gateway / budget gate)
# ---------------------------------------------------------------------------


def get_derivative_transport() -> ModelTransport:
    """Default transport wraps the existing ai_service gateway (D-37-02)."""
    return AIServiceTransport()


def get_derivative_budget_gate() -> DerivativeBudgetGate:
    return DerivativeBudgetGate(DEFAULT_DERIVATIVE_BUDGET)


# ---------------------------------------------------------------------------
# Domain errors -> HTTP
# ---------------------------------------------------------------------------


class GenerationJobConflict(ValueError):
    """Deterministic job gate violation with an HTTP status code."""

    def __init__(self, code: str, detail: str, status_code: int = 409):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


def _map_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", type(exc).__name__)
    detail = getattr(exc, "detail", str(exc))
    status_code = getattr(exc, "status_code", 400)
    return HTTPException(status_code=status_code, detail=f"{code}: {detail}")


# ---------------------------------------------------------------------------
# View mappers
# ---------------------------------------------------------------------------


def _to_job_view(row: DerivativeGenerationJob) -> GenerationJobView:
    return GenerationJobView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        fork_id=row.fork_id,
        context_package_id=row.context_package_id,
        package_hash=row.package_hash,
        intent=GenerationIntent(row.intent),
        job_key=row.job_key,
        idempotency_key=row.idempotency_key,
        status=row.status,
        status_reason=row.status_reason,
        error_code=row.error_code,
        retry_count=row.retry_count,
        prompt_hash=row.prompt_hash,
        schema_hash=row.schema_hash,
        config_hash=row.config_hash,
        model_lineage=dict(row.model_lineage or {}),
        price_snapshot=dict(row.price_snapshot or {}),
        budget_policy=dict(row.budget_policy or {}),
        response_hash=row.response_hash,
        schema_version=row.schema_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_summary(row: DerivativeGenerationJob) -> GenerationJobSummary:
    return GenerationJobSummary(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        job_key=row.job_key,
        intent=GenerationIntent(row.intent),
        status=row.status,
        error_code=row.error_code,
        package_hash=row.package_hash,
        created_at=row.created_at,
    )


def _to_candidate_view(row: DerivativeGenerationCandidate) -> CandidateView:
    return CandidateView(
        id=row.id,
        job_id=row.job_id,
        intent=GenerationIntent(row.intent),
        draft_text=row.draft_text,
        summary=row.summary,
        citation_keys=list(row.citation_keys or []),
        divergence=dict(row.divergence or {}),
        branch_suggestions=list(row.branch_suggestions or []),
        canon_delta_hash=row.canon_delta_hash,
        gate_verdict=row.gate_verdict,
        gate_reason=row.gate_reason,
        package_hash=row.package_hash,
        prompt_hash=row.prompt_hash,
        schema_hash=row.schema_hash,
        request_hash=row.request_hash,
        response_hash=row.response_hash,
        usage=dict(row.usage or {}),
        cost_usd=row.cost_usd,
        model_lineage=dict(row.model_lineage or {}),
        approval_state=row.approval_state,
        schema_version=row.schema_version,
        created_at=row.created_at,
    )


def _to_attempt_view(row: DerivativeGenerationAttempt) -> AttemptView:
    return AttemptView(
        id=row.id,
        job_id=row.job_id,
        attempt_number=row.attempt_number,
        status=row.status,
        provider=row.provider,
        model_id=row.model_id,
        provider_request_id=row.provider_request_id,
        request_hash=row.request_hash,
        response_hash=row.response_hash,
        reservation_key=row.reservation_key,
        reserved_input_tokens=row.reserved_input_tokens,
        reserved_output_tokens=row.reserved_output_tokens,
        reserved_cost_usd=row.reserved_cost_usd,
        usage=dict(row.usage or {}),
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        error_code=row.error_code,
    )


# ---------------------------------------------------------------------------
# Job service (create / run / cancel / query) — thin API orchestration
# ---------------------------------------------------------------------------


class DerivativeGenerationJobService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        transport: ModelTransport,
        budget_gate: DerivativeBudgetGate,
    ) -> None:
        self._session = session
        self._runner = DerivativeCandidateRunner(
            session, transport=transport, budget_gate=budget_gate
        )

    async def create_job(
        self,
        *,
        owner_id: int,
        novel_id: int,
        context_package_id: int,
        intent: str,
        job_key: str,
    ) -> tuple[DerivativeGenerationJob, bool]:
        """Create a queued job for one owned sealed package (idempotent)."""
        package = await self._session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.id == context_package_id,
                ContextPackageRecord.owner_id == owner_id,
                ContextPackageRecord.novel_id == novel_id,
            )
        )
        if package is None:
            raise GenerationJobConflict(
                "package_not_found",
                "context package not found in the owner/novel scope",
                status_code=404,
            )
        try:
            verify_package_hash(
                dict(package.canonical_payload or {}), package.package_hash
            )
        except ContextPackageError as exc:
            raise GenerationJobConflict(
                "package_hash_mismatch", exc.detail, status_code=409
            ) from exc
        if package.intent != intent:
            raise GenerationJobConflict(
                "intent_mismatch",
                f"sealed package intent {package.intent!r} != requested {intent!r}",
                status_code=409,
            )

        idempotency_key = build_generation_idempotency_key(
            owner_id,
            novel_id,
            package_hash=package.package_hash,
            intent=intent,
            job_key=job_key,
        )
        existing = await self._session.scalar(
            select(DerivativeGenerationJob).where(
                DerivativeGenerationJob.idempotency_key == idempotency_key,
                DerivativeGenerationJob.owner_id == owner_id,
                DerivativeGenerationJob.novel_id == novel_id,
            )
        )
        if existing is not None:
            if existing.status in DERIVATIVE_GENERATION_NONTERMINAL_STATUSES or (
                existing.status in ("succeeded", "blocked", "needs_override")
            ):
                return existing, True
            raise GenerationJobConflict(
                "terminal_job_exists",
                "a terminal job with this lineage already exists; retry it "
                "explicitly instead of resubmitting",
                status_code=409,
            )

        payload = dict(package.canonical_payload or {})
        row = DerivativeGenerationJob(
            owner_id=owner_id,
            novel_id=novel_id,
            fork_id=package.fork_id,
            context_package_id=package.id,
            package_hash=package.package_hash,
            intent=intent,
            job_key=job_key,
            idempotency_key=idempotency_key,
            status="queued",
            prompt_hash=prompt_hash(payload, intent=intent),
            schema_hash=schema_hash(),
            config_hash=config_hash(),
            model_lineage={},
            price_snapshot={},
            budget_policy=self._runner.budget_policy.as_dict(),  # frozen snapshot
            schema_version=DERIVATIVE_GENERATION_SCHEMA_VERSION,
        )
        self._session.add(row)
        await self._session.flush()
        return row, False

    async def run_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> CandidateRunResult:
        return await self._runner.run(
            owner_id=owner_id, novel_id=novel_id, job_id=job_id
        )

    async def cancel_job(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> DerivativeGenerationJob:
        row = await self._session.scalar(
            select(DerivativeGenerationJob)
            .where(
                DerivativeGenerationJob.id == job_id,
                DerivativeGenerationJob.owner_id == owner_id,
                DerivativeGenerationJob.novel_id == novel_id,
            )
            .with_for_update()
        )
        if row is None:
            raise GenerationJobConflict(
                "job_not_found",
                "generation job not found in the owner/novel scope",
                status_code=404,
            )
        if row.status not in DERIVATIVE_GENERATION_NONTERMINAL_STATUSES:
            raise GenerationJobConflict(
                "job_not_cancellable",
                f"job {row.id} is {row.status!r}; only recoverable jobs can be cancelled",
                status_code=409,
            )
        row.cancel_requested = True
        if row.status != "running":
            row.status = "cancelled"
            row.status_reason = "cancelled by the owner"
            row.error_code = "cancelled"
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def list_jobs(
        self, *, owner_id: int, novel_id: int
    ) -> list[DerivativeGenerationJob]:
        return list(
            (
                await self._session.scalars(
                    select(DerivativeGenerationJob)
                    .where(
                        DerivativeGenerationJob.owner_id == owner_id,
                        DerivativeGenerationJob.novel_id == novel_id,
                    )
                    .order_by(DerivativeGenerationJob.id.desc())
                )
            ).all()
        )

    async def get_detail(
        self, *, owner_id: int, novel_id: int, job_id: int
    ) -> tuple[
        DerivativeGenerationJob,
        DerivativeGenerationCandidate | None,
        list[DerivativeGenerationAttempt],
    ]:
        row = await self._session.scalar(
            select(DerivativeGenerationJob).where(
                DerivativeGenerationJob.id == job_id,
                DerivativeGenerationJob.owner_id == owner_id,
                DerivativeGenerationJob.novel_id == novel_id,
            )
        )
        if row is None:
            raise GenerationJobConflict(
                "job_not_found",
                "generation job not found in the owner/novel scope",
                status_code=404,
            )
        candidate = await self._session.scalar(
            select(DerivativeGenerationCandidate).where(
                DerivativeGenerationCandidate.job_id == row.id
            )
        )
        attempts = list(
            (
                await self._session.scalars(
                    select(DerivativeGenerationAttempt)
                    .where(DerivativeGenerationAttempt.job_id == row.id)
                    .order_by(DerivativeGenerationAttempt.attempt_number)
                )
            ).all()
        )
        return row, candidate, attempts


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/derivative-generation-jobs",
    response_model=GenerationJobCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_generation_job(
    body: GenerationJobCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    transport: ModelTransport = Depends(get_derivative_transport),
    budget_gate: DerivativeBudgetGate = Depends(get_derivative_budget_gate),
) -> GenerationJobCreateResponse:
    """Create a queued generation job for one owned sealed package.

    A duplicate idempotency key replays the existing nonterminal/succeeded
    job — one charge, one candidate. A package outside the owner/novel scope is
    an identical 404; a mismatched intent is a 409.
    """
    service = DerivativeGenerationJobService(
        db, transport=transport, budget_gate=budget_gate
    )
    try:
        job, replayed = await service.create_job(
            owner_id=current_user.id,
            novel_id=novel.id,
            context_package_id=body.context_package_id,
            intent=body.intent.value,
            job_key=body.job_key,
        )
    except GenerationJobConflict as exc:
        raise _map_error(exc) from exc
    return GenerationJobCreateResponse(
        job=_to_job_view(job),
        replayed=replayed,
        message=(
            "generation job replayed from the existing nonterminal job"
            if replayed
            else "generation job created (queued)"
        ),
    )


@router.get(
    "/{novel_id}/derivative-generation-jobs",
    response_model=GenerationJobListResponse,
)
async def list_generation_jobs(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    transport: ModelTransport = Depends(get_derivative_transport),
    budget_gate: DerivativeBudgetGate = Depends(get_derivative_budget_gate),
) -> GenerationJobListResponse:
    """List the owner's derivative generation jobs for one novel."""
    service = DerivativeGenerationJobService(
        db, transport=transport, budget_gate=budget_gate
    )
    rows = await service.list_jobs(owner_id=current_user.id, novel_id=novel.id)
    return GenerationJobListResponse(
        novel_id=novel.id, total=len(rows), items=[_to_summary(r) for r in rows]
    )


@router.get(
    "/{novel_id}/derivative-generation-jobs/{job_id}",
    response_model=GenerationJobDetailResponse,
)
async def get_generation_job(
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    transport: ModelTransport = Depends(get_derivative_transport),
    budget_gate: DerivativeBudgetGate = Depends(get_derivative_budget_gate),
) -> GenerationJobDetailResponse:
    """Read one job with its candidate (if any) and attempt lineage."""
    service = DerivativeGenerationJobService(
        db, transport=transport, budget_gate=budget_gate
    )
    try:
        job, candidate, attempts = await service.get_detail(
            owner_id=current_user.id, novel_id=novel.id, job_id=job_id
        )
    except GenerationJobConflict as exc:
        raise _map_error(exc) from exc
    return GenerationJobDetailResponse(
        job=_to_job_view(job),
        candidate=_to_candidate_view(candidate) if candidate is not None else None,
        attempts=[_to_attempt_view(a) for a in attempts],
    )


@router.post(
    "/{novel_id}/derivative-generation-jobs/{job_id}/run",
    response_model=GenerationJobRunResponse,
)
async def run_generation_job(
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    transport: ModelTransport = Depends(get_derivative_transport),
    budget_gate: DerivativeBudgetGate = Depends(get_derivative_budget_gate),
) -> GenerationJobRunResponse:
    """Execute the budgeted candidate run (sealed package -> candidate gate).

    A paused/queued job is recoverable; a terminal job returns 409
    (``job_not_runnable``) and is never silently re-called. Budget overruns and
    schema violations never call or publish.
    """
    service = DerivativeGenerationJobService(
        db, transport=transport, budget_gate=budget_gate
    )
    try:
        result = await service.run_job(
            owner_id=current_user.id, novel_id=novel.id, job_id=job_id
        )
    except (CandidateRunError, BudgetExceeded, UnknownPricing) as exc:
        raise _map_error(exc) from exc
    return GenerationJobRunResponse(
        job=_to_job_view(result.job),
        candidate=(
            _to_candidate_view(result.candidate)
            if result.candidate is not None
            else None
        ),
        attempts=[_to_attempt_view(a) for a in result.attempts],
    )


@router.post(
    "/{novel_id}/derivative-generation-jobs/{job_id}/cancel",
    response_model=GenerationJobCancelResponse,
)
async def cancel_generation_job(
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    transport: ModelTransport = Depends(get_derivative_transport),
    budget_gate: DerivativeBudgetGate = Depends(get_derivative_budget_gate),
) -> GenerationJobCancelResponse:
    """Cancel a recoverable job; a run then returns ``cancelled`` without a call."""
    service = DerivativeGenerationJobService(
        db, transport=transport, budget_gate=budget_gate
    )
    try:
        job = await service.cancel_job(
            owner_id=current_user.id, novel_id=novel.id, job_id=job_id
        )
    except GenerationJobConflict as exc:
        raise _map_error(exc) from exc
    return GenerationJobCancelResponse(
        job=_to_job_view(job),
        message=(
            "generation job cancelled"
            if job.status == "cancelled"
            else "cancel requested; the in-flight run will not be re-called"
        ),
    )


__all__ = ["router"]
