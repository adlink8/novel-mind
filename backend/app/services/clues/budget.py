"""Clue-owned budget policy and worst-case reservation helpers.

Reuses the pure BudgetGate core from Phase 08; persistence uses clue_* tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.clue import (
    ClueAnalysisRun,
    ClueBudgetLedger,
    ClueBudgetReservation,
    ClueModelCallAttempt,
)
from app.services.timeline.budget import (
    BudgetExceeded,
    BudgetGate,
    BudgetPolicy,
    Reservation,
    UnknownPricing,
)

__all__ = [
    "BudgetExceeded",
    "BudgetGate",
    "BudgetPolicy",
    "Reservation",
    "UnknownPricing",
    "ClueCallRepository",
    "PersistentAttempt",
]


@dataclass(frozen=True)
class PersistentAttempt:
    attempt_id: int
    reservation_id: int
    attempt_number: int


class ClueCallRepository:
    """Atomic PostgreSQL authority for clue budgets and model-call audits."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def reserve_and_start(
        self,
        *,
        run_id: int,
        stage_key: str,
        reservation_key: str,
        request_hash: str,
        cache_key: str | None,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> PersistentAttempt:
        rejection: BudgetExceeded | None = None
        async with self.sessions.begin() as session:
            run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
            if run is None:
                raise BudgetExceeded("clue run does not exist")
            ledger = await session.scalar(
                select(ClueBudgetLedger)
                .where(ClueBudgetLedger.run_id == run_id)
                .with_for_update()
            )
            if ledger is None:
                raise BudgetExceeded("clue run has no persistent budget ledger")
            attempt_number = (
                int(
                    await session.scalar(
                        select(
                            func.coalesce(
                                func.max(ClueModelCallAttempt.attempt_number), 0
                            )
                        ).where(
                            ClueModelCallAttempt.run_id == run_id,
                            ClueModelCallAttempt.stage_key == stage_key,
                        )
                    )
                )
                + 1
            )
            reservation_key = f"{stage_key}:attempt:{attempt_number}"
            if run.status == "paused_budget":
                await self._reject_budget(
                    session,
                    run_id=run_id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code="budget_paused",
                )
                rejection = BudgetExceeded(
                    "budget is paused; no further calls are allowed"
                )
                worst_cost = Decimal(0)
            elif input_price_per_million is None or output_price_per_million is None:
                await self._reject_budget(
                    session,
                    run_id=run_id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code="unknown_pricing",
                )
                rejection = UnknownPricing(
                    "provider pricing is unknown; cost cannot be reserved"
                )
                worst_cost = Decimal(0)
            else:
                worst_cost = (
                    Decimal(input_tokens) * input_price_per_million
                    + Decimal(output_tokens) * output_price_per_million
                ) / Decimal(1_000_000)
            exceeds = (
                ledger.reserved_calls + ledger.settled_calls + 1 > ledger.max_calls
                or ledger.reserved_input_tokens
                + ledger.settled_input_tokens
                + input_tokens
                > ledger.max_input_tokens
                or ledger.reserved_output_tokens
                + ledger.settled_output_tokens
                + output_tokens
                > ledger.max_output_tokens
                or Decimal(ledger.reserved_cost_usd)
                + Decimal(ledger.settled_cost_usd)
                + worst_cost
                > Decimal(ledger.max_cost_usd)
            )
            if rejection is None and exceeds:
                await self._reject_budget(
                    session,
                    run_id=run_id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code="budget_exceeded",
                )
                rejection = BudgetExceeded(
                    "worst-case reservation exceeds frozen policy"
                )
            if rejection is None:
                reservation = ClueBudgetReservation(
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
                ledger.reserved_cost_usd = (
                    Decimal(ledger.reserved_cost_usd) + worst_cost
                )
                attempt = ClueModelCallAttempt(
                    run_id=run_id,
                    reservation_id=reservation.id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    status="started",
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
        session: AsyncSession,
        *,
        run_id: int,
        stage_key: str,
        attempt_number: int,
        request_hash: str,
        cache_key: str | None,
        error_code: str,
    ) -> None:
        run = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        if run is not None:
            run.status = "paused_budget"
            run.status_reason = error_code
        session.add(
            ClueModelCallAttempt(
                run_id=run_id,
                stage_key=stage_key,
                attempt_number=attempt_number,
                status="failed",
                cache_key=cache_key,
                request_hash=request_hash,
                usage={},
                error_code=error_code,
            )
        )

    async def complete_attempt(
        self,
        handle: PersistentAttempt,
        *,
        status: str,
        response_hash: str | None,
        provider_request_id: str | None,
        usage: dict[str, Any],
        cost_usd: Decimal | None,
        latency_ms: int | None,
        error_code: str | None,
    ) -> None:
        async with self.sessions.begin() as session:
            reservation = await session.get(
                ClueBudgetReservation, handle.reservation_id, with_for_update=True
            )
            attempt = await session.get(
                ClueModelCallAttempt, handle.attempt_id, with_for_update=True
            )
            if reservation is None or attempt is None:
                raise RuntimeError("persistent clue call state disappeared")
            ledger = await session.get(
                ClueBudgetLedger, reservation.ledger_id, with_for_update=True
            )
            if reservation.status == "reserved" and ledger is not None:
                actual_input = int(usage.get("input_tokens", 0) or 0)
                actual_output = int(usage.get("output_tokens", 0) or 0)
                actual_cost = Decimal(cost_usd or 0)
                ledger.reserved_calls = max(
                    0, ledger.reserved_calls - reservation.calls
                )
                ledger.reserved_input_tokens = max(
                    0, ledger.reserved_input_tokens - reservation.input_tokens
                )
                ledger.reserved_output_tokens = max(
                    0, ledger.reserved_output_tokens - reservation.output_tokens
                )
                ledger.reserved_cost_usd = max(
                    Decimal(0),
                    Decimal(ledger.reserved_cost_usd) - Decimal(reservation.cost_usd),
                )
                if status in {"succeeded", "cache_hit", "outcome_unknown"}:
                    ledger.settled_calls += 1
                    ledger.settled_input_tokens += actual_input
                    ledger.settled_output_tokens += actual_output
                    ledger.settled_cost_usd = (
                        Decimal(ledger.settled_cost_usd) + actual_cost
                    )
                    reservation.status = "settled"
                    reservation.settled_usage = {
                        "input_tokens": actual_input,
                        "output_tokens": actual_output,
                        "cost_usd": str(actual_cost),
                    }
                else:
                    reservation.status = "released"
            attempt.status = status
            attempt.response_hash = response_hash
            attempt.provider_request_id = provider_request_id
            attempt.usage = dict(usage or {})
            attempt.cost_usd = cost_usd
            attempt.latency_ms = latency_ms
            attempt.error_code = error_code

    async def record_cache_hit(
        self,
        *,
        run_id: int,
        stage_key: str,
        cache_key: str,
        source_attempt_id: int | None,
        artifact_checksum: str,
    ) -> None:
        async with self.sessions.begin() as session:
            attempt_number = (
                int(
                    await session.scalar(
                        select(
                            func.coalesce(
                                func.max(ClueModelCallAttempt.attempt_number), 0
                            )
                        ).where(
                            ClueModelCallAttempt.run_id == run_id,
                            ClueModelCallAttempt.stage_key == stage_key,
                        )
                    )
                )
                + 1
            )
            session.add(
                ClueModelCallAttempt(
                    run_id=run_id,
                    stage_key=stage_key,
                    attempt_number=attempt_number,
                    status="cache_hit",
                    cache_key=cache_key,
                    cache_source_attempt_id=source_attempt_id,
                    request_hash=artifact_checksum,
                    response_hash=artifact_checksum,
                    usage={"cache_hit": True},
                )
            )

    async def load_exact_cache(self, cache_key: str) -> dict[str, Any] | None:
        """Return validated gateway_output for a successful exact-cache key."""

        async with self.sessions() as session:
            row = await session.scalar(
                select(ClueModelCallAttempt)
                .where(
                    ClueModelCallAttempt.cache_key == cache_key,
                    ClueModelCallAttempt.status == "succeeded",
                    ClueModelCallAttempt.response_hash.is_not(None),
                )
                .order_by(ClueModelCallAttempt.id.desc())
                .limit(1)
            )
            if row is None:
                return None
            usage = dict(row.usage or {})
            payload = usage.get("validated_output")
            if not isinstance(payload, dict):
                return None
            return {
                "gateway_output": payload,
                "source_attempt_id": row.id,
                "artifact_checksum": row.response_hash,
            }
