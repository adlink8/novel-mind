"""07-03 adjudicator tests with fake LLM."""

from __future__ import annotations
import json
import pytest
from app.services.chunking.adjudicator import (
    BoundaryAdjudicator,
    apply_decisions_to_proposals,
)
from app.services.chunking.budget import BudgetLedger, BudgetConfig
from app.services.chunking.rules import analyze_chapter, RuleEngineConfig
from app.services.chunking.segmentation import segment_from_proposals

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_only_eligible_calls_llm():
    text = "叙述句子一。" * 8 + "叙述句子二。" * 8
    spans, props = analyze_chapter(
        chapter_id=1,
        chapter_number=1,
        content=text,
        cfg=RuleEngineConfig(40, 100, auto_accept=0.99),
    )
    calls = {"n": 0}

    async def fake(system, user):
        calls["n"] += 1
        # parse boundary id from user payload
        import re

        m = re.search(r'"boundary_id":\s*"([^"]+)"', user)
        bid = m.group(1) if m else props[0].proposal_id
        prop = next(p for p in props if p.proposal_id == bid)
        return json.dumps(
            {
                "schema_version": "boundary-decision.v1",
                "boundary_id": prop.proposal_id,
                "decision": "merge",
                "reason_codes": ["TARGET_SIZE"],
                "left_span_id": prop.left_span_id,
                "right_span_id": prop.right_span_id,
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )

    led = BudgetLedger(
        BudgetConfig(
            max_boundaries_per_run=100,
            max_total_input_tokens=500_000,
            max_total_output_tokens=50_000,
            max_input_tokens_per_call=5000,
        )
    )
    adj = BoundaryAdjudicator(llm=fake, budget=led)
    decisions = await adj.adjudicate_pending(props, spans)
    eligible = [p for p in props if p.llm_eligible]
    assert calls["n"] == len(eligible)
    assert len(decisions) == len(eligible)


@pytest.mark.asyncio
async def test_malformed_json_falls_back():
    text = "甲。" * 12 + "乙。" * 12
    spans, props = analyze_chapter(
        chapter_id=2,
        chapter_number=1,
        content=text,
        cfg=RuleEngineConfig(30, 80, auto_accept=0.99),
    )

    async def bad(system, user):
        return "not-json{{"

    adj = BoundaryAdjudicator(llm=bad)
    pending = [p for p in props if p.llm_eligible]
    if not pending:
        pytest.skip("no eligible")
    d, audit = await adj.adjudicate_one(pending[0], spans)
    assert audit.fallback is True
    assert d == pending[0].fallback_decision


@pytest.mark.asyncio
async def test_apply_decisions_then_segment():
    text = "段落实体。" * 15
    spans, props = analyze_chapter(
        chapter_id=3,
        chapter_number=1,
        content=text,
        cfg=RuleEngineConfig(40, 90, auto_accept=0.99),
    )

    async def fake(system, user):
        import re

        m = re.search(r'"boundary_id":\s*"([^"]+)"', user)
        prop = next(p for p in props if p.proposal_id == m.group(1))
        return json.dumps(
            {
                "schema_version": "boundary-decision.v1",
                "boundary_id": prop.proposal_id,
                "decision": prop.fallback_decision,
                "reason_codes": list(prop.reason_codes)[:1] or ["TARGET_SIZE"],
                "left_span_id": prop.left_span_id,
                "right_span_id": prop.right_span_id,
                "confidence": 0.85,
            }
        )

    adj = BoundaryAdjudicator(llm=fake)
    decisions = await adj.adjudicate_pending(props, spans)
    updated = apply_decisions_to_proposals(props, decisions)
    seg = segment_from_proposals(spans, updated)
    assert seg.segments
