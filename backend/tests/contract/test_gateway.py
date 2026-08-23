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
import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.config import settings
from app.models import AIModelConfig, Novel, SkillRegistry, SkillRun, SkillVersion, User

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


async def _seed_agent_run(
    db_session,
    *,
    base_url: str | None = "http://127.0.0.1:9999/v1",
    with_default_model: bool = True,
) -> tuple[Novel, str]:
    """建立绑定 owner/novel 的运行上下文，供逻辑模型契约复用。"""
    owner = (
        await db_session.execute(select(User).where(User.username == "testuser"))
    ).scalar_one()
    novel = Novel(owner_id=owner.id, title="网关模型配置契约")
    db_session.add(novel)
    await db_session.flush()

    registry = SkillRegistry(
        owner_id=owner.id,
        novel_id=novel.id,
        name="answer-reading-question",
        status="active",
    )
    db_session.add(registry)
    await db_session.flush()
    version = SkillVersion(
        registry_id=registry.id,
        owner_id=owner.id,
        novel_id=novel.id,
        name=registry.name,
        version="1.0.0",
        yaml_checksum="a" * 64,
        allowed_tools=["get_novel"],
        read_permissions=[],
        write_permissions=[],
        forbidden_spaces=[],
        budget={},
        approval_required_for=[],
        input_schema={},
        output_schema={},
        status="active",
    )
    db_session.add(version)
    await db_session.flush()

    run_token = "run-owner-model-contract-token"
    run = SkillRun(
        owner_id=owner.id,
        novel_id=novel.id,
        skill_version_id=version.id,
        status="running",
        input={"novel_id": novel.id, "question": "测试"},
        input_hash="b" * 64,
        internal_token_hash=hashlib.sha256(run_token.encode("utf-8")).hexdigest(),
    )
    db_session.add(run)
    if with_default_model:
        db_session.add(
            AIModelConfig(
                owner_id=owner.id,
                name="实际 OpenAI 兼容连接",
                provider="openai",
                model_id="provider-model-1",
                base_url=base_url,
                api_key="provider-secret",
                is_default=True,
                is_active=True,
            )
        )
    await db_session.commit()
    return novel, run_token


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


async def test_gateway_logical_model_uses_run_owner_default_config(
    auth_client: AsyncClient,
    db_session,
    monkeypatch,
):
    """Pi 的逻辑模型必须解析为运行 owner 保存的真实模型连接。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_agent_run(db_session)

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200
    assert fake.chat_calls[0]["model"] == "provider-model-1"
    assert fake.chat_calls[0]["api_key"] == "provider-secret"
    assert fake.chat_calls[0]["api_base"] == "http://127.0.0.1:9999/v1"


async def test_gateway_logical_model_requires_run_context(gateway_client):
    """逻辑模型不能绕过 per-run owner 绑定。"""
    client, fake = gateway_client
    resp = await client.post(
        "/api/gateway/v1/chat/completions",
        headers=_auth_headers(),
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 401
    assert not fake.chat_calls


async def test_gateway_logical_model_requires_owner_default(
    auth_client: AsyncClient,
    db_session,
    monkeypatch,
):
    """运行 owner 未配置默认模型时必须失败关闭。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_agent_run(db_session, with_default_model=False)

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 409
    assert not fake.chat_calls


