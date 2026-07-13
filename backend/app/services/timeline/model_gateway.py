"""Strict, timeline-only structured model gateway with explicit repair semantics."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.analysis import (
    AnalysisBudgetLedger,
    AnalysisBudgetReservation,
    AnalysisRun,
    ModelCallAttempt,
)
from app.services.timeline.budget import BudgetExceeded, BudgetGate, UnknownPricing

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class DependencyPaused(RuntimeError):
    """Resolved deployment cannot safely execute this structured stage."""


class ModelCallFailed(RuntimeError):
    def __init__(self, message: str, attempts: list["GatewayAttempt"]) -> None:
        super().__init__(message)
        self.attempts = attempts


class StructuredOutputRejected(ModelCallFailed):
    """Both the original response and the sole repair failed local gates."""


class ModelTransport(Protocol):
    async def complete(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ModelDeployment:
    provider: str
    model_id: str
    revision: str
    supports_structured_output: bool
    input_price_per_million: Decimal
    output_price_per_million: Decimal

    @property
    def resolved_name(self) -> str:
        return f"{self.provider}/{self.model_id}"

    @property
    def lineage(self) -> tuple[str, str, str]:
        return self.provider, self.model_id, self.revision


@dataclass(frozen=True)
class GatewayAttempt:
    attempt_number: int
    status: str
    reservation_key: str
    request_hash: str
    response_hash: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal | None = None
    error_code: str | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class GatewayResult(Generic[SchemaT]):
    output: SchemaT
    attempts: list[GatewayAttempt]
    deployment: ModelDeployment


@dataclass(frozen=True)
class PersistentAttempt:
    attempt_id: int
    reservation_id: int
    attempt_number: int


class PostgresCallRepository:
    """Atomic PostgreSQL authority for timeline budgets and model-call audits."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def reserve_and_start(
        self, *, run_id: int, stage_key: str, reservation_key: str,
        request_hash: str, cache_key: str | None, input_tokens: int,
        output_tokens: int, input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> PersistentAttempt:
        rejection: BudgetExceeded | None = None
        async with self.sessions.begin() as session:
            run = await session.get(AnalysisRun, run_id, with_for_update=True)
            if run is None:
                raise BudgetExceeded("timeline run does not exist")
            ledger = await session.scalar(select(AnalysisBudgetLedger).where(
                AnalysisBudgetLedger.run_id == run_id,
            ).with_for_update())
            if ledger is None:
                raise BudgetExceeded("timeline run has no persistent budget ledger")
            attempt_number = int(await session.scalar(select(
                func.coalesce(func.max(ModelCallAttempt.attempt_number), 0)
            ).where(
                ModelCallAttempt.run_id == run_id,
                ModelCallAttempt.stage_key == stage_key,
            ))) + 1
            # Attempt numbering is durable; a resumed process must not reuse its local attempt:1 key.
            reservation_key = f"{stage_key}:attempt:{attempt_number}"
            if run.status == "paused_budget":
                await self._reject_budget(
                    session, run_id=run_id, stage_key=stage_key,
                    attempt_number=attempt_number, request_hash=request_hash,
                    cache_key=cache_key, error_code="budget_paused",
                )
                rejection = BudgetExceeded("budget is paused; no further calls are allowed")
                worst_cost = Decimal(0)
            elif input_price_per_million is None or output_price_per_million is None:
                await self._reject_budget(
                    session, run_id=run_id, stage_key=stage_key,
                    attempt_number=attempt_number, request_hash=request_hash,
                    cache_key=cache_key, error_code="unknown_pricing",
                )
                rejection = UnknownPricing("provider pricing is unknown; cost cannot be reserved")
                worst_cost = Decimal(0)
            else:
                worst_cost = (
                Decimal(input_tokens) * input_price_per_million
                + Decimal(output_tokens) * output_price_per_million
                ) / Decimal(1_000_000)
            exceeds = (
                ledger.reserved_calls + ledger.settled_calls + 1 > ledger.max_calls
                or ledger.reserved_input_tokens + ledger.settled_input_tokens + input_tokens
                > ledger.max_input_tokens
                or ledger.reserved_output_tokens + ledger.settled_output_tokens + output_tokens
                > ledger.max_output_tokens
                or Decimal(ledger.reserved_cost_usd) + Decimal(ledger.settled_cost_usd) + worst_cost
                > Decimal(ledger.max_cost_usd)
            )
            if rejection is None and exceeds:
                await self._reject_budget(
                    session, run_id=run_id, stage_key=stage_key,
                    attempt_number=attempt_number, request_hash=request_hash,
                    cache_key=cache_key, error_code="budget_exceeded",
                )
                rejection = BudgetExceeded("worst-case reservation exceeds frozen policy")
            if rejection is None:
                reservation = AnalysisBudgetReservation(
                    ledger_id=ledger.id,
                    reservation_key=reservation_key,
                    status="reserved",
                    calls=1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=worst_cost,
                    settled_usage={},
                )
                session.add(reservation)
                await session.flush()
                ledger.reserved_calls += 1
                ledger.reserved_input_tokens += input_tokens
                ledger.reserved_output_tokens += output_tokens
                ledger.reserved_cost_usd = Decimal(ledger.reserved_cost_usd) + worst_cost
                attempt = ModelCallAttempt(
                    run_id=run_id,
                    reservation_id=reservation.id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    status="reserved",
                    cache_key=cache_key,
                    request_hash=request_hash,
                    usage={},
                )
                session.add(attempt)
                await session.flush()
                result = PersistentAttempt(attempt.id, reservation.id, attempt_number)
        if rejection is not None:
            raise rejection
        return result

    @staticmethod
    async def _reject_budget(
        session: AsyncSession, *, run_id: int, stage_key: str,
        attempt_number: int, request_hash: str, cache_key: str | None,
        error_code: str,
    ) -> None:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        if run is not None:
            run.status = "paused_budget"
            run.status_reason = error_code
        session.add(ModelCallAttempt(
            run_id=run_id,
            stage_key=stage_key,
            attempt_number=attempt_number,
            status="budget_rejected",
            cache_key=cache_key,
            request_hash=request_hash,
            usage={},
            error_code=error_code,
        ))

    async def complete_attempt(
        self, handle: PersistentAttempt, *, status: str,
        response_hash: str | None, provider_request_id: str | None,
        usage: dict[str, int], cost_usd: Decimal | None,
        latency_ms: int, error_code: str | None,
    ) -> None:
        async with self.sessions.begin() as session:
            reservation = await session.get(
                AnalysisBudgetReservation, handle.reservation_id, with_for_update=True,
            )
            attempt = await session.get(ModelCallAttempt, handle.attempt_id, with_for_update=True)
            if reservation is None or attempt is None:
                raise RuntimeError("persistent timeline call state disappeared")
            ledger = await session.get(AnalysisBudgetLedger, reservation.ledger_id, with_for_update=True)
            if reservation.status == "reserved":
                actual_input = int(usage.get("input_tokens", 0))
                actual_output = int(usage.get("output_tokens", 0))
                actual_cost = Decimal(cost_usd or 0)
                if actual_input > reservation.input_tokens or actual_output > reservation.output_tokens:
                    run = await session.get(AnalysisRun, attempt.run_id, with_for_update=True)
                    if run is not None:
                        run.status = "paused_budget"
                        run.status_reason = "provider_usage_exceeded_reservation"
                    status = "budget_exceeded"
                    error_code = "provider_usage_exceeded_reservation"
                    actual_input = reservation.input_tokens
                    actual_output = reservation.output_tokens
                    actual_cost = Decimal(reservation.cost_usd)
                ledger.reserved_calls -= reservation.calls
                ledger.reserved_input_tokens -= reservation.input_tokens
                ledger.reserved_output_tokens -= reservation.output_tokens
                ledger.reserved_cost_usd = Decimal(ledger.reserved_cost_usd) - Decimal(reservation.cost_usd)
                ledger.settled_calls += reservation.calls
                ledger.settled_input_tokens += actual_input
                ledger.settled_output_tokens += actual_output
                ledger.settled_cost_usd = Decimal(ledger.settled_cost_usd) + actual_cost
                reservation.status = "settled"
                reservation.settled_usage = {
                    "input_tokens": actual_input,
                    "output_tokens": actual_output,
                    "cost_usd": str(actual_cost),
                }
            attempt.status = status
            attempt.response_hash = response_hash
            attempt.provider_request_id = provider_request_id
            attempt.usage = usage
            attempt.cost_usd = cost_usd
            attempt.latency_ms = latency_ms
            attempt.error_code = error_code

    async def mark_outcome_unknown(
        self, handle: PersistentAttempt, *, latency_ms: int, error_code: str,
    ) -> None:
        async with self.sessions.begin() as session:
            attempt = await session.get(ModelCallAttempt, handle.attempt_id, with_for_update=True)
            if attempt is not None:
                attempt.status = "outcome_unknown"
                attempt.latency_ms = latency_ms
                attempt.error_code = error_code
            run = await session.get(AnalysisRun, attempt.run_id, with_for_update=True) if attempt else None
            if run is not None:
                run.status = "paused_dependency"
                run.status_reason = "provider_outcome_unknown"

    async def record_cache_hit(
        self, *, run_id: int, stage_key: str, cache_key: str,
        source_attempt_id: int, artifact_checksum: str,
    ) -> ModelCallAttempt:
        async with self.sessions.begin() as session:
            attempt_number = int(await session.scalar(select(
                func.coalesce(func.max(ModelCallAttempt.attempt_number), 0)
            ).where(
                ModelCallAttempt.run_id == run_id,
                ModelCallAttempt.stage_key == stage_key,
            ))) + 1
            attempt = ModelCallAttempt(
                run_id=run_id,
                stage_key=stage_key,
                attempt_number=attempt_number,
                status="call-skipped",
                cache_key=cache_key,
                cache_source_attempt_id=source_attempt_id,
                request_hash=hashlib.sha256(cache_key.encode()).hexdigest(),
                response_hash=artifact_checksum,
                usage={},
                error_code=None,
            )
            session.add(attempt)
            await session.flush()
            return attempt


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _response_content(response: Any) -> str:
    if isinstance(response, dict) and isinstance(response.get("content"), str):
        return response["content"]
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    raise ValueError("provider response has no textual structured content")


