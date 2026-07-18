"""Timeline-only structured model gateway contracts."""

from decimal import Decimal

import pytest

from app.schemas.timeline import TimelineExtraction
from app.services.timeline.budget import BudgetGate, BudgetPolicy
from app.services.timeline.model_gateway import (
    DependencyPaused,
    ModelDeployment,
    StructuredOutputRejected,
    TimelineModelGateway,
)

pytestmark = pytest.mark.unit


VALID = '{"events": [], "story_time_constraints": []}'


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def deployment(*, structured=True):
    return ModelDeployment(
        provider="openai", model_id="gpt-test", revision="2026-07-01",
        supports_structured_output=structured,
        input_price_per_million=Decimal("1"), output_price_per_million=Decimal("2"),
    )


def budget(calls=2):
    return BudgetGate(BudgetPolicy(calls, 10_000, 2_000, Decimal("1")))


@pytest.mark.asyncio
async def test_structured_call_disables_hidden_retry_stream_and_fallback():
    transport = FakeTransport([{"content": VALID, "usage": {"input_tokens": 20, "output_tokens": 5}}])
    gateway = TimelineModelGateway(transport)
    result = await gateway.generate(
        deployment=deployment(), schema=TimelineExtraction, messages=[{"role": "user", "content": "x"}],
        budget=budget(), run_id=4, stage_key="extract:7", max_input_tokens=100,
        max_output_tokens=50, timeout=12,
    )
    assert result.output.events == []
    call = transport.calls[0]
    assert call["response_format"] is TimelineExtraction
    assert call["timeout"] == 12 and call["num_retries"] == 0 and call["stream"] is False
    assert call["model"] == "openai/gpt-test"
    assert len(result.attempts) == 1 and result.attempts[0].status == "succeeded"
    assert result.attempts[0].cost_usd == Decimal("0.000030")


@pytest.mark.asyncio
async def test_capability_failure_pauses_before_network_or_budget():
    transport = FakeTransport([{"content": VALID}])
    gate = budget()
    with pytest.raises(DependencyPaused):
        await TimelineModelGateway(transport).generate(
            deployment=deployment(structured=False), schema=TimelineExtraction, messages=[], budget=gate,
            run_id=1, stage_key="extract:1", max_input_tokens=10, max_output_tokens=10,
        )
    assert transport.calls == [] and gate.reservations == {}


@pytest.mark.asyncio
async def test_local_validation_allows_exactly_one_independently_reserved_repair():
    transport = FakeTransport([
        {"content": '{"events": [], "unexpected": true}', "usage": {}},
        {"content": VALID, "usage": {"input_tokens": 8, "output_tokens": 3}},
    ])
    gate = budget()
    result = await TimelineModelGateway(transport).generate(
        deployment=deployment(), schema=TimelineExtraction, messages=[], budget=gate,
        run_id=1, stage_key="extract:1", max_input_tokens=100, max_output_tokens=50,
    )
    assert [a.status for a in result.attempts] == ["schema_rejected", "succeeded"]
    assert set(gate.reservations) == {"extract:1:repair:1", "extract:1:repair:2"}
    assert len(transport.calls) == 2
    assert "validation error" in transport.calls[1]["messages"][-1]["content"].lower()


@pytest.mark.asyncio
async def test_second_invalid_response_is_rejected_without_fallback_or_third_call():
    transport = FakeTransport([{"content": "{}", "usage": {}}, {"content": "{}", "usage": {}}])
    with pytest.raises(StructuredOutputRejected) as exc:
        await TimelineModelGateway(transport).generate(
            deployment=deployment(), schema=TimelineExtraction, messages=[], budget=budget(),
            run_id=1, stage_key="extract:1", max_input_tokens=100, max_output_tokens=50,
        )
    assert len(transport.calls) == 2
    assert len(exc.value.attempts) == 2


@pytest.mark.asyncio
async def test_business_gate_failure_uses_same_one_repair_limit():
    transport = FakeTransport([{"content": VALID, "usage": {}}, {"content": VALID, "usage": {}}])

    def reject(_):
        raise ValueError("evidence scope mismatch")

    with pytest.raises(StructuredOutputRejected):
        await TimelineModelGateway(transport).generate(
            deployment=deployment(), schema=TimelineExtraction, messages=[], budget=budget(),
            run_id=1, stage_key="extract:1", max_input_tokens=100, max_output_tokens=50,
            business_validator=reject,
        )
    assert len(transport.calls) == 2
