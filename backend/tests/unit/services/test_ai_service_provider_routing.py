"""Provider protocol routing for the shared AI service."""

from types import SimpleNamespace

import pytest

from app.services.ai_service import AIService

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("provider", "model_id", "expected"),
    [
        ("openai", "gpt-provider-a", "gpt-provider-a"),
        ("custom", "compatible-model-a", "openai/compatible-model-a"),
        ("anthropic", "claude-provider-a", "anthropic/claude-provider-a"),
        ("gemini", "gemini-provider-a", "gemini/gemini-provider-a"),
        ("ollama", "qwen3:8b", "ollama/qwen3:8b"),
    ],
)
def test_litellm_model_name_covers_all_settings_providers(
    provider,
    model_id,
    expected,
):
    assert AIService.litellm_model_name(provider, model_id) == expected


def test_clue_runtime_uses_configured_gemini_without_vertex(monkeypatch):
    from app.services.clues.worker import production_runtime

    monkeypatch.setattr("app.config.settings.chat_provider", "gemini")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gemini-2.5-flash")

    runtime = production_runtime()

    assert runtime.deployment.provider == "gemini"
    assert runtime.deployment.model_id == "gemini-2.5-flash"
    assert runtime.judge.resolve_model_name() == "gemini/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_reader_chat_requires_owner_default_model(monkeypatch):
    from app.services.reader_chat.gateway import DependencyPaused
    from app.services.reader_chat.worker import resolve_reader_chat_deployment

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def execute(self, _statement):
            return Result()

    class Sessions:
        def __call__(self):
            return Session()

    monkeypatch.setattr("app.config.settings.chat_provider", "gemini")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gemini-2.5-flash")

    with pytest.raises(DependencyPaused, match="owner_default_model_missing"):
        await resolve_reader_chat_deployment(owner_id=2, sessions=Sessions())


@pytest.mark.asyncio
async def test_reader_chat_resolves_owner_default_model_with_credentials(monkeypatch):
    """Reader Chat must use the active default owned by the job owner."""
    from app.services.reader_chat.worker import resolve_reader_chat_deployment

    owner_model = SimpleNamespace(
        id=13,
        owner_id=2,
        provider="custom",
        model_id="deepseek-v4-flash",
        api_key="owner-config-key",
        base_url="https://opencode.ai/zen/go/v1",
        extra_params={},
    )

    class Result:
        def scalars(self):
            return self

        def all(self):
            return [owner_model]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def execute(self, _statement):
            return Result()

    class Sessions:
        def __call__(self):
            return Session()

    monkeypatch.setattr("app.config.settings.chat_provider", "openai")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gpt-4o-mini")
    async def validate_base_url(value):
        return value

    monkeypatch.setattr(
        "app.core.url_security.validate_ai_base_url",
        validate_base_url,
    )

    deployment = await resolve_reader_chat_deployment(
        owner_id=2,
        sessions=Sessions(),
    )

    assert deployment.config_id == 13
    assert deployment.provider == "custom"
    assert deployment.model_id == "deepseek-v4-flash"
    assert deployment.api_key == "owner-config-key"
    assert deployment.base_url == "https://opencode.ai/zen/go/v1"


@pytest.mark.asyncio
async def test_custom_chat_uses_openai_compatible_litellm_route(monkeypatch):
    """自定义端点必须走 OpenAI-compatible，不得被全局 provider 接管。"""
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    async def fake_log_usage(**kwargs):
        return None

    monkeypatch.setattr("app.services.ai_service.litellm.acompletion", fake_acompletion)
    monkeypatch.setattr("app.services.ai_service._log_usage", fake_log_usage)

    service = AIService()
    response = await service.chat(
        messages=[{"role": "user", "content": "hi"}],
        model=service.litellm_model_name("custom", "deepseek-v4-flash"),
        api_key="provider-secret",
        api_base="https://opencode.ai/zen/go/v1",
    )

    assert response.choices[0].message.content == "OK"
    assert captured["model"] == "openai/deepseek-v4-flash"
    assert captured["api_key"] == "provider-secret"
    assert captured["api_base"] == "https://opencode.ai/zen/go/v1"


@pytest.mark.asyncio
async def test_reader_chat_transport_forwards_frozen_owner_connection(monkeypatch):
    from app.services.reader_chat.worker import _LiteLLMTransport

    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="owner-call",
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"answer":true}'))
            ],
            usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1),
        )

    monkeypatch.setattr("app.services.ai_service.ai_service.chat", fake_chat)

    await _LiteLLMTransport().complete(
        model="openai/deepseek-v4-flash",
        messages=[{"role": "user", "content": "hello"}],
        api_key="owner-config-key",
        api_base="https://opencode.ai/zen/go/v1",
        max_tokens=16,
    )

    assert captured["model"] == "openai/deepseek-v4-flash"
    assert captured["api_key"] == "owner-config-key"
    assert captured["api_base"] == "https://opencode.ai/zen/go/v1"


def test_custom_reader_chat_model_id_does_not_double_prefix_openai_route():
    from app.services.reader_chat.gateway import ModelDeployment

    deployment = ModelDeployment(
        provider="custom",
        model_id="openai/already-qualified",
        revision="ai_model_config:13",
        supports_structured_output=True,
        input_price_per_million=None,
        output_price_per_million=None,
    )

    assert deployment.resolved_name == "openai/already-qualified"


@pytest.mark.asyncio
async def test_stream_chat_surfaces_tool_calls_from_litellm(monkeypatch):
    """Pi gateway must receive function calls, not silently drop them as empty text."""

    async def chunks():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id="call-1",
                                function=SimpleNamespace(
                                    name="get_chapter",
                                    arguments='{"novel_id":91,',
                                ),
                            )
                        ],
                    )
                )
            ]
        )
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                index=0,
                                id=None,
                                function=SimpleNamespace(
                                    name=None,
                                    arguments='"chapter_id":59}',
                                ),
                            )
                        ],
                    )
                )
            ]
        )

    async def fake_acompletion(**_kwargs):
        return chunks()

    async def fake_log_usage(**_kwargs):
        return None

    monkeypatch.setattr("app.services.ai_service.litellm.acompletion", fake_acompletion)
    monkeypatch.setattr("app.services.ai_service._log_usage", fake_log_usage)

    output = [
        item
        async for item in AIService().stream_chat(
            messages=[{"role": "user", "content": "analyze"}],
            tools=[{"type": "function", "function": {"name": "get_chapter"}}],
        )
    ]

    assert output == [
        {
            "__tool_calls__": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "get_chapter",
                        "arguments": '{"novel_id":91,"chapter_id":59}',
                    },
                }
            ]
        }
    ]