def _response_usage(response: Any) -> dict[str, int]:
    raw = response.get("usage", {}) if isinstance(response, dict) else getattr(response, "usage", {})
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return {
        "input_tokens": int(raw.get("input_tokens", raw.get("prompt_tokens", 0))),
        "output_tokens": int(raw.get("output_tokens", raw.get("completion_tokens", 0))),
    }


def _response_request_id(response: Any) -> str | None:
    if isinstance(response, dict):
        value = response.get("id")
    else:
        value = getattr(response, "id", None)
    return str(value) if value is not None else None


class TimelineModelGateway:
    """Owns timeline structured calls; callers own persistence and publication."""

    def __init__(self, transport: ModelTransport, *,
                 persistence: PostgresCallRepository | None = None) -> None:
        self.transport = transport
        self.persistence = persistence

    async def generate(
        self, *, deployment: ModelDeployment, schema: type[SchemaT],
        messages: list[dict[str, str]], budget: BudgetGate, run_id: int,
        stage_key: str, max_input_tokens: int, max_output_tokens: int,
        timeout: float = 60, business_validator: Callable[[SchemaT], None] | None = None,
        cache_key: str | None = None,
    ) -> GatewayResult[SchemaT]:
        if not deployment.supports_structured_output:
            raise DependencyPaused(
                f"{deployment.resolved_name}@{deployment.revision} lacks structured-output capability"
            )

        attempts: list[GatewayAttempt] = []
        current_messages = list(messages)
        for attempt_number in (1, 2):
            reservation_key = f"{stage_key}:attempt:{attempt_number}"
            request_hash = _canonical_hash({
                "deployment": deployment.lineage, "messages": current_messages,
                "schema": schema.model_json_schema(), "timeout": timeout,
            })
            persistent_attempt = None
            if self.persistence is not None:
                persistent_attempt = await self.persistence.reserve_and_start(
                    run_id=run_id, stage_key=stage_key, reservation_key=reservation_key,
                    request_hash=request_hash, cache_key=cache_key,
                    input_tokens=max_input_tokens, output_tokens=max_output_tokens,
                    input_price_per_million=deployment.input_price_per_million,
                    output_price_per_million=deployment.output_price_per_million,
                )
                attempt_number = persistent_attempt.attempt_number
            else:
                budget.reserve(
                    reservation_key, input_tokens=max_input_tokens, output_tokens=max_output_tokens,
                    input_price_per_million=deployment.input_price_per_million,
                    output_price_per_million=deployment.output_price_per_million,
                )
            started = time.perf_counter()
            try:
                response = await self.transport.complete(
                    model=deployment.resolved_name, messages=current_messages,
                    response_format=schema, timeout=timeout, num_retries=0, stream=False,
                )
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                if persistent_attempt is not None:
                    await self.persistence.mark_outcome_unknown(
                        persistent_attempt, latency_ms=latency_ms,
                        error_code=type(exc).__name__,
                    )
                attempts.append(GatewayAttempt(
                    attempt_number, "outcome_unknown", reservation_key, request_hash,
                    error_code=type(exc).__name__, latency_ms=latency_ms,
                ))
                raise ModelCallFailed("provider call outcome is unknown", attempts) from exc

            usage = _response_usage(response)
            actual_cost = (
                Decimal(usage["input_tokens"]) * deployment.input_price_per_million
                + Decimal(usage["output_tokens"]) * deployment.output_price_per_million
            ) / Decimal(1_000_000)
            try:
                content = _response_content(response)
                output = schema.model_validate_json(content, strict=True)
                if business_validator is not None:
                    business_validator(output)
            except (ValidationError, ValueError) as exc:
                response_hash = _canonical_hash(response)
                latency_ms = int((time.perf_counter() - started) * 1000)
                if persistent_attempt is not None:
                    await self.persistence.complete_attempt(
                        persistent_attempt, status="schema_rejected",
                        response_hash=response_hash,
                        provider_request_id=_response_request_id(response),
                        usage=usage, cost_usd=actual_cost, latency_ms=latency_ms,
                        error_code=type(exc).__name__,
                    )
                else:
                    budget.settle(
                        reservation_key, actual_input_tokens=usage["input_tokens"],
                        actual_output_tokens=usage["output_tokens"], actual_cost_usd=actual_cost,
                    )
                attempts.append(GatewayAttempt(
                    attempt_number, "schema_rejected", reservation_key, request_hash,
                    response_hash, usage, actual_cost, type(exc).__name__,
                    latency_ms,
                ))
                if attempt_number == 2:
                    raise StructuredOutputRejected("structured output failed local validation", attempts) from exc
                current_messages = current_messages + [{
                    "role": "user",
                    "content": "Local validation error. Return one corrected JSON object matching the supplied schema; do not add fields.",
                }]
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            response_hash = _canonical_hash(response)
            if persistent_attempt is not None:
                await self.persistence.complete_attempt(
                    persistent_attempt, status="succeeded", response_hash=response_hash,
                    provider_request_id=_response_request_id(response), usage=usage,
                    cost_usd=actual_cost, latency_ms=latency_ms, error_code=None,
                )
            else:
                budget.settle(
                    reservation_key, actual_input_tokens=usage["input_tokens"],
                    actual_output_tokens=usage["output_tokens"], actual_cost_usd=actual_cost,
                )
            attempts.append(GatewayAttempt(
                attempt_number, "succeeded", reservation_key, request_hash,
                response_hash, usage, actual_cost, latency_ms=latency_ms,
            ))
            return GatewayResult(output, attempts, deployment)

        raise AssertionError("unreachable")
