"""Timeline worker helper functions (transports, prices, reason clipping, reads)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from app.models.analysis import AnalysisVersion
from app.models.novel import Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.models.user import User
from app.services.timeline.model_gateway import ModelDeployment
from app.services.timeline.worker import (
    _LiteLLMTransport,
    _clip_status_reason,
    _load_persisted_candidates,
    _prices,
    production_runtime,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clip_status_reason_limits_length_and_cleans_whitespace():
    assert _clip_status_reason(None) is None
    assert _clip_status_reason("ok") == "ok"
    assert _clip_status_reason("multi\nline") == "multi line"
    long_reason = "x" * 200
    clipped = _clip_status_reason(long_reason)
    assert len(clipped) == 128
    assert clipped.endswith("…")


def test_prices_snapshots_deployment():
    deployment = ModelDeployment(
        provider="openai",
        model_id="gpt-x",
        revision="rev-1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1.5"),
        output_price_per_million=Decimal("3"),
    )
    prices = _prices(deployment)
    assert prices == {
        "provider": "openai",
        "model_id": "gpt-x",
        "revision": "rev-1",
        "input_price_per_million": "1.5",
        "output_price_per_million": "3",
    }


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_litellm_transport_normalizes_usage():
    transport = _LiteLLMTransport()
    message = SimpleNamespace(content="litellm answer")
    usage = SimpleNamespace(
        model_dump=lambda: {"prompt_tokens": 5, "completion_tokens": 2}
    )
    response = SimpleNamespace(
        id="ll-1", usage=usage, choices=[SimpleNamespace(message=message)]
    )
    with mock.patch(
        "litellm.acompletion", new=mock.AsyncMock(return_value=response)
    ) as acompl:
        out = await transport.complete(model="gpt-x", messages=[], temperature=0.1)
    assert out["content"] == "litellm answer"
    assert out["usage"]["prompt_tokens"] == 5
    assert acompl.call_args.kwargs["temperature"] == 0.1


@pytest.mark.asyncio
async def test_litellm_transport_plain_usage_namespace():
    transport = _LiteLLMTransport()
    message = SimpleNamespace(content="x")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    response = SimpleNamespace(
        id="ll-2", usage=usage, choices=[SimpleNamespace(message=message)]
    )
    with mock.patch("litellm.acompletion", new=mock.AsyncMock(return_value=response)):
        out = await transport.complete(model="gpt-x", messages=[])
    # usage without model_dump is passed through as-is (SimpleNamespace)
    assert out["usage"].prompt_tokens == 1
    assert out["usage"].completion_tokens == 1


# ---------------------------------------------------------------------------
# production_runtime
# ---------------------------------------------------------------------------


def test_production_runtime_uses_configured_litellm_provider(monkeypatch):
    monkeypatch.setattr("app.config.settings.chat_provider", "gemini")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gemini-2.5-flash")
    runtime = _run(production_runtime())
    assert runtime.extraction_deployment.provider == "gemini"
    assert runtime.extraction_deployment.model_id == "gemini-2.5-flash"
    assert isinstance(runtime.gateway.transport, _LiteLLMTransport)
    assert runtime.extraction_prompt


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_production_runtime_prefers_owner_default_model_config(monkeypatch):
    from app.services.reader_chat.worker import ModelDeployment as OwnedDeployment

    monkeypatch.setattr("app.config.settings.chat_provider", "openai")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gpt-4o-mini")
    monkeypatch.setattr(
        "app.config.settings.analysis_input_price_per_million", Decimal("0.14")
    )
    monkeypatch.setattr(
        "app.config.settings.analysis_output_price_per_million", Decimal("0.28")
    )

    async def fake_resolve(*, owner_id, **kwargs):
        assert owner_id == 2
        return OwnedDeployment(
            provider="custom",
            model_id="deepseek-v4-flash",
            revision="ai_model_config:13",
            supports_structured_output=True,
            input_price_per_million=Decimal("0.15"),
            output_price_per_million=Decimal("0.60"),
            config_id=13,
            api_key="owned-key",
            base_url="https://opencode.ai/zen/go/v1",
        )

    monkeypatch.setattr(
        "app.services.reader_chat.worker.resolve_reader_chat_deployment",
        fake_resolve,
    )

    runtime = _run(production_runtime(owner_id=2))
    deployment = runtime.extraction_deployment
    # custom 协议映射为 litellm 认识的 openai 兼容名
    assert deployment.provider == "openai"
    assert deployment.model_id == "deepseek-v4-flash"
    assert deployment.revision == "ai_model_config:13"
    assert deployment.supports_structured_output is True
    # 计价用分析 worker 的 settings（而非 reader chat 的硬编码价）
    assert deployment.input_price_per_million == Decimal("0.14")
    assert deployment.output_price_per_million == Decimal("0.28")
    # 凭据只在部署对象上，等待网关传入传输层
    assert deployment.api_key == "owned-key"
    assert deployment.base_url == "https://opencode.ai/zen/go/v1"


def test_production_runtime_owner_config_failure_pauses_honestly(monkeypatch):
    """owner 配置存在但有毛病 → 带原因暂停，禁止静默回落 env（重演糊涂账）。"""
    from app.services.reader_chat.gateway import (
        DependencyPaused as ReaderDependencyPaused,
    )
    from app.services.timeline.model_gateway import DependencyPaused

    async def fake_resolve(*, owner_id, **kwargs):
        raise ReaderDependencyPaused("owner_default_model_api_key_missing")

    monkeypatch.setattr(
        "app.services.reader_chat.worker.resolve_reader_chat_deployment",
        fake_resolve,
    )

    with pytest.raises(DependencyPaused) as excinfo:
        _run(production_runtime(owner_id=2))
    assert "owner_default_model_api_key_missing" in str(excinfo.value)


def test_production_runtime_env_fallback_without_owner_honors_schema_override(
    monkeypatch,
):
    """owner_id=None（无 run 上下文）才走 env 回退；未知模型靠开关放行。"""
    monkeypatch.setattr("app.config.settings.chat_provider", "openai")
    monkeypatch.setattr(
        "app.config.settings.default_chat_model", "deepseek-v4-flash"
    )
    monkeypatch.setattr(
        "app.config.settings.analysis_force_structured_output", True
    )

    runtime = _run(production_runtime())
    deployment = runtime.extraction_deployment
    assert deployment.provider == "openai"
    assert deployment.model_id == "deepseek-v4-flash"
    # litellm 注册表不认识该模型，靠开关放行 structured output
    assert deployment.supports_structured_output is True
    assert deployment.api_key is None


# ---------------------------------------------------------------------------
# _LiteLLMTransport streaming accumulation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_litellm_transport_accumulates_stream(monkeypatch):
    from types import SimpleNamespace as NS

    chunks = [
        NS(id="c-1", choices=[NS(delta=NS(content="hel"))], usage=None),
        NS(id="c-1", choices=[NS(delta=NS(content="lo"))], usage=None),
        NS(
            id="c-1",
            choices=[NS(delta=NS(content=None))],
            usage=NS(model_dump=lambda: {"prompt_tokens": 5, "completion_tokens": 2}),
        ),
    ]

    class _FakeAsyncStream:
        def __aiter__(self):
            async def _gen():
                for chunk in chunks:
                    yield chunk

            return _gen()

    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncStream()

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    transport = _LiteLLMTransport()
    out = await transport.complete(
        model="openai/deepseek-v4-flash",
        messages=[{"role": "user", "content": "x"}],
        stream=False,
    )
    # 传输层强制流式并请求 usage，调用方无感
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert out["content"] == "hello"
    assert out["id"] == "c-1"
    assert out["usage"]["prompt_tokens"] == 5
    assert out["usage"]["completion_tokens"] == 2


# ---------------------------------------------------------------------------
# _load_persisted_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_persisted_candidates_rebuilds_events(db_session):
    from app.models.novel import Chapter

    owner = User(
        username="wm-worker", email="wm-worker@example.com", hashed_password="x"
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="工人书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="正文"
    )
    db_session.add(chapter)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="worker-v1",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    event = MachineTimelineEvent(
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="1:e1",
        title="事件",
        description="描述",
        event_type="plot",
        time_precision="unknown",
        time_expression=None,
        narrative_chapter_number=1,
        narrative_index=0,
        story_rank=None,
        story_constraints=[],
        confidence=0.9,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={"stage": "chapter_extract"},
        publication_status="provisional",
    )
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        TimelineParticipant(event_id=event.id, entity_id=None, mention="阿宁")
    )
    db_session.add(
        TimelineEvidenceRef(
            event_id=event.id,
            chapter_id=chapter.id,
            evidence_id="ev-1",
            source_start=0,
            source_end=1,
            content_hash="0" * 64,
        )
    )
    await db_session.commit()

    candidates = await _load_persisted_candidates(db_session, version.id)
    assert len(candidates) == 1
    assert candidates[0].candidate_id == "1:e1"
    assert candidates[0].participants[0].mention == "阿宁"
    assert candidates[0].evidence[0].evidence_id == "ev-1"
    assert candidates[0].story_time.precision.value == "unknown"
