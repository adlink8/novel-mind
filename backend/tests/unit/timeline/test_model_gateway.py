"""Timeline-only structured model gateway contracts."""

from decimal import Decimal
from types import SimpleNamespace

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
        provider="openai",
        model_id="gpt-test",
        revision="2026-07-01",
        supports_structured_output=structured,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
    )


def budget(calls=2):
    return BudgetGate(BudgetPolicy(calls, 10_000, 2_000, Decimal("1")))


@pytest.mark.asyncio
async def test_structured_call_explicit_stream_no_hidden_retry():
    transport = FakeTransport(
        [{"content": VALID, "usage": {"input_tokens": 20, "output_tokens": 5}}]
    )
    gateway = TimelineModelGateway(transport)
    result = await gateway.generate(
        deployment=deployment(),
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "x"}],
        budget=budget(),
        run_id=4,
        stage_key="extract:7",
        max_input_tokens=100,
        max_output_tokens=50,
        timeout=12,
    )
    assert result.output.events == []
    call = transport.calls[0]
    assert call["response_format"] is TimelineExtraction
    assert call["timeout"] == 12 and call["num_retries"] == 0 and call["stream"] is True
    assert call["model"] == "openai/gpt-test"
    assert len(result.attempts) == 1 and result.attempts[0].status == "succeeded"
    assert result.attempts[0].cost_usd == Decimal("0.000030")


@pytest.mark.asyncio
async def test_transport_receives_owner_credentials_only_when_present():
    transport = FakeTransport(
        [{"content": VALID, "usage": {"input_tokens": 10, "output_tokens": 2}}]
    )
    gateway = TimelineModelGateway(transport)
    owned = ModelDeployment(
        provider="openai",
        model_id="deepseek-v4-flash",
        revision="ai_model_config:13",
        supports_structured_output=True,
        input_price_per_million=Decimal("0.14"),
        output_price_per_million=Decimal("0.28"),
        api_key="owned-key",
        base_url="https://opencode.ai/zen/go/v1",
    )
    await gateway.generate(
        deployment=owned,
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "x"}],
        budget=budget(),
        run_id=4,
        stage_key="extract:7",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    call = transport.calls[0]
    assert call["api_key"] == "owned-key"
    assert call["api_base"] == "https://opencode.ai/zen/go/v1"

    bare = FakeTransport(
        [{"content": VALID, "usage": {"input_tokens": 10, "output_tokens": 2}}]
    )
    await TimelineModelGateway(bare).generate(
        deployment=deployment(),
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "x"}],
        budget=budget(),
        run_id=4,
        stage_key="extract:8",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert "api_key" not in bare.calls[0]
    assert "api_base" not in bare.calls[0]


