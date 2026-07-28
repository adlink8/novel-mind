"""07-03 budget ledger tests."""

from __future__ import annotations
import pytest
from app.services.chunking.budget import BudgetConfig, BudgetLedger
from app.services.chunking.adjudicator import BoundaryAdjudicator
from app.services.chunking.rules import analyze_chapter, RuleEngineConfig

pytestmark = pytest.mark.unit


def test_budget_blocks_oversized_input():
    led = BudgetLedger(BudgetConfig(max_input_tokens_per_call=10))
    ok, reason = led.can_call("bp1", 100)
    assert ok is False
    assert "input" in reason


def test_worst_case_gate():
    led = BudgetLedger(
        BudgetConfig(
            max_boundaries_per_run=2,
            max_total_input_tokens=100,
            max_input_tokens_per_call=50,
            max_attempts_per_boundary=2,
            max_total_output_tokens=1000,
        )
    )
    assert led.worst_case_ok(1) is True
    assert led.worst_case_ok(100) is False


@pytest.mark.asyncio
async def test_worst_case_skip_no_llm_calls():
    text = "句。" * 40
    spans, props = analyze_chapter(
        chapter_id=1,
        chapter_number=1,
        content=text,
        cfg=RuleEngineConfig(20, 40, auto_accept=0.99),
    )
    calls = {"n": 0}

    async def boom(s, u):
        calls["n"] += 1
        raise AssertionError("should not call")

    led = BudgetLedger(
        BudgetConfig(
            max_boundaries_per_run=0,
            max_total_input_tokens=1,
            max_total_output_tokens=1,
        )
    )
    adj = BoundaryAdjudicator(llm=boom, budget=led)
    await adj.adjudicate_pending(props, spans)
    assert calls["n"] == 0
