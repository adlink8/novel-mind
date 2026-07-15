"""Dual-scope reader-chat budget gate contracts (unit)."""

from decimal import Decimal

import pytest

from app.services.reader_chat.budget import (
    BudgetExceeded,
    DualBudgetGate,
    ScopeBudgetGate,
    BudgetPolicy,
    UnknownPricing,
)

pytestmark = pytest.mark.unit


def test_unknown_pricing_on_either_scope_blocks_network():
    gate = DualBudgetGate(
        conversation_policy=BudgetPolicy(2, 1000, 500, Decimal("1")),
        novel_policy=BudgetPolicy(2, 1000, 500, Decimal("1")),
    )
    with pytest.raises(UnknownPricing):
        gate.reserve(
            "job:1:attempt:1",
            input_tokens=100,
            output_tokens=50,
            input_price_per_million=None,
            output_price_per_million=Decimal("1"),
        )
    assert gate.network_calls_allowed is False
    assert gate.novel.paused is True


def test_conversation_ceiling_rejects_without_novel_overspend_when_fresh():
    gate = DualBudgetGate(
        conversation=ScopeBudgetGate(BudgetPolicy(1, 100, 50, Decimal("0.01"))),
        novel=ScopeBudgetGate(BudgetPolicy(10, 10_000, 5_000, Decimal("10"))),
    )
    gate.reserve(
        "a",
        input_tokens=100,
        output_tokens=50,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    with pytest.raises(BudgetExceeded):
        gate.reserve(
            "b",
            input_tokens=1,
            output_tokens=1,
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("1"),
        )
    assert "b" not in gate.conversation.reservations
    # Novel reservation for b must not remain reserved after conversation rejection.
    assert "b" not in gate.novel.reservations or gate.novel.reservations["b"].status != "reserved"


def test_novel_ceiling_rejects_before_any_settlement():
    gate = DualBudgetGate(
        conversation=ScopeBudgetGate(BudgetPolicy(10, 10_000, 5_000, Decimal("10"))),
        novel=ScopeBudgetGate(BudgetPolicy(1, 100, 50, Decimal("0.01"))),
    )
    gate.reserve(
        "a",
        input_tokens=100,
        output_tokens=50,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    with pytest.raises(BudgetExceeded):
        gate.reserve(
            "b",
            input_tokens=1,
            output_tokens=1,
            input_price_per_million=Decimal("1"),
            output_price_per_million=Decimal("1"),
        )


def test_dual_settle_is_idempotent_single_transition():
    gate = DualBudgetGate(
        conversation_policy=BudgetPolicy(2, 200, 100, Decimal("1")),
        novel_policy=BudgetPolicy(2, 200, 100, Decimal("1")),
    )
    gate.reserve(
        "k",
        input_tokens=100,
        output_tokens=50,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    gate.settle(
        "k",
        actual_input_tokens=40,
        actual_output_tokens=10,
        actual_cost_usd=Decimal("0.1"),
    )
    assert gate.conversation.settled_calls == 1
    assert gate.novel.settled_calls == 1
    with pytest.raises(ValueError):
        gate.release("k")


def test_reservation_key_idempotent_on_both_scopes():
    gate = DualBudgetGate(
        conversation_policy=BudgetPolicy(2, 1000, 500, Decimal("1")),
        novel_policy=BudgetPolicy(2, 1000, 500, Decimal("1")),
    )
    first = gate.reserve(
        "same",
        input_tokens=10,
        output_tokens=5,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    second = gate.reserve(
        "same",
        input_tokens=10,
        output_tokens=5,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("1"),
    )
    assert first[0] is second[0]
    assert first[1] is second[1]
