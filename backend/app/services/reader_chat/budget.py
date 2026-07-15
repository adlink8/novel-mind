"""Dual-scope (conversation + novel) worst-case budget reservation for reader chat.

Both ledgers must accept a reservation before any provider call. Lock order is
deterministic: novel ledger first, then conversation ledger (by stable scope).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.reader_chat import (
    ReaderBudgetLedger,
    ReaderBudgetReservation,
    ReaderGenerationJob,
    ReaderModelCallAttempt,
)


class BudgetExceeded(RuntimeError):
    """Either conversation or novel scope rejects the worst-case reservation."""


class UnknownPricing(BudgetExceeded):
    """Deployment pricing is unknown; cost cannot be reserved (fail closed)."""


@dataclass(frozen=True)
class BudgetPolicy:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: Decimal


DEFAULT_CONVERSATION_POLICY = BudgetPolicy(
    max_calls=40,
    max_input_tokens=400_000,
    max_output_tokens=80_000,
    max_cost_usd=Decimal("5.00"),
)
DEFAULT_NOVEL_POLICY = BudgetPolicy(
    max_calls=400,
    max_input_tokens=4_000_000,
    max_output_tokens=800_000,
    max_cost_usd=Decimal("50.00"),
)


@dataclass
class Reservation:
    key: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    status: str = "reserved"


class ScopeBudgetGate:
    """In-memory single-scope ledger for unit tests."""

    def __init__(self, policy: BudgetPolicy) -> None:
        self.policy = policy
        self.reservations: dict[str, Reservation] = {}
        self.paused = False
        self.settled_calls = 0

    @property
    def network_calls_allowed(self) -> bool:
        return not self.paused

    def reserve(
        self,
        key: str,
        *,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> Reservation:
        existing = self.reservations.get(key)
        if existing:
            return existing
        if self.paused:
            raise BudgetExceeded("budget is paused; no further calls are allowed")
        if input_price_per_million is None or output_price_per_million is None:
            self.paused = True
            raise UnknownPricing("provider pricing is unknown; cost cannot be reserved")
        cost = (
            Decimal(input_tokens) * input_price_per_million
            + Decimal(output_tokens) * output_price_per_million
        ) / Decimal(1_000_000)
        active = [
            r for r in self.reservations.values() if r.status in {"reserved", "settled"}
        ]
        if (
            len(active) + 1 > self.policy.max_calls
            or sum(r.input_tokens for r in active) + input_tokens
            > self.policy.max_input_tokens
            or sum(r.output_tokens for r in active) + output_tokens
            > self.policy.max_output_tokens
            or sum((r.cost_usd for r in active), Decimal(0)) + cost
            > self.policy.max_cost_usd
        ):
            self.paused = True
            raise BudgetExceeded("worst-case reservation exceeds frozen policy")
        reservation = Reservation(key, input_tokens, output_tokens, cost)
        self.reservations[key] = reservation
        return reservation

    def settle(
        self,
        key: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("reservation already transitioned")
        if (
            actual_input_tokens > reservation.input_tokens
            or actual_output_tokens > reservation.output_tokens
        ):
            self.paused = True
            raise BudgetExceeded("provider usage exceeded reservation")
        reservation.input_tokens = actual_input_tokens
        reservation.output_tokens = actual_output_tokens
        reservation.cost_usd = actual_cost_usd
        reservation.status = "settled"
        self.settled_calls += 1

    def release(self, key: str) -> None:
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("only reserved entries can be released")
        reservation.status = "released"


class DualBudgetGate:
    """In-memory dual-scope gate: both scopes reserve or neither allows a call."""

    def __init__(
        self,
        conversation: ScopeBudgetGate | None = None,
        novel: ScopeBudgetGate | None = None,
        *,
        conversation_policy: BudgetPolicy = DEFAULT_CONVERSATION_POLICY,
        novel_policy: BudgetPolicy = DEFAULT_NOVEL_POLICY,
    ) -> None:
        self.conversation = conversation or ScopeBudgetGate(conversation_policy)
        self.novel = novel or ScopeBudgetGate(novel_policy)

    @property
    def network_calls_allowed(self) -> bool:
        return (
            self.conversation.network_calls_allowed and self.novel.network_calls_allowed
        )

    def reserve(
        self,
        key: str,
        *,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> tuple[Reservation, Reservation]:
        # Novel first (broader), then conversation — deterministic order.
        novel_res = self.novel.reserve(
            key,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_price_per_million=input_price_per_million,
            output_price_per_million=output_price_per_million,
        )
        try:
            conv_res = self.conversation.reserve(
                key,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_price_per_million=input_price_per_million,
                output_price_per_million=output_price_per_million,
            )
        except BudgetExceeded:
            # Roll back the novel reservation created for this key when conversation rejects.
            novel_entry = self.novel.reservations.get(key)
            if (
                novel_entry is not None
                and novel_entry.status == "reserved"
                and key not in self.conversation.reservations
            ):
                self.novel.release(key)
            raise
        return conv_res, novel_res

    def settle(
        self,
        key: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
        actual_cost_usd: Decimal,
    ) -> None:
        self.novel.settle(
            key,
            actual_input_tokens=actual_input_tokens,
            actual_output_tokens=actual_output_tokens,
            actual_cost_usd=actual_cost_usd,
        )
        self.conversation.settle(
            key,
            actual_input_tokens=actual_input_tokens,
            actual_output_tokens=actual_output_tokens,
            actual_cost_usd=actual_cost_usd,
        )

    def release(self, key: str) -> None:
        # Always delegate so settled/missing keys surface ValueError/KeyError.
        self.novel.release(key)
        self.conversation.release(key)


@dataclass(frozen=True)
class PersistentDualAttempt:
    attempt_id: int
    conversation_reservation_id: int
    novel_reservation_id: int
    attempt_number: int
    reservation_key: str


class DualBudgetRepository:
    """PostgreSQL dual-ledger reservation and attempt audit authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        conversation_policy: BudgetPolicy = DEFAULT_CONVERSATION_POLICY,
        novel_policy: BudgetPolicy = DEFAULT_NOVEL_POLICY,
    ) -> None:
        self.sessions = sessions
        self.conversation_policy = conversation_policy
        self.novel_policy = novel_policy

    async def reserve_and_start(
        self,
        *,
        job_id: int,
        reservation_key: str,
        request_hash: str,
        cache_key: str | None,
        input_tokens: int,
        output_tokens: int,
        input_price_per_million: Decimal | None,
        output_price_per_million: Decimal | None,
    ) -> PersistentDualAttempt:
        rejection: BudgetExceeded | None = None
        result: PersistentDualAttempt | None = None
        async with self.sessions.begin() as session:
            job = await session.get(ReaderGenerationJob, job_id, with_for_update=True)
            if job is None:
                raise BudgetExceeded("generation job does not exist")

            novel_ledger = await self._get_or_create_novel_ledger(
                session,
                owner_id=job.owner_id,
                novel_id=job.novel_id,
            )
            conv_ledger = await self._get_or_create_conversation_ledger(
                session,
                owner_id=job.owner_id,
                novel_id=job.novel_id,
                conversation_id=job.conversation_id,
            )
            # Deterministic lock order: novel then conversation (already locked via FOR UPDATE).
            novel_ledger = await session.get(
                ReaderBudgetLedger, novel_ledger.id, with_for_update=True
            )
            conv_ledger = await session.get(
                ReaderBudgetLedger, conv_ledger.id, with_for_update=True
            )
            assert novel_ledger is not None and conv_ledger is not None

            attempt_number = await self._next_attempt_number(session, job_id)
            reservation_key = f"job:{job_id}:attempt:{attempt_number}"

            if job.status == "paused_budget":
                await self._reject_budget(
                    session,
                    job=job,
                    attempt_number=attempt_number,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    error_code="budget_paused",
                )
                rejection = BudgetExceeded("budget is paused; no further calls are allowed")
                worst_cost = Decimal(0)
            elif input_price_per_million is None or output_price_per_million is None:
                await self._reject_budget(
                    session,
                    job=job,
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

            if rejection is None:
                for ledger in (novel_ledger, conv_ledger):
                    if self._exceeds(ledger, input_tokens, output_tokens, worst_cost):
                        await self._reject_budget(
                            session,
                            job=job,
                            attempt_number=attempt_number,
                            request_hash=request_hash,
                            cache_key=cache_key,
                            error_code="budget_exceeded",
                        )
                        rejection = BudgetExceeded(
                            "worst-case reservation exceeds frozen policy"
                        )
                        break

            if rejection is None:
                novel_res = ReaderBudgetReservation(
                    ledger_id=novel_ledger.id,
                    reservation_key=reservation_key,
                    status="reserved",
                    calls=1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=worst_cost,
                    settled_usage={},
                )
                conv_res = ReaderBudgetReservation(
                    ledger_id=conv_ledger.id,
                    reservation_key=reservation_key,
                    status="reserved",
                    calls=1,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=worst_cost,
                    settled_usage={},
                )
                session.add_all([novel_res, conv_res])
                await session.flush()
                self._apply_reserve(novel_ledger, input_tokens, output_tokens, worst_cost)
                self._apply_reserve(conv_ledger, input_tokens, output_tokens, worst_cost)
                attempt = ReaderModelCallAttempt(
                    generation_job_id=job_id,
                    reservation_id=conv_res.id,
                    attempt_number=attempt_number,
                    status="started",
                    cache_key=cache_key,
                    request_hash=request_hash,
                    usage={},
                )
                session.add(attempt)
                await session.flush()
                # Stash novel reservation id on usage for dual settlement lookup.
                attempt.usage = {
                    "novel_reservation_id": novel_res.id,
                    "reservation_key": reservation_key,
                }
                result = PersistentDualAttempt(
                    attempt_id=attempt.id,
                    conversation_reservation_id=conv_res.id,
                    novel_reservation_id=novel_res.id,
                    attempt_number=attempt_number,
                    reservation_key=reservation_key,
                )

        if rejection is not None:
            raise rejection
        assert result is not None
        return result

    async def complete_attempt(
        self,
        handle: PersistentDualAttempt,
        *,
        status: str,
        response_hash: str | None,
        provider_request_id: str | None,
        usage: dict[str, Any],
        cost_usd: Decimal | None,
        latency_ms: int,
        error_code: str | None,
        envelope: dict[str, Any] | None = None,
    ) -> None:
        async with self.sessions.begin() as session:
            attempt = await session.get(
                ReaderModelCallAttempt, handle.attempt_id, with_for_update=True
            )
            if attempt is None:
                raise RuntimeError("persistent reader call state disappeared")
            conv_res = await session.get(
                ReaderBudgetReservation,
                handle.conversation_reservation_id,
                with_for_update=True,
            )
            novel_res = await session.get(
                ReaderBudgetReservation,
                handle.novel_reservation_id,
                with_for_update=True,
            )
            if conv_res is None or novel_res is None:
                raise RuntimeError("persistent reader reservation disappeared")

            actual_input = int(usage.get("input_tokens", 0))
            actual_output = int(usage.get("output_tokens", 0))
            actual_cost = Decimal(cost_usd or 0)

            for res in (conv_res, novel_res):
                if res.status != "reserved":
                    continue
                ledger = await session.get(
                    ReaderBudgetLedger, res.ledger_id, with_for_update=True
                )
                if ledger is None:
                    continue
                ledger.reserved_calls = max(0, ledger.reserved_calls - res.calls)
                ledger.reserved_input_tokens = max(
                    0, ledger.reserved_input_tokens - res.input_tokens
                )
                ledger.reserved_output_tokens = max(
                    0, ledger.reserved_output_tokens - res.output_tokens
                )
                ledger.reserved_cost_usd = max(
                    Decimal(0),
                    Decimal(ledger.reserved_cost_usd) - Decimal(res.cost_usd),
                )
                ledger.settled_calls += res.calls
                ledger.settled_input_tokens += actual_input
                ledger.settled_output_tokens += actual_output
                ledger.settled_cost_usd = Decimal(ledger.settled_cost_usd) + actual_cost
                res.status = "settled"
                res.settled_usage = {
                    "input_tokens": actual_input,
                    "output_tokens": actual_output,
                    "cost_usd": str(actual_cost),
                }

            payload: dict[str, Any] = {
                "input_tokens": actual_input,
                "output_tokens": actual_output,
                "novel_reservation_id": handle.novel_reservation_id,
                "reservation_key": handle.reservation_key,
            }
            if envelope is not None:
                payload["envelope"] = envelope
            attempt.status = status
            attempt.response_hash = response_hash
            attempt.provider_request_id = provider_request_id
            attempt.usage = payload
            attempt.cost_usd = cost_usd
            attempt.latency_ms = latency_ms
            attempt.error_code = error_code

    async def mark_outcome_unknown(
        self,
        handle: PersistentDualAttempt,
        *,
        latency_ms: int,
        error_code: str,
    ) -> None:
        """Keep reservation charged/held semantics: release worst-case and mark unknown.

        Spec requires outcome-unknown retention without blind retry. We release
        reserved capacity so the ledger is not permanently clogged, but the attempt
        remains outcome_unknown for audit.
        """

        async with self.sessions.begin() as session:
            attempt = await session.get(
                ReaderModelCallAttempt, handle.attempt_id, with_for_update=True
            )
            if attempt is None:
                return
            attempt.status = "outcome_unknown"
            attempt.latency_ms = latency_ms
            attempt.error_code = error_code
            job = await session.get(
                ReaderGenerationJob, attempt.generation_job_id, with_for_update=True
            )
            if job is not None and job.status not in {
                "cancelled",
                "completed",
                "failed",
                "failed_validation",
            }:
                job.status = "paused_dependency"
                job.status_reason = "provider_outcome_unknown"
                job.error_code = error_code

            for res_id in (
                handle.conversation_reservation_id,
                handle.novel_reservation_id,
            ):
                res = await session.get(
                    ReaderBudgetReservation, res_id, with_for_update=True
                )
                if res is None or res.status != "reserved":
                    continue
                ledger = await session.get(
                    ReaderBudgetLedger, res.ledger_id, with_for_update=True
                )
                if ledger is not None:
                    ledger.reserved_calls = max(0, ledger.reserved_calls - res.calls)
                    ledger.reserved_input_tokens = max(
                        0, ledger.reserved_input_tokens - res.input_tokens
                    )
                    ledger.reserved_output_tokens = max(
                        0, ledger.reserved_output_tokens - res.output_tokens
                    )
                    ledger.reserved_cost_usd = max(
                        Decimal(0),
                        Decimal(ledger.reserved_cost_usd) - Decimal(res.cost_usd),
                    )
                res.status = "released"

    async def record_cache_hit(
        self,
        *,
        job_id: int,
        cache_key: str,
        source_attempt_id: int,
        response_hash: str,
        request_hash: str,
    ) -> ReaderModelCallAttempt:
        async with self.sessions.begin() as session:
            attempt_number = await self._next_attempt_number(session, job_id)
            attempt = ReaderModelCallAttempt(
                generation_job_id=job_id,
                attempt_number=attempt_number,
                status="cache_hit",
                cache_key=cache_key,
                cache_source_attempt_id=source_attempt_id,
                request_hash=request_hash,
                response_hash=response_hash,
                usage={},
            )
            session.add(attempt)
            await session.flush()
            return attempt

    @staticmethod
    def _exceeds(
        ledger: ReaderBudgetLedger,
        input_tokens: int,
        output_tokens: int,
        worst_cost: Decimal,
    ) -> bool:
        return (
            ledger.reserved_calls + ledger.settled_calls + 1 > ledger.max_calls
            or ledger.reserved_input_tokens + ledger.settled_input_tokens + input_tokens
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

    @staticmethod
    def _apply_reserve(
        ledger: ReaderBudgetLedger,
        input_tokens: int,
        output_tokens: int,
        worst_cost: Decimal,
    ) -> None:
        ledger.reserved_calls += 1
        ledger.reserved_input_tokens += input_tokens
        ledger.reserved_output_tokens += output_tokens
        ledger.reserved_cost_usd = Decimal(ledger.reserved_cost_usd) + worst_cost

    async def _get_or_create_novel_ledger(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
    ) -> ReaderBudgetLedger:
        ledger = await session.scalar(
            select(ReaderBudgetLedger)
            .where(
                ReaderBudgetLedger.scope_type == "novel",
                ReaderBudgetLedger.owner_id == owner_id,
                ReaderBudgetLedger.novel_id == novel_id,
            )
            .with_for_update()
        )
        if ledger is not None:
            return ledger
        policy = self.novel_policy
        ledger = ReaderBudgetLedger(
            scope_type="novel",
            owner_id=owner_id,
            novel_id=novel_id,
            conversation_id=None,
            max_calls=policy.max_calls,
            max_input_tokens=policy.max_input_tokens,
            max_output_tokens=policy.max_output_tokens,
            max_cost_usd=policy.max_cost_usd,
        )
        session.add(ledger)
        await session.flush()
        return ledger

    async def _get_or_create_conversation_ledger(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        conversation_id: int,
    ) -> ReaderBudgetLedger:
        ledger = await session.scalar(
            select(ReaderBudgetLedger)
            .where(
                ReaderBudgetLedger.scope_type == "conversation",
                ReaderBudgetLedger.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if ledger is not None:
            return ledger
        policy = self.conversation_policy
        ledger = ReaderBudgetLedger(
            scope_type="conversation",
            owner_id=owner_id,
            novel_id=novel_id,
            conversation_id=conversation_id,
            max_calls=policy.max_calls,
            max_input_tokens=policy.max_input_tokens,
            max_output_tokens=policy.max_output_tokens,
            max_cost_usd=policy.max_cost_usd,
        )
        session.add(ledger)
        await session.flush()
        return ledger

    @staticmethod
    async def _next_attempt_number(session: AsyncSession, job_id: int) -> int:
        from sqlalchemy import func

        current = await session.scalar(
            select(func.coalesce(func.max(ReaderModelCallAttempt.attempt_number), 0)).where(
                ReaderModelCallAttempt.generation_job_id == job_id
            )
        )
        return int(current or 0) + 1

    @staticmethod
    async def _reject_budget(
        session: AsyncSession,
        *,
        job: ReaderGenerationJob,
        attempt_number: int,
        request_hash: str,
        cache_key: str | None,
        error_code: str,
    ) -> None:
        job.status = "paused_budget"
        job.status_reason = error_code
        job.error_code = error_code
        session.add(
            ReaderModelCallAttempt(
                generation_job_id=job.id,
                attempt_number=attempt_number,
                status="failed",
                cache_key=cache_key,
                request_hash=request_hash,
                usage={},
                error_code=error_code,
            )
        )
