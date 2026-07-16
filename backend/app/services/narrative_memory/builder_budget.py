"""Row-locked budget reservation and settlement for narrative-memory builder."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildBudgetReservation,
    NarrativeMemoryBuildRun,
)


class BudgetExceeded(RuntimeError):
    pass


class UnknownPricing(BudgetExceeded):
    pass


@dataclass(frozen=True)
class PersistentReservation:
    reservation_id: int
    reservation_key: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class BuilderBudgetService:
    """Atomic worst-case reservation before every builder provider call."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(
        self,
        *,
        run_id: int,
        reservation_key: str,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> PersistentReservation:
        run = await self._session.get(
            NarrativeMemoryBuildRun, run_id, with_for_update=True
        )
        if run is None:
            raise BudgetExceeded("builder run does not exist")
        ledger = await self._session.scalar(
            select(NarrativeMemoryBuildBudgetLedger)
            .where(NarrativeMemoryBuildBudgetLedger.run_id == run_id)
            .with_for_update()
        )
        if ledger is None:
            raise BudgetExceeded("builder run has no budget ledger")

        existing = await self._session.scalar(
            select(NarrativeMemoryBuildBudgetReservation).where(
                NarrativeMemoryBuildBudgetReservation.ledger_id == ledger.id,
                NarrativeMemoryBuildBudgetReservation.reservation_key
                == reservation_key,
            )
        )
        if existing is not None:
            return PersistentReservation(
                reservation_id=existing.id,
                reservation_key=existing.reservation_key,
                input_tokens=existing.input_tokens,
                output_tokens=existing.output_tokens,
                cost_usd=Decimal(existing.cost_usd),
            )

        if run.status == "paused_budget":
            raise BudgetExceeded("budget is paused; no further calls are allowed")
        if input_price_per_million is None or output_price_per_million is None:
            run.status = "paused_budget"
            run.status_reason = "unknown_pricing"
            await self._session.flush()
            raise UnknownPricing("provider pricing is unknown; cost cannot be reserved")

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
        if exceeds:
            run.status = "paused_budget"
            run.status_reason = "budget_exceeded"
            await self._session.flush()
            raise BudgetExceeded("worst-case reservation exceeds frozen policy")

        reservation = NarrativeMemoryBuildBudgetReservation(
            ledger_id=ledger.id,
            reservation_key=reservation_key,
            status="reserved",
            calls=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=worst_cost,
            settled_usage={},
        )
        self._session.add(reservation)
        ledger.reserved_calls += 1
        ledger.reserved_input_tokens += input_tokens
        ledger.reserved_output_tokens += output_tokens
        ledger.reserved_cost_usd = Decimal(ledger.reserved_cost_usd) + worst_cost
        await self._session.flush()
        return PersistentReservation(
            reservation_id=reservation.id,
            reservation_key=reservation.reservation_key,
            input_tokens=reservation.input_tokens,
            output_tokens=reservation.output_tokens,
            cost_usd=Decimal(reservation.cost_usd),
        )

    async def settle(
        self,
        *,
        reservation_id: int,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        reservation = await self._session.get(
            NarrativeMemoryBuildBudgetReservation,
            reservation_id,
            with_for_update=True,
        )
        if reservation is None:
            raise BudgetExceeded("reservation not found")
        if reservation.status == "settled":
            return
        if reservation.status != "reserved":
            raise BudgetExceeded(f"reservation status is {reservation.status}")
        if (
            actual_input_tokens > reservation.input_tokens
            or actual_output_tokens > reservation.output_tokens
        ):
            raise BudgetExceeded("provider usage exceeded reservation")

        ledger = await self._session.get(
            NarrativeMemoryBuildBudgetLedger,
            reservation.ledger_id,
            with_for_update=True,
        )
        if ledger is None:
            raise BudgetExceeded("ledger not found")

        reserved_cost = Decimal(reservation.cost_usd)
        ledger.reserved_calls = max(0, ledger.reserved_calls - 1)
        ledger.reserved_input_tokens = max(
            0, ledger.reserved_input_tokens - reservation.input_tokens
        )
        ledger.reserved_output_tokens = max(
            0, ledger.reserved_output_tokens - reservation.output_tokens
        )
        ledger.reserved_cost_usd = max(
            Decimal(0), Decimal(ledger.reserved_cost_usd) - reserved_cost
        )
        ledger.settled_calls += 1
        ledger.settled_input_tokens += actual_input_tokens
        ledger.settled_output_tokens += actual_output_tokens
        ledger.settled_cost_usd = Decimal(ledger.settled_cost_usd) + actual_cost_usd
        reservation.status = "settled"
        reservation.settled_usage = {
            "input_tokens": actual_input_tokens,
            "output_tokens": actual_output_tokens,
            "cost_usd": str(actual_cost_usd),
        }
        await self._session.flush()

    async def release(self, *, reservation_id: int) -> None:
        reservation = await self._session.get(
            NarrativeMemoryBuildBudgetReservation,
            reservation_id,
            with_for_update=True,
        )
        if reservation is None or reservation.status != "reserved":
            return
        ledger = await self._session.get(
            NarrativeMemoryBuildBudgetLedger,
            reservation.ledger_id,
            with_for_update=True,
        )
        if ledger is None:
            return
        ledger.reserved_calls = max(0, ledger.reserved_calls - 1)
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
        reservation.status = "released"
        await self._session.flush()
