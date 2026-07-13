"""Atomic worst-case reservation logic executed before every provider call."""

from dataclasses import dataclass
from decimal import Decimal


class BudgetExceeded(RuntimeError): pass
class UnknownPricing(BudgetExceeded): pass


@dataclass(frozen=True)
class BudgetPolicy:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_usd: Decimal


@dataclass
class Reservation:
    key: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    status: str = "reserved"


class BudgetGate:
    """Serializable ledger core; callers persist each transition in one DB transaction."""

    def __init__(self, policy: BudgetPolicy) -> None:
        self.policy = policy
        self.reservations: dict[str, Reservation] = {}
        self.paused = False
        self.settled_calls = 0

    @property
    def network_calls_allowed(self) -> bool:
        return not self.paused

    def reserve(self, key: str, *, input_tokens: int, output_tokens: int,
                input_price_per_million: Decimal | None,
                output_price_per_million: Decimal | None) -> Reservation:
        existing = self.reservations.get(key)
        if existing:
            return existing
        if input_price_per_million is None or output_price_per_million is None:
            self.paused = True
            raise UnknownPricing("provider pricing is unknown; cost cannot be reserved")
        cost = ((Decimal(input_tokens) * input_price_per_million) +
                (Decimal(output_tokens) * output_price_per_million)) / Decimal(1_000_000)
        active = [r for r in self.reservations.values() if r.status in {"reserved", "settled"}]
        if (len(active) + 1 > self.policy.max_calls or
                sum(r.input_tokens for r in active) + input_tokens > self.policy.max_input_tokens or
                sum(r.output_tokens for r in active) + output_tokens > self.policy.max_output_tokens or
                sum((r.cost_usd for r in active), Decimal(0)) + cost > self.policy.max_cost_usd):
            self.paused = True
            raise BudgetExceeded("worst-case reservation exceeds frozen policy")
        reservation = Reservation(key, input_tokens, output_tokens, cost)
        self.reservations[key] = reservation
        return reservation

    def settle(self, key: str, *, actual_input_tokens: int, actual_output_tokens: int,
               actual_cost_usd: Decimal) -> None:
        reservation = self.reservations[key]
        if reservation.status != "reserved":
            raise ValueError("reservation already transitioned")
        if actual_input_tokens > reservation.input_tokens or actual_output_tokens > reservation.output_tokens:
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
