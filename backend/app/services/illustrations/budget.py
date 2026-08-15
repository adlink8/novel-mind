"""Novel-scoped worst-case budget reservation/settlement for illustration jobs.

D-33-02: budget and cost are first-class. Every provider call must reserve the
worst-case cost against a frozen price snapshot before it starts, and settle
with the actual usage/cost after it finishes. Unknown usage/cost stays explicit
(``settle_unknown``) and budget exhaustion or unknown pricing fails closed —
no provider call and no retry can bypass a frozen policy.

This module is the in-memory deterministic gate (the ``reader_chat/budget.py``
analog) used by the Phase 33-01 contract tests. The durable
``illustration_budget_ledgers`` / ``illustration_budget_reservations`` rows
record the same reservation/settlement lifecycle; lease and attempt uniqueness
are enforced by the ``illustration_jobs`` / ``illustration_attempts`` schema
(partial-unique nonterminal idempotency key and unique (job, attempt_number)).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.schemas.illustration import PriceSnapshot


class BudgetExceeded(RuntimeError):
    """The novel-scope worst-case reservation exceeds the frozen policy."""


class UnknownPricing(BudgetExceeded):
    """Deployment pricing is unknown; cost cannot be reserved (fail closed)."""


@dataclass(frozen=True)
class IllustrationBudgetPolicy:
    max_calls: int
    max_cost_usd: Decimal


DEFAULT_ILLUSTRATION_POLICY = IllustrationBudgetPolicy(
    max_calls=200,
    max_cost_usd=Decimal("50.00"),
)


def worst_case_cost_usd(
    price_snapshot: PriceSnapshot,
    *,
    calls: int,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """Worst-case cost from a frozen price snapshot (D-33-02).

    Token prices are USD per million tokens; ``image_price_per_image`` is a
    flat per-image price. Fails closed (``UnknownPricing``) when a dimension
    with non-zero usage has no known price.
    """
    if calls < 0 or input_tokens < 0 or output_tokens < 0:
        raise ValueError("usage dimensions cannot be negative")

    if calls > 0 and price_snapshot.image_price_per_image is None:
        raise UnknownPricing(
            f"provider {price_snapshot.provider!r} image price is unknown; "
            "cost cannot be reserved"
        )
    if input_tokens > 0 and price_snapshot.input_price_per_million is None:
        raise UnknownPricing(
            f"provider {price_snapshot.provider!r} input token price is unknown; "
            "cost cannot be reserved"
        )
    if output_tokens > 0 and price_snapshot.output_price_per_million is None:
        raise UnknownPricing(
            f"provider {price_snapshot.provider!r} output token price is unknown; "
            "cost cannot be reserved"
        )

    image_cost = Decimal(calls) * (price_snapshot.image_price_per_image or Decimal(0))
    token_cost = (
        Decimal(input_tokens) * (price_snapshot.input_price_per_million or Decimal(0))
        + Decimal(output_tokens)
        * (price_snapshot.output_price_per_million or Decimal(0))
    ) / Decimal(1_000_000)
    return image_cost + token_cost


@dataclass
class Reservation:
    key: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    status: str = "reserved"
    settled_usage: dict[str, Any] | None = None


class IllustrationBudgetGate:
    """In-memory single-novel-scope budget gate (owner/novel per instance)."""

    def __init__(self, policy: IllustrationBudgetPolicy = DEFAULT_ILLUSTRATION_POLICY):
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
        calls: int,
        input_tokens: int,
        output_tokens: int,
        price_snapshot: PriceSnapshot,
    ) -> Reservation:
        """Reserve worst-case capacity or fail closed before any provider call."""
        existing = self.reservations.get(key)
        if existing:
            # Replaying an already-reserved key is idempotent: one charge.
            return existing
        if self.paused:
            raise BudgetExceeded("illustration budget is paused; no further calls")
        cost = worst_case_cost_usd(
            price_snapshot,
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        active = [r for r in self.reservations.values() if r.status == "reserved"]
        if (
            len(active) + 1 > self.policy.max_calls
            or sum((r.cost_usd for r in active), Decimal(0)) + cost
            > self.policy.max_cost_usd
        ):
            self.paused = True
            raise BudgetExceeded(
                "worst-case illustration reservation exceeds the frozen policy"
            )
        reservation = Reservation(
            key=key,
            calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
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
        """Settle with explicit actual usage/cost (D-33-02)."""
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("reservation already transitioned")
        if (
            actual_input_tokens > reservation.input_tokens
            or actual_output_tokens > reservation.output_tokens
        ):
            self.paused = True
            raise BudgetExceeded("provider usage exceeded the reserved worst case")
        reservation.status = "settled"
        reservation.settled_usage = {
            "input_tokens": actual_input_tokens,
            "output_tokens": actual_output_tokens,
            "cost_usd": str(actual_cost_usd),
            "usage_unknown": False,
        }
        self.settled_calls += 1

    def settle_unknown(self, key: str, *, error_code: str) -> None:
        """Mark an outcome-unknown call: usage/cost stays explicitly unknown."""
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("reservation already transitioned")
        reservation.status = "settled"
        reservation.settled_usage = {
            "usage_unknown": True,
            "cost_usd": None,
            "error_code": error_code,
        }

    def release(self, key: str) -> None:
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("only reserved entries can be released")
        reservation.status = "released"

    def snapshot(self) -> dict[str, Any]:
        """Read-only ledger summary used by tests and audits."""
        settled_unknown = [
            r
            for r in self.reservations.values()
            if r.status == "settled" and r.settled_usage.get("usage_unknown")
        ]
        return {
            "paused": self.paused,
            "settled_calls": self.settled_calls,
            "settled_cost_usd": str(
                sum(
                    (
                        Decimal(r.settled_usage["cost_usd"])
                        for r in self.reservations.values()
                        if r.status == "settled"
                        and not r.settled_usage.get("usage_unknown")
                    ),
                    Decimal(0),
                )
            ),
            "settled_unknown_count": len(settled_unknown),
        }