@pytest.mark.asyncio
async def test_transient_error_retries_same_round_then_succeeds(monkeypatch):
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    transport = FakeTransport(
        [
            litellm_InternalServerError("upstream blew up"),
            {"content": VALID, "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
    )
    gateway = TimelineModelGateway(transport)
    result = await gateway.generate(
        deployment=deployment(),
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "x"}],
        budget=budget(),
        run_id=4,
        stage_key="extract:7",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert result.output.events == []
    assert len(transport.calls) == 2
    statuses = [a.status for a in result.attempts]
    assert statuses == ["outcome_unknown", "succeeded"]


@pytest.mark.asyncio
async def test_transient_retry_exhaustion_raises_with_all_attempts(monkeypatch):
    from app.services.timeline import model_gateway as gw

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    monkeypatch.setattr(gw, "_TRANSIENT_RETRY_LIMIT", 2)
    transport = FakeTransport(
        [litellm_InternalServerError("boom")] * 10
    )
    gateway = TimelineModelGateway(transport)
    with pytest.raises(gw.ModelCallFailed) as excinfo:
        await gateway.generate(
            deployment=deployment(),
            schema=TimelineExtraction,
            messages=[{"role": "user", "content": "x"}],
            budget=budget(calls=8),
            run_id=4,
            stage_key="extract:7",
            max_input_tokens=100,
            max_output_tokens=50,
        )
    # 主调用 + 2 次重试，全部耗尽后诚实抛出；未进入 repair 轮
    assert len(excinfo.value.attempts) == 3
    assert len(transport.calls) == 3


def litellm_InternalServerError(message: str) -> Exception:
    import litellm

    return litellm.InternalServerError(message, llm_provider="openai", model="gpt-test")


class _FakePersistence:
    """只记录 mark_outcome_unknown 的 pause_run 语义。"""

    def __init__(self):
        self.marks: list[bool] = []
        self.counter = 0

    async def reserve_and_start(self, **kwargs):
        self.counter += 1
        return SimpleNamespace(attempt_id=self.counter, attempt_number=self.counter)

    async def complete_attempt(self, attempt, **kwargs):
        return None

    async def mark_outcome_unknown(
        self, attempt, *, latency_ms, error_code, pause_run=True
    ):
        self.marks.append(pause_run)

    async def record_cache_hit(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_transient_retry_does_not_pause_run_until_exhausted(monkeypatch):
    async def _no_sleep(_):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    persistence = _FakePersistence()
    transport = FakeTransport(
        [
            litellm_InternalServerError("boom-1"),
            litellm_InternalServerError("boom-2"),
            {"content": VALID, "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
    )
    gateway = TimelineModelGateway(transport, persistence=persistence)
    result = await gateway.generate(
        deployment=deployment(),
        schema=TimelineExtraction,
        messages=[{"role": "user", "content": "x"}],
        budget=budget(),
        run_id=4,
        stage_key="extract:7",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert result.attempts[-1].status == "succeeded"
    # 前两次瞬态失败不翻 run 状态；重试成功即恢复，全程无暂停
    assert persistence.marks == [False, False]


@pytest.mark.asyncio
async def test_final_transient_failure_pauses_run(monkeypatch):
    from app.services.timeline import model_gateway as gw

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    monkeypatch.setattr(gw, "_TRANSIENT_RETRY_LIMIT", 1)
    persistence = _FakePersistence()
    transport = FakeTransport([litellm_InternalServerError("boom")] * 5)
    gateway = TimelineModelGateway(transport, persistence=persistence)
    with pytest.raises(gw.ModelCallFailed):
        await gateway.generate(
            deployment=deployment(),
            schema=TimelineExtraction,
            messages=[{"role": "user", "content": "x"}],
            budget=budget(calls=8),
            run_id=4,
            stage_key="extract:7",
            max_input_tokens=100,
            max_output_tokens=50,
        )
    # 首次瞬态失败保留 run 运行态，重试耗尽后的最后一次失败才置暂停
    assert persistence.marks == [False, True]


@pytest.mark.asyncio
async def test_capability_failure_pauses_before_network_or_budget():
    transport = FakeTransport([{"content": VALID}])
    gate = budget()
    with pytest.raises(DependencyPaused):
        await TimelineModelGateway(transport).generate(
            deployment=deployment(structured=False),
            schema=TimelineExtraction,
            messages=[],
            budget=gate,
            run_id=1,
            stage_key="extract:1",
            max_input_tokens=10,
            max_output_tokens=10,
        )
    assert transport.calls == [] and gate.reservations == {}


@pytest.mark.asyncio
async def test_local_validation_allows_exactly_one_independently_reserved_repair():
    transport = FakeTransport(
        [
            {"content": '{"events": [{"candidate_id": null}]}', "usage": {}},
            {"content": VALID, "usage": {"input_tokens": 8, "output_tokens": 3}},
        ]
    )
    gate = budget()
    result = await TimelineModelGateway(transport).generate(
        deployment=deployment(),
        schema=TimelineExtraction,
        messages=[],
        budget=gate,
        run_id=1,
        stage_key="extract:1",
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert [a.status for a in result.attempts] == ["schema_rejected", "succeeded"]
    assert set(gate.reservations) == {"extract:1:repair:1", "extract:1:repair:2"}
    assert len(transport.calls) == 2
    assert "validation error" in transport.calls[1]["messages"][-1]["content"].lower()


@pytest.mark.asyncio
async def test_second_invalid_response_is_rejected_without_fallback_or_third_call():
    transport = FakeTransport(
        [{"content": "{}", "usage": {}}, {"content": "{}", "usage": {}}]
    )
    with pytest.raises(StructuredOutputRejected) as exc:
        await TimelineModelGateway(transport).generate(
            deployment=deployment(),
            schema=TimelineExtraction,
            messages=[],
            budget=budget(),
            run_id=1,
            stage_key="extract:1",
            max_input_tokens=100,
            max_output_tokens=50,
        )
    assert len(transport.calls) == 2
    assert len(exc.value.attempts) == 2


@pytest.mark.asyncio
async def test_business_gate_failure_uses_same_one_repair_limit():
    transport = FakeTransport(
        [{"content": VALID, "usage": {}}, {"content": VALID, "usage": {}}]
    )

    def reject(_):
        raise ValueError("evidence scope mismatch")

    with pytest.raises(StructuredOutputRejected):
        await TimelineModelGateway(transport).generate(
            deployment=deployment(),
            schema=TimelineExtraction,
            messages=[],
            budget=budget(),
            run_id=1,
            stage_key="extract:1",
            max_input_tokens=100,
            max_output_tokens=50,
            business_validator=reject,
        )
    assert len(transport.calls) == 2
