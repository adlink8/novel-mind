"""Fixed-deployment structured gateway for narrative-memory builder calls."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildModelCallAttempt,
    NarrativeMemoryBuildRun,
)
from app.services.narrative_memory.builder_budget import (
    BudgetExceeded,
    BuilderBudgetService,
    UnknownPricing,
)
from app.services.narrative_memory.builder_contracts import (
    ModelDeploymentSnapshot,
    assert_no_forbidden_keys,
    dump_canonical,
)
from app.services.narrative_memory.builder_packages import PackageBuildError


class ModelTransport(Protocol):
    async def complete(self, **kwargs: Any) -> Any: ...


class GatewayError(RuntimeError):
    pass


class CancelledBeforePersist(GatewayError):
    pass


@dataclass
class GatewayAttemptResult:
    attempt_id: int
    attempt_number: int
    status: str
    request_hash: str
    response_hash: str | None = None
    cache_hit: bool = False
    output: dict[str, Any] | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: Decimal | None = None
    error_code: str | None = None
    latency_ms: int | None = None


class BuilderModelGateway:
    """Reserve → transport (or cache) → validate → insert attempt → settle.

    Call attempts are append-only: rows are inserted only with final status.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        transport: ModelTransport,
        deployment: ModelDeploymentSnapshot,
        max_schema_repairs: int = 1,
    ) -> None:
        self._session = session
        self._transport = transport
        self._deployment = deployment
        self._max_schema_repairs = max(0, min(1, max_schema_repairs))
        self._budget = BuilderBudgetService(session)
        self.transport_calls = 0

    async def execute_structured(
        self,
        *,
        run_id: int,
        stage_key: str,
        cache_key: str,
        request_payload: dict[str, Any],
        validate_output,
        is_cancelled,
        estimated_input_tokens: int = 800,
        estimated_output_tokens: int = 1200,
    ) -> GatewayAttemptResult:
        assert_no_forbidden_keys(request_payload)
        request_hash = sha256(
            dump_canonical(request_payload).encode("utf-8")
        ).hexdigest()

        cached = await self._find_cache_source(
            cache_key=cache_key, request_hash=request_hash
        )
        if cached is not None:
            return await self._record_cache_hit(
                run_id=run_id,
                stage_key=stage_key,
                cache_key=cache_key,
                request_hash=request_hash,
                source=cached,
                validate_output=validate_output,
            )

        if await is_cancelled():
            raise CancelledBeforePersist("cancelled before reservation")

        input_price, output_price = self._deployment.prices()
        attempt_number = await self._next_attempt_number(run_id, stage_key)
        reservation_key = f"{stage_key}:attempt:{attempt_number}"
        try:
            reservation = await self._budget.reserve(
                run_id=run_id,
                reservation_key=reservation_key,
                input_tokens=estimated_input_tokens,
                output_tokens=estimated_output_tokens,
                input_price_per_million=input_price,
                output_price_per_million=output_price,
            )
        except UnknownPricing:
            await self._insert_attempt(
                run_id=run_id,
                stage_key=stage_key,
                attempt_number=attempt_number,
                status="budget_rejected",
                request_hash=request_hash,
                cache_key=cache_key,
                error_code="unknown_pricing",
            )
            await self._pause_run(run_id, "unknown_pricing")
            raise
        except BudgetExceeded as exc:
            await self._insert_attempt(
                run_id=run_id,
                stage_key=stage_key,
                attempt_number=attempt_number,
                status="budget_rejected",
                request_hash=request_hash,
                cache_key=cache_key,
                error_code=str(exc)[:80] or "budget_exceeded",
            )
            await self._pause_run(run_id, "budget_exceeded")
            raise

        if await is_cancelled():
            await self._budget.release(reservation_id=reservation.reservation_id)
            await self._insert_attempt(
                run_id=run_id,
                reservation_id=reservation.reservation_id,
                stage_key=stage_key,
                attempt_number=attempt_number,
                status="cancelled",
                request_hash=request_hash,
                cache_key=cache_key,
                error_code="cancelled_before_transport",
            )
            raise CancelledBeforePersist("cancelled before transport")

        repairs_left = self._max_schema_repairs
        raw_output: Any = None
        validated: dict[str, Any] | None = None
        started = time.perf_counter()
        current_reservation = reservation
        current_attempt_number = attempt_number

        while True:
            try:
                self.transport_calls += 1
                raw_output = await self._transport.complete(
                    stage_key=stage_key,
                    payload=request_payload,
                    deployment=self._deployment.model_dump(mode="json"),
                    repair=repairs_left < self._max_schema_repairs,
                )
                validated = validate_output(raw_output)
                assert_no_forbidden_keys(validated)
                break
            except (PackageBuildError, ValueError, TypeError) as exc:
                if repairs_left <= 0:
                    usage = {
                        "input_tokens": min(estimated_input_tokens, 1),
                        "output_tokens": min(estimated_output_tokens, 1),
                    }
                    cost = Decimal("0")
                    await self._budget.settle(
                        reservation_id=current_reservation.reservation_id,
                        actual_input_tokens=usage["input_tokens"],
                        actual_output_tokens=usage["output_tokens"],
                        actual_cost_usd=cost,
                    )
                    await self._insert_attempt(
                        run_id=run_id,
                        reservation_id=current_reservation.reservation_id,
                        stage_key=stage_key,
                        attempt_number=current_attempt_number,
                        status="failed",
                        request_hash=request_hash,
                        cache_key=cache_key,
                        error_code="schema_or_business_invalid",
                        usage=usage,
                        cost_usd=cost,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                    )
                    raise GatewayError(str(exc)) from exc
                # Record failed attempt, settle, then reserve a repair attempt.
                await self._budget.settle(
                    reservation_id=current_reservation.reservation_id,
                    actual_input_tokens=min(estimated_input_tokens, 1),
                    actual_output_tokens=min(estimated_output_tokens, 1),
                    actual_cost_usd=Decimal("0"),
                )
                await self._insert_attempt(
                    run_id=run_id,
                    reservation_id=current_reservation.reservation_id,
                    stage_key=stage_key,
                    attempt_number=current_attempt_number,
                    status="failed",
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code="schema_repair_needed",
                )
                repairs_left -= 1
                current_attempt_number = await self._next_attempt_number(
                    run_id, stage_key
                )
                repair_key = f"{stage_key}:attempt:{current_attempt_number}:repair"
                current_reservation = await self._budget.reserve(
                    run_id=run_id,
                    reservation_key=repair_key,
                    input_tokens=estimated_input_tokens,
                    output_tokens=estimated_output_tokens,
                    input_price_per_million=input_price,
                    output_price_per_million=output_price,
                )
            except Exception as exc:
                await self._budget.settle(
                    reservation_id=current_reservation.reservation_id,
                    actual_input_tokens=min(estimated_input_tokens, 1),
                    actual_output_tokens=0,
                    actual_cost_usd=Decimal("0"),
                )
                await self._insert_attempt(
                    run_id=run_id,
                    reservation_id=current_reservation.reservation_id,
                    stage_key=stage_key,
                    attempt_number=current_attempt_number,
                    status="failed",
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code=type(exc).__name__[:80],
                )
                raise

        assert validated is not None
        usage = _usage_from_output(
            raw_output, estimated_input_tokens, estimated_output_tokens
        )
        cost = _cost(usage, input_price, output_price)
        response_hash = sha256(dump_canonical(validated).encode("utf-8")).hexdigest()
        latency_ms = int((time.perf_counter() - started) * 1000)

        if await is_cancelled():
            await self._budget.settle(
                reservation_id=current_reservation.reservation_id,
                actual_input_tokens=usage["input_tokens"],
                actual_output_tokens=usage["output_tokens"],
                actual_cost_usd=cost,
            )
            await self._insert_attempt(
                run_id=run_id,
                reservation_id=current_reservation.reservation_id,
                stage_key=stage_key,
                attempt_number=current_attempt_number,
                status="cancelled",
                request_hash=request_hash,
                response_hash=response_hash,
                cache_key=cache_key,
                error_code="cancelled_after_transport",
                usage=usage,
                cost_usd=cost,
                latency_ms=latency_ms,
                validated_output=None,
            )
            raise CancelledBeforePersist("cancelled after transport; output discarded")

        await self._budget.settle(
            reservation_id=current_reservation.reservation_id,
            actual_input_tokens=usage["input_tokens"],
            actual_output_tokens=usage["output_tokens"],
            actual_cost_usd=cost,
        )
        attempt = await self._insert_attempt(
            run_id=run_id,
            reservation_id=current_reservation.reservation_id,
            stage_key=stage_key,
            attempt_number=current_attempt_number,
            status="succeeded",
            request_hash=request_hash,
            response_hash=response_hash,
            cache_key=cache_key,
            usage=usage,
            cost_usd=cost,
            latency_ms=latency_ms,
            validated_output=validated,
        )
        return GatewayAttemptResult(
            attempt_id=attempt.id,
            attempt_number=current_attempt_number,
            status="succeeded",
            request_hash=request_hash,
            response_hash=response_hash,
            output=validated,
            usage=usage,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    async def _find_cache_source(
        self, *, cache_key: str, request_hash: str
    ) -> NarrativeMemoryBuildModelCallAttempt | None:
        return await self._session.scalar(
            select(NarrativeMemoryBuildModelCallAttempt)
            .where(
                NarrativeMemoryBuildModelCallAttempt.cache_key == cache_key,
                NarrativeMemoryBuildModelCallAttempt.request_hash == request_hash,
                NarrativeMemoryBuildModelCallAttempt.status == "succeeded",
                NarrativeMemoryBuildModelCallAttempt.validated_output.is_not(None),
            )
            .order_by(NarrativeMemoryBuildModelCallAttempt.id.asc())
            .limit(1)
        )

    async def _record_cache_hit(
        self,
        *,
        run_id: int,
        stage_key: str,
        cache_key: str,
        request_hash: str,
        source: NarrativeMemoryBuildModelCallAttempt,
        validate_output,
    ) -> GatewayAttemptResult:
        output = validate_output(source.validated_output)
        assert_no_forbidden_keys(output)
        attempt_number = await self._next_attempt_number(run_id, stage_key)
        attempt = await self._insert_attempt(
            run_id=run_id,
            stage_key=stage_key,
            attempt_number=attempt_number,
            status="cache_hit",
            request_hash=request_hash,
            response_hash=source.response_hash,
            cache_key=cache_key,
            cache_source_attempt_id=source.id,
            usage={"input_tokens": 0, "output_tokens": 0, "cache_hit": 1},
            cost_usd=Decimal("0"),
            validated_output=output,
        )
        return GatewayAttemptResult(
            attempt_id=attempt.id,
            attempt_number=attempt_number,
            status="cache_hit",
            request_hash=request_hash,
            response_hash=source.response_hash,
            cache_hit=True,
            output=output,
            usage={"input_tokens": 0, "output_tokens": 0},
            cost_usd=Decimal("0"),
        )

    async def _insert_attempt(
        self,
        *,
        run_id: int,
        stage_key: str,
        attempt_number: int,
        status: str,
        request_hash: str,
        cache_key: str | None = None,
        reservation_id: int | None = None,
        response_hash: str | None = None,
        cache_source_attempt_id: int | None = None,
        usage: dict | None = None,
        cost_usd: Decimal | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
        validated_output: dict | None = None,
    ) -> NarrativeMemoryBuildModelCallAttempt:
        attempt = NarrativeMemoryBuildModelCallAttempt(
            run_id=run_id,
            reservation_id=reservation_id,
            stage_key=stage_key,
            attempt_number=attempt_number,
            status=status,
            cache_key=cache_key,
            cache_source_attempt_id=cache_source_attempt_id,
            request_hash=request_hash,
            response_hash=response_hash,
            deployment_lineage=self._deployment.model_dump(mode="json"),
            usage=usage or {},
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            error_code=error_code,
            validated_output=validated_output,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def _pause_run(self, run_id: int, reason: str) -> None:
        run = await self._session.get(NarrativeMemoryBuildRun, run_id)
        if run is not None and run.status not in {
            "cancelled",
            "completed",
            "failed",
        }:
            run.status = "paused_budget"
            run.status_reason = reason[:160]
            await self._session.flush()

    async def _next_attempt_number(self, run_id: int, stage_key: str) -> int:
        current = await self._session.scalar(
            select(
                func.coalesce(
                    func.max(NarrativeMemoryBuildModelCallAttempt.attempt_number), 0
                )
            ).where(
                NarrativeMemoryBuildModelCallAttempt.run_id == run_id,
                NarrativeMemoryBuildModelCallAttempt.stage_key == stage_key,
            )
        )
        return int(current or 0) + 1


def _usage_from_output(
    raw_output: Any, estimated_input: int, estimated_output: int
) -> dict[str, int]:
    if isinstance(raw_output, dict) and isinstance(raw_output.get("usage"), dict):
        usage = raw_output["usage"]
        return {
            "input_tokens": int(usage.get("input_tokens", estimated_input)),
            "output_tokens": int(usage.get("output_tokens", estimated_output)),
        }
    return {"input_tokens": estimated_input, "output_tokens": estimated_output}


def _cost(
    usage: dict[str, int],
    input_price: Decimal | None,
    output_price: Decimal | None,
) -> Decimal:
    if input_price is None or output_price is None:
        return Decimal("0")
    return (
        Decimal(usage["input_tokens"]) * input_price
        + Decimal(usage["output_tokens"]) * output_price
    ) / Decimal(1_000_000)
