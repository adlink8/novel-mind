"""网关固定请求头契约：OpenCode Go 要求 x-opencode-session 稳定会话标识，
缺头直接 400（2026-09-07 线上故障）。网关必须把 transport_headers 透传为
transport 的 extra_headers，且不得覆盖调用方显式传入的头。"""

import pytest

from app.schemas.timeline import TimelineExtraction
from app.services.timeline.budget import BudgetGate, BudgetPolicy
from app.services.timeline.model_gateway import TimelineModelGateway
from decimal import Decimal

pytestmark = pytest.mark.unit

VALID = '{"events": [], "story_time_constraints": []}'


class _RecordingTransport:
    def __init__(self):
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "id": "t1",
            "content": VALID,
            "usage": {"input_tokens": 8, "output_tokens": 3},
        }


def _deployment():
    from app.services.timeline.model_gateway import ModelDeployment

    return ModelDeployment(
        provider="openai",
        model_id="gpt-test",
        revision="2026-07-01",
        supports_structured_output=True,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
    )


@pytest.mark.asyncio
async def test_transport_headers_are_forwarded_as_extra_headers():
    transport = _RecordingTransport()
    gateway = TimelineModelGateway(
        transport,
        transport_headers={
            "x-opencode-session": "run-session-abc",
            "User-Agent": "novelmind-timeline-worker/0.1",
        },
    )
    await gateway.generate(
        deployment=_deployment(),
        schema=TimelineExtraction,
        messages=[],
        budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
        run_id=1,
        stage_key="extract:1",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert transport.calls[0]["extra_headers"]["x-opencode-session"] == (
        "run-session-abc"
    )
    assert transport.calls[0]["extra_headers"]["User-Agent"].startswith(
        "novelmind-"
    )


@pytest.mark.asyncio
async def test_no_headers_means_no_extra_headers_key():
    transport = _RecordingTransport()
    gateway = TimelineModelGateway(transport)
    await gateway.generate(
        deployment=_deployment(),
        schema=TimelineExtraction,
        messages=[],
        budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
        run_id=1,
        stage_key="extract:1",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert "extra_headers" not in transport.calls[0]
