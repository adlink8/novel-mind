"""response_format 降级契约：zen 网关多上游路由，部分上游拒绝结构化输出
（"This response_format type is unavailable now"）。网关必须自动降级为纯
prompt 契约重试——不消耗 repair 轮，不把 run 打成 paused。"""

from decimal import Decimal

import pytest

from app.schemas.timeline import TimelineExtraction
from app.services.timeline.budget import BudgetGate, BudgetPolicy
import app.services.timeline.model_gateway as gateway_module
from app.services.timeline.model_gateway import TimelineModelGateway

pytestmark = pytest.mark.unit

# 降级重试走的是瞬态退避常量；单测里压成 0 避免真等 20s
gateway_module._TRANSIENT_RETRY_BACKOFF_S = 0.0

VALID = '{"events": [], "story_time_constraints": []}'


class BadRequestError(Exception):
    """与 litellm.BadRequestError 同名的本地替身（网关按类名识别）。"""


class _DowngradeTransport:
    """第一次调用抛 response_format 400，之后按调用记录返回。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise BadRequestError(
                "OpenAIException - Error from provider (Console Go): Upstream "
                "request failed: [invalid_request_error] This response_format "
                "type is unavailable now"
            )
        return {
            "id": f"t{len(self.calls)}",
            "content": VALID,
            "usage": {"input_tokens": 8, "output_tokens": 3},
        }


class _OtherBadRequestTransport:
    async def complete(self, **kwargs):
        raise BadRequestError("Error from provider: invalid api key")


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


def _budget():
    return BudgetGate(BudgetPolicy(10, 100_000, 50_000, Decimal("10")))


@pytest.mark.asyncio
async def test_response_format_rejection_downgrades_without_repair_round():
    transport = _DowngradeTransport()
    gateway = TimelineModelGateway(transport)
    result = await gateway.generate(
        deployment=_deployment(),
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "payload"}],
        budget=_budget(),
        run_id=1,
        stage_key="extract:1",
        max_input_tokens=1000,
        max_output_tokens=500,
    )
    assert result.output.events == []
    assert len(transport.calls) == 2
    # 首次带结构化输出，降级后不再传 response_format
    assert transport.calls[0]["response_format"] is TimelineExtraction
    assert transport.calls[1]["response_format"] is None
    # 只有一个成功 attempt，降级调用不计为失败/repair
    assert [a.status for a in result.attempts] == ["succeeded"]
    # 非持久化路径 attempt_number = repair_index（同一轮内重试）
    assert result.attempts[0].attempt_number == 1


@pytest.mark.asyncio
async def test_unrelated_bad_request_is_not_swallowed():
    gateway = TimelineModelGateway(_OtherBadRequestTransport())
    from app.services.timeline.model_gateway import ModelCallFailed

    with pytest.raises(ModelCallFailed):
        await gateway.generate(
            deployment=_deployment(),
            schema=TimelineExtraction,
            messages=[{"role": "user", "content": "payload"}],
            budget=_budget(),
            run_id=1,
            stage_key="extract:1",
            max_input_tokens=1000,
            max_output_tokens=500,
        )
