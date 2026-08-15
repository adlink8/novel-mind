"""Gateway runtime consumption of owner-scoped Agent task model bindings."""

from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import (
    AIModelConfig,
    AgentTaskModelBinding,
    Novel,
    SkillRegistry,
    SkillRun,
    SkillVersion,
    User,
)

pytestmark = pytest.mark.contract

GATEWAY_TOKEN = "task-binding-gateway-token"


class _FakeMessage:
    content = "绑定模型回答"


class _FakeChoice:
    message = _FakeMessage()


class _FakeResponse:
    id = "chatcmpl-task-binding"
    choices = [_FakeChoice()]
    usage = None


class _FakeAiService:
    def __init__(self) -> None:
        self.chat_calls: list[dict] = []

    async def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        return _FakeResponse()


@pytest.mark.asyncio
async def test_gateway_consumes_owner_qa_binding_and_preserves_model_lineage(
    auth_client: AsyncClient,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(settings, "novelmind_gateway_token", GATEWAY_TOKEN)
    fake = _FakeAiService()
    monkeypatch.setattr("app.api.gateway.ai_service", fake, raising=False)

    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert owner is not None
    novel = Novel(owner_id=owner.id, title="任务绑定网关契约")
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
        name="answer-reading-question",
        version="1.0.0",
        yaml_checksum="a" * 64,
        allowed_tools=[],
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

    default_model = AIModelConfig(
        owner_id=owner.id,
        name="owner-default-model",
        provider="openai",
        model_id="owner-default",
        is_default=True,
        is_active=True,
    )
    qa_model = AIModelConfig(
        owner_id=owner.id,
        name="owner-qa-model",
        provider="openai",
        model_id="owner-qa",
        is_default=False,
        is_active=True,
    )
    db_session.add_all([default_model, qa_model])
    await db_session.flush()
    db_session.add(
        AgentTaskModelBinding(
            owner_id=owner.id,
            task="qa",
            model_id=qa_model.id,
        )
    )

    run_token = "task-binding-run-token"
    run = SkillRun(
        owner_id=owner.id,
        novel_id=novel.id,
        skill_version_id=version.id,
        status="running",
        input={"question": "测试"},
        input_hash="b" * 64,
        internal_token_hash=hashlib.sha256(run_token.encode()).hexdigest(),
    )
    db_session.add(run)
    await db_session.commit()

    response = await auth_client.post(
        "/api/gateway/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GATEWAY_TOKEN}",
            "X-NovelMind-Run-Token": run_token,
            "X-NovelMind-Novel-ID": str(novel.id),
        },
        json={
            "model": "reader-chat-default",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["model"] == "owner-qa"
    assert response.json()["model_lineage"] == {
        "owner_id": owner.id,
        "task": "qa",
        "skill_name": "answer-reading-question",
        "model": "owner-qa",
        "model_config_id": qa_model.id,
    }
    assert fake.chat_calls[0]["model"] == "owner-qa"
    assert fake.chat_calls[0]["task_type"] == "qa"
    assert fake.chat_calls[0]["api_key"] is None
    assert fake.chat_calls[0]["api_base"] is None
