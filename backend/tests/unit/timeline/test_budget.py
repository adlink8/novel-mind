"""Worst-case pre-call budget gate contract."""

from decimal import Decimal

import pytest

from app.services.timeline.budget import BudgetExceeded, BudgetGate, BudgetPolicy, UnknownPricing

pytestmark = pytest.mark.unit


def test_unknown_pricing_pauses_before_call():
    gate = BudgetGate(BudgetPolicy(2, 1000, 500, Decimal("1.00")))
    with pytest.raises(UnknownPricing):
        gate.reserve("extract:1:1", input_tokens=100, output_tokens=50, input_price_per_million=None,
                     output_price_per_million=Decimal("1"))
    assert gate.paused and gate.network_calls_allowed is False


def test_atomic_reservation_is_idempotent_and_enforces_ceiling():
    gate = BudgetGate(BudgetPolicy(1, 100, 50, Decimal("0.01")))
    first = gate.reserve("extract:1:1", input_tokens=100, output_tokens=50,
                         input_price_per_million=Decimal("1"), output_price_per_million=Decimal("1"))
    assert gate.reserve("extract:1:1", input_tokens=100, output_tokens=50,
                        input_price_per_million=Decimal("1"), output_price_per_million=Decimal("1")) is first
    with pytest.raises(BudgetExceeded):
        gate.reserve("repair:1:2", input_tokens=1, output_tokens=1,
                     input_price_per_million=Decimal("1"), output_price_per_million=Decimal("1"))


def test_settle_and_release_are_single_transition():
    gate = BudgetGate(BudgetPolicy(2, 200, 100, Decimal("1")))
    reservation = gate.reserve("extract:1:1", input_tokens=100, output_tokens=50,
                               input_price_per_million=Decimal("1"), output_price_per_million=Decimal("1"))
    gate.settle(reservation.key, actual_input_tokens=40, actual_output_tokens=10, actual_cost_usd=Decimal("0.1"))
    assert gate.settled_calls == 1
    with pytest.raises(ValueError):
        gate.release(reservation.key)