async def test_gateway_rejects_unsafe_stored_base_url(
    auth_client: AsyncClient,
    db_session,
    monkeypatch,
):
    """即使数据库被直接写入危险地址，网关也不能发起请求。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_agent_run(
        db_session,
        base_url="http://169.254.169.254/latest/meta-data",
    )

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    assert not fake.chat_calls


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


async def _seed_skill_run(db_session, *, skill_name: str) -> tuple[Novel, str]:
    """与 _seed_agent_run 相同，但技能名可变（分析类 vs 问答类契约）。"""
    owner = (
        await db_session.execute(select(User).where(User.username == "testuser"))
    ).scalar_one()
    novel = Novel(owner_id=owner.id, title="JSON 契约技能")
    db_session.add(novel)
    await db_session.flush()
    registry = SkillRegistry(
        owner_id=owner.id, novel_id=novel.id, name=skill_name, status="active"
    )
    db_session.add(registry)
    await db_session.flush()
    version = SkillVersion(
        registry_id=registry.id,
        owner_id=owner.id,
        novel_id=novel.id,
        name=registry.name,
        version="1.0.0",
        yaml_checksum="a" * 64,
        allowed_tools=["get_novel"],
        read_permissions=[],
        write_permissions=[],
        forbidden_spaces=[],
        budget={},
        approval_required_for=[],
        input_schema={},
        output_schema={},
        status="active",
    )
    db_session.add(version)
    await db_session.flush()
    run_token = f"run-token-{skill_name}"
    db_session.add(
        SkillRun(
            owner_id=owner.id,
            novel_id=novel.id,
            skill_version_id=version.id,
            status="running",
            input={"novel_id": novel.id, "question": "测试"},
            input_hash="b" * 64,
            internal_token_hash=hashlib.sha256(run_token.encode("utf-8")).hexdigest(),
        )
    )
    db_session.add(
        AIModelConfig(
            owner_id=owner.id,
            name="连接",
            provider="openai",
            model_id="provider-model-1",
            base_url="http://127.0.0.1:9999/v1",
            api_key="provider-secret",
            is_default=True,
            is_active=True,
        )
    )
    await db_session.commit()
    return novel, run_token


@pytest.mark.parametrize(
    "skill_name",
    [
        "analyze-chapter",
        "detect-key-scenes",
        "propose-world-model-candidates",
        "build-visual-bible",
    ],
)
async def test_gateway_forces_json_object_for_analysis_skills(
    auth_client: AsyncClient, db_session, monkeypatch, skill_name: str
):
    """结构化 JSON 契约的分析 skill：网关必须向上游强制 response_format。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_skill_run(db_session, skill_name=skill_name)

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200
    assert fake.chat_calls[0]["response_format"] == {"type": "json_object"}


async def test_gateway_does_not_constrain_prose_skills(
    auth_client: AsyncClient, db_session, monkeypatch
):
    """问答等散文 skill：绝不注入 response_format（cited answer 是自然语言）。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_skill_run(
        db_session, skill_name="answer-reading-question"
    )

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert resp.status_code == 200
    assert fake.chat_calls[0]["response_format"] is None


# ─────────────── 模型底座适配（档案驱动 / 语义保真） ───────────────


def test_normalize_messages_preserves_reasoning_for_thinking_models():
    """thinking 模型的 reasoning_content/reasoning_details 必须随 assistant 历史回传。"""
    from app.api.gateway import GatewayChatMessage, _normalize_messages

    messages = [
        GatewayChatMessage(role="user", content="q"),
        GatewayChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
            reasoning_content="因为……所以……",
            reasoning_details=[{"id": "c1", "signature": "sig"}],
        ),
        GatewayChatMessage(role="tool", content="{}", tool_call_id="c1"),
    ]
    out = _normalize_messages(messages)
    assert out[1]["reasoning_content"] == "因为……所以……"
    assert out[1]["reasoning_details"] == [{"id": "c1", "signature": "sig"}]
    assert out[1]["tool_calls"][0]["id"] == "c1"


async def test_gateway_non_stream_propagates_real_finish_reason(
    auth_client: AsyncClient, db_session, monkeypatch
):
    """上游 finish_reason=length 必须原样透出，绝不伪装成 stop。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")

    class _LengthChoice:
        message = _FakeMessage()
        finish_reason = "length"

    class _LengthResponse(_FakeChatResponse):
        choices = [_LengthChoice()]

    fake = _FakeAiService()
    fake.chat_response = _LengthResponse()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_skill_run(
        db_session, skill_name="answer-reading-question"
    )

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["finish_reason"] == "length"


async def test_gateway_uses_model_config_defaults_for_budget_params(
    auth_client: AsyncClient, db_session, monkeypatch
):
    """max_tokens/temperature 未显式指定时采用模型档案值，不再用硬编码兜底。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_skill_run(
        db_session, skill_name="answer-reading-question"
    )
    await db_session.execute(
        update(AIModelConfig).values(max_tokens=8192, temperature=0.3)
    )
    await db_session.commit()

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert fake.chat_calls[0]["max_tokens"] == 8192
    assert fake.chat_calls[0]["temperature"] == 0.3


async def test_gateway_stream_forwards_upstream_finish_reason(
    auth_client: AsyncClient, db_session, monkeypatch
):
    """流式终止 chunk 携带上游真实 finish_reason（length 不再被伪装成 stop）。"""
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    monkeypatch.setattr(settings, "ai_allowed_private_hosts", "127.0.0.1")

    class _LengthStreamFake(_FakeAiService):
        async def stream_chat(self, **kwargs):
            self.stream_calls.append(kwargs)
            yield {"__finish_reason__": "length"}

    fake = _LengthStreamFake()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)
    novel, run_token = await _seed_skill_run(
        db_session, skill_name="answer-reading-question"
    )

    resp = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            **_auth_headers(),
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert '"finish_reason": "length"' in resp.text
