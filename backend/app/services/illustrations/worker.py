"""Durable illustration generation worker (Phase 33-02, REQ-VIS-04).

Authority boundaries (mirrors ``reader_chat/worker.py``):
- Reads frozen job lineage and the approved/fresh PromptRevision only; it never
  rebuilds the source lineage on retry (D-33-01 immutable source/prompt/model
  lineage).
- Writes only ``illustration_*`` and ``asset_revisions`` rows plus asset bytes
  through ``AssetStorage``. No reader/export/publish path exists (D-33-03).
- Re-runs the server-side generation gate before every execution so a prompt
  that became stale or unapproved after job creation fails closed.

Lifecycle (D-33-01/D-33-02):
- ``_claim_job`` → lease/heartbeat claim; ``_load_job_context`` re-validates
  the approved + fresh prompt; ``_has_succeeded_asset`` makes a re-dispatch
  idempotent (no second provider call, no second charge).
- ``_execute_attempts`` runs bounded, reason-coded attempts. Each attempt
  reserves worst-case cost through ``DurableIllustrationBudgetRepository``
  before the provider call and settles with explicit usage/cost after; a
  timeout/5xx/disconnect is an explicit ``outcome_unknown`` (reconcilable by
  request id/hash) and an empty/invalid payload is an explicit failure — a
  provider failure never becomes a successful empty asset.
- A successful attempt persists one immutable candidate ``AssetRevision`` and
  finishes the job ``succeeded``; a successful attempt is never repeated.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import async_session_factory
from app.models.illustration import AssetRevision
from app.models.illustration_job import (
    ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
    IllustrationAttempt,
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
)
from app.schemas.illustration import (
    AssetRevisionContract,
    IllustrationJobContract,
    IllustrationLineage,
    PriceSnapshot,
    canonical_illustration_hash,
    validate_asset_bytes,
    validate_asset_revision_contract,
    validate_illustration_job_contract,
)
from app.services.illustrations.budget import (
    BudgetExceeded,
    DEFAULT_ILLUSTRATION_POLICY,
    IllustrationBudgetPolicy,
    UnknownPricing,
    worst_case_cost_usd,
)
from app.services.illustrations.gateway import (
    GenerationGateError,
    GatewayAttempt,
    IllustrationBudget,
    IllustrationGateway,
    ProviderOutcomeUnknown,
    ProviderRejected,
    check_generation_prompt_gate,
)
from app.services.illustrations.storage import AssetStorage

logger = logging.getLogger(__name__)
# Hashes / IDs only — never raw prompt, provider response, or model output.
_SAFE_LOG = logging.getLogger("illustrations.worker.audit")

MOCK_ILLUSTRATION_PROVIDER = "mock"
MOCK_ILLUSTRATION_MODEL = "mock-img-v1"
MOCK_IMAGE_WIDTH = 1024
MOCK_IMAGE_HEIGHT = 1024
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_INPUT_TOKENS = 4096
DEFAULT_MAX_OUTPUT_TOKENS = 2048
LEASE_MINUTES = 5


class IllustrationWorkerError(RuntimeError):
    pass


def default_illustration_price_snapshot() -> PriceSnapshot:
    """Frozen deployment price snapshot for the deterministic mock provider."""
    return PriceSnapshot(
        provider=MOCK_ILLUSTRATION_PROVIDER,
        model=MOCK_ILLUSTRATION_MODEL,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.10"),
        image_price_per_image=Decimal("0.04"),
    )


@dataclass(frozen=True)
class IllustrationWorkerRuntime:
    sessions: async_sessionmaker[AsyncSession]
    gateway: IllustrationGateway
    storage: AssetStorage
    price_snapshot: PriceSnapshot = field(default_factory=default_illustration_price_snapshot)
    budget_policy: IllustrationBudgetPolicy = DEFAULT_ILLUSTRATION_POLICY
    width: int = MOCK_IMAGE_WIDTH
    height: int = MOCK_IMAGE_HEIGHT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    timeout: float = 30.0


# ---------------------------------------------------------------------------
# Durable budget repository (D-33-02): ledger + reservation rows
# ---------------------------------------------------------------------------


class DurableIllustrationBudgetRepository:
    """Implements the gateway budget seam by persisting ledger/reservation rows.

    Uses the same session as the job execution transaction. Worst-case
    reservation happens before any provider call and fails closed on budget
    exhaustion / unknown pricing; settlement records explicit usage/cost and
    keeps unknown usage/cost explicit (``settle_unknown``).
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        policy: IllustrationBudgetPolicy,
    ) -> None:
        self._session = session
        self._owner_id = owner_id
        self._novel_id = novel_id
        self._policy = policy

    async def _ledger(self) -> IllustrationBudgetLedger:
        row = await self._session.scalar(
            select(IllustrationBudgetLedger).where(
                IllustrationBudgetLedger.owner_id == self._owner_id,
                IllustrationBudgetLedger.novel_id == self._novel_id,
            )
        )
        if row is None:
            row = IllustrationBudgetLedger(
                owner_id=self._owner_id,
                novel_id=self._novel_id,
                max_calls=self._policy.max_calls,
                max_cost_usd=self._policy.max_cost_usd,
            )
            self._session.add(row)
            await self._session.flush()
        return row

    async def _reservation(self, key: str) -> IllustrationBudgetReservation:
        ledger = await self._ledger()
        row = await self._session.scalar(
            select(IllustrationBudgetReservation).where(
                IllustrationBudgetReservation.ledger_id == ledger.id,
                IllustrationBudgetReservation.reservation_key == key,
            )
        )
        if row is None:
            raise ValueError(f"budget reservation {key!r} not found")
        return row

    async def reserve(
        self,
        *,
        key: str,
        calls: int,
        input_tokens: int,
        output_tokens: int,
        price_snapshot: PriceSnapshot,
    ) -> IllustrationBudgetReservation:
        ledger = await self._ledger()
        cost = worst_case_cost_usd(
            price_snapshot,
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        # Lifetime novel budget: active reservations + already-settled calls.
        total_calls = (ledger.reserved_calls or 0) + (ledger.settled_calls or 0)
        total_cost = (ledger.reserved_cost_usd or Decimal(0)) + (
            ledger.settled_cost_usd or Decimal(0)
        )
        if total_calls + calls > ledger.max_calls:
            raise BudgetExceeded("novel illustration call budget exhausted")
        if total_cost + cost > ledger.max_cost_usd:
            raise BudgetExceeded("novel illustration cost budget exhausted")
        row = IllustrationBudgetReservation(
            ledger_id=ledger.id,
            reservation_key=key,
            status="reserved",
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            price_snapshot=price_snapshot.model_dump(mode="json"),
            settled_usage={},
        )
        self._session.add(row)
        await self._session.flush()
        ledger.reserved_calls = total_calls + calls
        ledger.reserved_cost_usd = total_cost + cost
        await self._session.flush()
        return row

    async def settle(
        self,
        *,
        key: str,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        row = await self._reservation(key)
        if row.status != "reserved":
            raise ValueError("reservation already transitioned")
        if (
            actual_input_tokens > row.input_tokens
            or actual_output_tokens > row.output_tokens
        ):
            raise BudgetExceeded("provider usage exceeded the reserved worst case")
        row.status = "settled"
        row.input_tokens = actual_input_tokens
        row.output_tokens = actual_output_tokens
        row.cost_usd = actual_cost_usd
        row.settled_usage = {
            "input_tokens": actual_input_tokens,
            "output_tokens": actual_output_tokens,
            "cost_usd": str(actual_cost_usd),
            "usage_unknown": False,
        }
        ledger = await self._ledger()
        ledger.reserved_calls = max(0, (ledger.reserved_calls or 0) - row.calls)
        ledger.reserved_cost_usd = max(
            Decimal(0), (ledger.reserved_cost_usd or Decimal(0)) - row.cost_usd
        )
        ledger.settled_calls = (ledger.settled_calls or 0) + 1
        ledger.settled_cost_usd = (ledger.settled_cost_usd or Decimal(0)) + actual_cost_usd
        await self._session.flush()

    async def settle_unknown(self, *, key: str, error_code: str) -> None:
        row = await self._reservation(key)
        if row.status != "reserved":
            raise ValueError("reservation already transitioned")
        row.status = "settled"
        row.settled_usage = {
            "usage_unknown": True,
            "cost_usd": None,
            "error_code": error_code,
        }
        ledger = await self._ledger()
        ledger.reserved_calls = max(0, (ledger.reserved_calls or 0) - row.calls)
        ledger.reserved_cost_usd = max(
            Decimal(0), (ledger.reserved_cost_usd or Decimal(0)) - row.cost_usd
        )
        # A call happened but its cost is explicitly unknown; never silently zeroed.
        ledger.settled_calls = (ledger.settled_calls or 0) + 1
        await self._session.flush()

    async def release(self, *, key: str) -> None:
        row = await self._reservation(key)
        if row.status != "reserved":
            raise ValueError("only reserved entries can be released")
        row.status = "released"
        ledger = await self._ledger()
        ledger.reserved_calls = max(0, (ledger.reserved_calls or 0) - row.calls)
        ledger.reserved_cost_usd = max(
            Decimal(0), (ledger.reserved_cost_usd or Decimal(0)) - row.cost_usd
        )
        await self._session.flush()


# ---------------------------------------------------------------------------
# Lineage helpers (byte-replayable from the frozen job row)
# ---------------------------------------------------------------------------


def illustration_lineage_from_job(job: IllustrationJob) -> IllustrationLineage:
    return IllustrationLineage(
        scene_spec_hash=job.scene_spec_hash,
        prompt_revision_id=job.prompt_revision_id,
        prompt_revision_hash=job.prompt_revision_hash,
        visual_bible_revision_id=job.visual_bible_revision_id,
        visual_bible_revision_hash=job.visual_bible_revision_hash,
        source_snapshot_id=job.source_snapshot_id,
        source_snapshot_hash=job.source_snapshot_hash,
        cutoff_chapter=job.cutoff_chapter,
        model_lineage=dict(job.model_lineage or {}),
        config_hash=job.config_hash,
    )


def illustration_job_contract_from_row(job: IllustrationJob) -> IllustrationJobContract:
    return IllustrationJobContract(
        schema_version="illustration.v1",
        artifact_kind="illustration_job",
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        job_key=job.job_key,
        lineage=illustration_lineage_from_job(job),
        price_snapshot=dict(job.price_snapshot or {}),
        idempotency_key=job.idempotency_key,
    )


# ---------------------------------------------------------------------------
# Runtime / dispatch
# ---------------------------------------------------------------------------


def production_runtime() -> IllustrationWorkerRuntime:
    import os

    root = AssetStorage.default_storage_root()
    transport_mode = "success"
    if os.environ.get("NOVELMIND_ILLUSTRATION_CONTROLLED_TRANSPORT") == "1":
        transport_mode = os.environ.get("NOVELMIND_ILLUSTRATION_MOCK_MODE", "success")
    from app.services.illustrations.gateway import MockIllustrationTransport

    return IllustrationWorkerRuntime(
        sessions=async_session_factory,
        gateway=IllustrationGateway(MockIllustrationTransport(mode=transport_mode)),
        storage=AssetStorage(root),
    )


async def dispatch_illustration_job(job_id: int) -> None:
    """BackgroundTasks entrypoint; durable lease makes repeated dispatch safe."""
    try:
        await run_illustration_worker(job_id, runtime=production_runtime())
    except Exception:  # noqa: BLE001 - background runner must not crash the request
        _SAFE_LOG.exception("illustration background dispatch failed job_id=%s", job_id)


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


async def run_illustration_worker(
    job_id: int, *, runtime: IllustrationWorkerRuntime
) -> None:
    lease_id = uuid.uuid4().hex
    if not await _claim_job(runtime.sessions, job_id, lease_id):
        return
    try:
        context = await _load_job_context(runtime.sessions, job_id)
        if await _has_succeeded_asset(runtime.sessions, job_id):
            await _finish_job(
                runtime.sessions,
                job_id,
                "succeeded",
                None,
                "idempotent_completion",
            )
            return
        await _execute_attempts(runtime, job_id, context, lease_id)
    except GenerationGateError as exc:
        await _finish_job(
            runtime.sessions, job_id, "failed", exc.reason_code, str(exc)
        )
    except (UnknownPricing, BudgetExceeded) as exc:
        await _finish_job(
            runtime.sessions,
            job_id,
            "paused_budget",
            type(exc).__name__,
            str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - durable failure isolation
        await _finish_job(
            runtime.sessions, job_id, "failed", type(exc).__name__, type(exc).__name__
        )
        raise


async def _claim_job(
    sessions: async_sessionmaker[AsyncSession], job_id: int, lease_id: str
) -> bool:
    async with sessions.begin() as session:
        job = await session.get(IllustrationJob, job_id, with_for_update=True)
        if job is None:
            return False
        claimable = set(ILLUSTRATION_JOB_NONTERMINAL_STATUSES)
        if job.status not in claimable:
            return False
        now = datetime.now(UTC)
        if (
            job.status == "running"
            and job.lease_id
            and job.lease_id != lease_id
            and job.lease_expires_at
            and job.lease_expires_at > now
        ):
            return False
        if job.cancel_requested:
            job.status = "cancelled"
            job.status_reason = "user_cancel"
            job.error_code = "user_cancel"
            return False
        job.lease_id = lease_id
        job.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
        job.heartbeat_at = now
        job.status = "running"
        return True


async def _load_job_context(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> dict[str, Any]:
    """Frozen context: job row + revalidated approved/fresh prompt.

    The server-side generation gate re-runs here so a prompt that became stale
    or unapproved after job creation fails closed before any provider call.
    """
    async with sessions() as session:
        job = await session.get(IllustrationJob, job_id)
        if job is None:
            raise IllustrationWorkerError("illustration job missing")
        if job.prompt_revision_id is None:
            raise GenerationGateError(
                "prompt_revision_not_found", "job has no prompt revision lineage"
            )
        prompt_row = await check_generation_prompt_gate(
            session,
            owner_id=job.owner_id,
            novel_id=job.novel_id,
            prompt_revision_id=job.prompt_revision_id,
        )
        return {
            "job": job,
            "prompt_text": prompt_row.prompt_text,
            "lineage": illustration_lineage_from_job(job),
            "price_snapshot": PriceSnapshot.model_validate(dict(job.price_snapshot or {})),
        }


async def _has_succeeded_asset(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> bool:
    async with sessions() as session:
        asset = await session.scalar(
            select(AssetRevision)
            .where(AssetRevision.job_id == job_id)
            .limit(1)
        )
    return asset is not None


async def _count_attempts(
    sessions: async_sessionmaker[AsyncSession], job_id: int
) -> int:
    async with sessions() as session:
        value = await session.scalar(
            select(func.count(IllustrationAttempt.id)).where(
                IllustrationAttempt.job_id == job_id
            )
        )
    return int(value or 0)


async def _execute_attempts(
    runtime: IllustrationWorkerRuntime,
    job_id: int,
    context: dict[str, Any],
    lease_id: str,
) -> None:
    job: IllustrationJob = context["job"]
    job_contract = illustration_job_contract_from_row(job)
    validate_illustration_job_contract(job_contract)

    async with runtime.sessions() as session:
        budget_repo = DurableIllustrationBudgetRepository(
            session,
            owner_id=job.owner_id,
            novel_id=job.novel_id,
            policy=runtime.budget_policy,
        )
        existing = await _count_attempts_in_session(session, job_id)
        if existing >= runtime.max_attempts:
            # All bounded attempts already recorded by a prior crashed run.
            last = await _last_attempt_in_session(session, job_id)
            if last is not None and last.status == "outcome_unknown":
                await session.commit()
                await _finish_job(
                    runtime.sessions,
                    job_id,
                    "outcome_unknown",
                    "provider_outcome_unknown",
                    "retries exhausted; outcome must be reconciled by request id/hash",
                )
            else:
                await session.commit()
                await _finish_job(
                    runtime.sessions,
                    job_id,
                    "failed",
                    last.error_code if last else "provider_rejected",
                    "provider returned an unusable asset on all attempts",
                )
            return

        outcome_unknown = False
        last_error_code: str | None = None
        for attempt_number in range(existing + 1, runtime.max_attempts + 1):
            reservation_key = f"job:{job_id}:attempt:{attempt_number}"
            try:
                result = await runtime.gateway.generate(
                    job_id=job_id,
                    attempt_number=attempt_number,
                    reservation_key=reservation_key,
                    prompt_text=context["prompt_text"],
                    lineage=context["lineage"],
                    price_snapshot=context["price_snapshot"],
                    budget=budget_repo,
                    max_input_tokens=runtime.max_input_tokens,
                    max_output_tokens=runtime.max_output_tokens,
                    width=runtime.width,
                    height=runtime.height,
                    timeout=runtime.timeout,
                )
            except (UnknownPricing, BudgetExceeded):
                raise
            except (ProviderOutcomeUnknown, ProviderRejected) as exc:
                await _record_attempt(session, job_id, exc.attempt)
                last_error_code = exc.attempt.error_code or "provider_rejected"
                outcome_unknown = isinstance(exc, ProviderOutcomeUnknown)
                await _heartbeat_in_session(session, job_id, lease_id)
                if attempt_number >= runtime.max_attempts:
                    break
                continue
            else:
                await _record_attempt(session, job_id, result.attempt)
                await _persist_asset(
                    session, runtime, job, job_contract, context, result
                )
                await session.commit()
                await _finish_job(
                    runtime.sessions,
                    job_id,
                    "succeeded",
                    None,
                    "generated",
                    response_hash=result.response_hash,
                )
                return

        # Bounded attempts exhausted with no success.
        await session.commit()
        if outcome_unknown:
            await _finish_job(
                runtime.sessions,
                job_id,
                "outcome_unknown",
                "provider_outcome_unknown",
                "retries exhausted; outcome must be reconciled by request id/hash",
            )
        else:
            await _finish_job(
                runtime.sessions,
                job_id,
                "failed",
                last_error_code or "provider_rejected",
                "provider returned an unusable asset on all attempts",
            )


async def _count_attempts_in_session(session: AsyncSession, job_id: int) -> int:
    value = await session.scalar(
        select(func.count(IllustrationAttempt.id)).where(
            IllustrationAttempt.job_id == job_id
        )
    )
    return int(value or 0)


async def _last_attempt_in_session(
    session: AsyncSession, job_id: int
) -> IllustrationAttempt | None:
    return await session.scalar(
        select(IllustrationAttempt)
        .where(IllustrationAttempt.job_id == job_id)
        .order_by(IllustrationAttempt.attempt_number.desc())
        .limit(1)
    )


async def _record_attempt(
    session: AsyncSession, job_id: int, attempt: GatewayAttempt
) -> None:
    session.add(
        IllustrationAttempt(
            job_id=job_id,
            reservation_id=attempt.reservation_id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            provider_request_id=attempt.provider_request_id,
            request_hash=attempt.request_hash,
            response_hash=attempt.response_hash,
            usage=dict(attempt.usage),
            cost_usd=attempt.cost_usd,
            latency_ms=attempt.latency_ms,
            error_code=attempt.error_code,
        )
    )
    await session.flush()


async def _persist_asset(
    session: AsyncSession,
    runtime: IllustrationWorkerRuntime,
    job: IllustrationJob,
    job_contract: IllustrationJobContract,
    context: dict[str, Any],
    result: Any,
) -> AssetRevision:
    response = result.response
    storage_key = runtime.storage.store(
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        payload=response.payload,
        mime_type=response.mime_type,
        bytes_hash=result.response_hash,
    )
    revision_number = 1
    revision_key = f"{job.job_key}:rev{revision_number}"
    asset_id = (
        f"asset-{job.owner_id}-{job.novel_id}-{revision_number}-"
        f"{result.response_hash[:12]}"
    )
    contract = AssetRevisionContract(
        schema_version="illustration-asset.v1",
        artifact_kind="illustration_asset",
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        job_id=job.id,
        revision_key=revision_key,
        revision_number=revision_number,
        asset_id=asset_id,
        storage_key=storage_key,
        mime_type=response.mime_type,
        width=response.width,
        height=response.height,
        size_bytes=len(response.payload),
        bytes_hash=result.response_hash,
        lineage=context["lineage"],
        provider=response.provider,
        provider_model=response.provider_model,
        provider_request_id=response.provider_request_id,
        provider_response=dict(response.response_metadata),
        provenance={
            "source": response.provider,
            "fixture": "illustration-mock-success",
            "attempt_number": result.attempt.attempt_number,
        },
        rights_status="unreviewed",
        approval_state="candidate",
        idempotency_key=job.idempotency_key,
    )
    # Server-side gates: the asset must replay from its frozen lineage and the
    # bytes must replay hash/size — a provider failure is never an empty success.
    validate_asset_revision_contract(contract, job_contract)
    validate_asset_bytes(contract, response.payload)
    canonical = contract.model_dump(mode="json")
    row = AssetRevision(
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        job_id=job.id,
        revision_key=revision_key,
        revision_number=revision_number,
        asset_id=asset_id,
        storage_key=storage_key,
        mime_type=response.mime_type,
        width=response.width,
        height=response.height,
        size_bytes=len(response.payload),
        bytes_hash=result.response_hash,
        scene_spec_hash=job.scene_spec_hash,
        prompt_revision_id=job.prompt_revision_id,
        prompt_revision_hash=job.prompt_revision_hash,
        visual_bible_revision_hash=job.visual_bible_revision_hash,
        source_snapshot_id=job.source_snapshot_id,
        source_snapshot_hash=job.source_snapshot_hash,
        cutoff_chapter=job.cutoff_chapter,
        model_lineage=dict(job.model_lineage or {}),
        config_hash=job.config_hash,
        provider=response.provider,
        provider_model=response.provider_model,
        provider_request_id=response.provider_request_id,
        provider_response=dict(response.response_metadata),
        provenance=dict(
            {
                "source": response.provider,
                "fixture": "illustration-mock-success",
                "attempt_number": result.attempt.attempt_number,
            }
        ),
        rights_status="unreviewed",
        approval_state="candidate",
        approved_by=None,
        canonical_payload=canonical,
        canonical_payload_hash=canonical_illustration_hash(canonical),
        idempotency_key=job.idempotency_key,
        projection_hash=result.response_hash,
        schema_version="illustration-asset.v1",
    )
    session.add(row)
    await session.flush()
    return row


async def _heartbeat_in_session(
    session: AsyncSession, job_id: int, lease_id: str
) -> None:
    """Extend the lease/heartbeat within the running execution transaction.

    D-33-01 durable worker: a long multi-attempt run keeps its lease fresh so a
    concurrent worker cannot claim the same job mid-run.
    """
    job = await session.get(IllustrationJob, job_id, with_for_update=True)
    if job is not None and job.lease_id == lease_id:
        now = datetime.now(UTC)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
    await session.flush()


async def _finish_job(
    sessions: async_sessionmaker[AsyncSession],
    job_id: int,
    status: str,
    error_code: str | None,
    status_reason: str,
    *,
    response_hash: str | None = None,
) -> None:
    async with sessions.begin() as session:
        job = await session.get(IllustrationJob, job_id, with_for_update=True)
        if job is None:
            return
        if job.status == "succeeded":
            return
        job.status = status
        job.error_code = error_code
        job.status_reason = status_reason[:160]
        if response_hash is not None:
            job.response_hash = response_hash


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_ILLUSTRATION_POLICY",
    "DurableIllustrationBudgetRepository",
    "IllustrationWorkerError",
    "IllustrationWorkerRuntime",
    "MOCK_ILLUSTRATION_MODEL",
    "MOCK_ILLUSTRATION_PROVIDER",
    "default_illustration_price_snapshot",
    "dispatch_illustration_job",
    "illustration_job_contract_from_row",
    "illustration_lineage_from_job",
    "production_runtime",
    "run_illustration_worker",
]
