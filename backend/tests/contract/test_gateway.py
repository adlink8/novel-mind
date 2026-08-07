"""
模型网关契约测试（25.2-02 / D-15）。

覆盖：
  - fail-closed 401：令牌缺失 / 不匹配 / 环境未配置
  - 非流式 OpenAI completion wire shape（stub AIService）
  - 流式 SSE chunk shape：`data: {...}\n\n` 终止 `data: [DONE]`
  - 拒绝客户端 base_url（防 SSRF，V10）
  - 模型列表端点
  - 委托 AIService（usage 日志在 AIService 内，本网关不重复计价）
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.config import settings

pytestmark = pytest.mark.contract

GATEWAY_TOKEN = "contract-test-gateway-token"


class _FakeMessage:
    content = "模型回答内容"


class _FakeChoice:
    message = _FakeMessage()


class _FakeUsage:
    prompt_tokens = 12
    completion_tokens = 8


class _FakeChatResponse:
    """模拟 litellm 非流式响应。"""

    id = "chatcmpl-fake001"
    choices = [_FakeChoice()]
    usage = _FakeUsage()


class _FakeAiService:
    """stub AIService：记录调用，避免真实模型调用。"""

    default_model = "contract/fake-model"

    def __init__(self) -> None:
        self.chat_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.chat_response = _FakeChatResponse()
        self.stream_deltas: list[str] = ["你好", "，世界", ""]

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        return self.chat_response

    async def stream_chat(self, **kwargs):
        self.stream_calls.append(kwargs)
        for delta in self.stream_deltas:
            if delta:
                yield delta


@pytest.fixture
def gateway_client(client: AsyncClient, monkeypatch):
    """配置网关令牌并把 AIService 换成 stub。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    return client, fake


def _auth_headers(token: str | None = GATEWAY_TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ────────────────────────── fail-closed 401 ──────────────────────────


async def test_gateway_rejects_missing_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_gateway_rejects_wrong_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers("wrong-token"),
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


async def test_gateway_fail_closed_when_token_unset(client: AsyncClient, monkeypatch):
    """环境未配置令牌 → 即使带令牌也 401（绝不降级放行）。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", "")
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers("anything"),
        json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 401


# ────────────────────────── OpenAI wire shape ──────────────────────────


async def test_gateway_non_stream_openai_shape(gateway_client):
    client, fake = gateway_client
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "contract/fake-model",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "max_tokens": 32,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "contract/fake-model"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"] == "模型回答内容"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 12
    assert body["usage"]["completion_tokens"] == 8
    # 委托了 AIService（无第二张计价表）。
    assert fake.chat_calls, "gateway 必须委托 AIService.chat"
    assert fake.chat_calls[0]["messages"] == [{"role": "user", "content": "hi"}]
    assert fake.chat_calls[0]["model"] == "contract/fake-model"
    assert fake.chat_calls[0]["task_type"] == "gateway"


async def test_gateway_stream_openai_sse_shape(gateway_client):
    client, fake = gateway_client
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "contract/fake-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    lines = resp.text.split("\n")
    chunks = [line for line in lines if line.startswith("data: ")]
    assert chunks[-1] == "data: [DONE]"  # SSE 终止标记
    # OpenAI SSE 契约：内容块带 delta.content；末尾有 finish_reason:"stop" 的
    # 终止块（delta 为空），之后才是 [DONE]。
    stop_chunk = None
    for raw in chunks[:-1]:
        payload = json.loads(raw[len("data: ") :])
        assert payload["object"] == "chat.completion.chunk"
        assert "choices" in payload
        if payload["choices"][0]["finish_reason"] == "stop":
            stop_chunk = payload
            continue
        # 内容块必须携带非空 delta.content。
        assert payload["choices"][0]["delta"]["content"]
    assert stop_chunk is not None, "流式响应必须包含 finish_reason:'stop' 终止块"
    # 流式同样委托 AIService.stream_chat。
    assert fake.stream_calls, "gateway 必须委托 AIService.stream_chat"


async def test_gateway_rejects_client_base_url(gateway_client):
    """客户端禁止传 base_url（防 SSRF，V10）。"""
    client, _ = gateway_client
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "x",
            "messages": [{"role": "user", "content": "hi"}],
            "base_url": "http://evil.example.com",
        },
    )
    assert resp.status_code == 422  # extra="forbid"


async def test_gateway_models_endpoint(gateway_client):
    client, _ = gateway_client
    resp = await client.get("/api/gateway/v1/models", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "contract/fake-model"


async def test_gateway_models_requires_token(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    resp = await client.get("/api/gateway/v1/models")
    assert resp.status_code == 401
