"""Public HTTP/service contract for owner-scoped preference memories."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_access_token
from app.models import (
    AgentSettings,
    Chapter,
    Novel,
    ReaderConversation,
    ReaderMessage,
    UserPreferenceMemory,
    User,
)
from app.services.agent_runtime.reader_bridge import build_reader_skill_input
from app.services.user_preference_memory import (
    build_preference_context,
    delete_preference_memory,
    extract_from_persisted_message,
)

pytestmark = pytest.mark.unit


async def _persist_message(db_session, *, owner_id: int, body: str, role: str = "user"):
    novel = Novel(owner_id=owner_id, title="preference test novel", status="ready")
    db_session.add(novel)
    await db_session.flush()
    conversation = ReaderConversation(
        owner_id=owner_id,
        novel_id=novel.id,
        title="preference test",
        next_sequence=2,
    )
    db_session.add(conversation)
    await db_session.flush()
    message = ReaderMessage(
        conversation_id=conversation.id,
        owner_id=owner_id,
        novel_id=novel.id,
        sequence=1,
        role=role,
        body=body,
        client_message_id=f"client-{conversation.id}",
    )
    db_session.add(message)
    await db_session.flush()
    return message


@pytest.mark.asyncio
async def test_persisted_explicit_preference_is_extracted_and_listed(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=True))
    message = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点，不要展开太多。"
    )

    created = await extract_from_persisted_message(db_session, message.id)
    assert len(created) == 1

    response = await auth_client.get("/api/memory/preferences")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"] == [
        {
            "id": payload["items"][0]["id"],
            "source_message_id": message.id,
            "kind": "response_style",
            "value": "concise",
            "confidence": 1.0,
            "explicit": True,
            "created_at": payload["items"][0]["created_at"],
            "expires_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_disabled_memory_neither_writes_nor_recalls(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=False))
    message = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点。"
    )

    assert await extract_from_persisted_message(db_session, message.id) == []
    response = await auth_client.get("/api/memory/preferences")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_only_explicit_stable_user_preferences_are_admitted(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    fiction = await _persist_message(
        db_session, owner_id=owner.id, body="阿宁住在竹林，月光照在青石上。"
    )
    assistant = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点。", role="assistant"
    )
    one_off_request = await _persist_message(
        db_session, owner_id=owner.id, body="请解释这一段。"
    )

    assert await extract_from_persisted_message(db_session, fiction.id) == []
    assert await extract_from_persisted_message(db_session, assistant.id) == []
    assert await extract_from_persisted_message(db_session, one_off_request.id) == []

    response = await auth_client.get("/api/memory/preferences")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_retention_sets_expiry_and_expired_memories_are_not_recalled(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(
        AgentSettings(owner_id=owner.id, memory_enabled=True, memory_retention_days=7)
    )
    message = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点。"
    )

    created = await extract_from_persisted_message(db_session, message.id)
    assert len(created) == 1
    assert created[0].expires_at is not None
    assert created[0].expires_at > datetime.now(timezone.utc) + timedelta(days=6)

    created[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()
    response = await auth_client.get("/api/memory/preferences")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_preference_context_recalls_only_active_explicit_allowlisted_values(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=True))
    allowed_message = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点。"
    )
    source_message = await _persist_message(
        db_session, owner_id=owner.id, body="这是不得注入的 source 原文。"
    )
    expired_message = await _persist_message(
        db_session, owner_id=owner.id, body="以后默认用中文回答。"
    )
    db_session.add_all(
        [
            UserPreferenceMemory(
                owner_id=owner.id,
                source_message_id=allowed_message.id,
                kind="response_style",
                value="concise",
                confidence=1.0,
                explicit=True,
            ),
            UserPreferenceMemory(
                owner_id=owner.id,
                source_message_id=source_message.id,
                kind="response_style",
                value="source原文不得进入上下文",
                confidence=1.0,
                explicit=True,
            ),
            UserPreferenceMemory(
                owner_id=owner.id,
                source_message_id=expired_message.id,
                kind="language",
                value="zh-CN",
                confidence=1.0,
                explicit=True,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
            UserPreferenceMemory(
                owner_id=owner.id,
                source_message_id=allowed_message.id,
                kind="response_style",
                value="verbose",
                confidence=1.0,
                explicit=False,
            ),
        ]
    )
    await db_session.flush()

    context = await build_preference_context(db_session, owner_id=owner.id)

    assert len(context["items"]) == 1
    assert context["items"][0]["kind"] == "response_style"
    assert context["items"][0]["value"] == "concise"
    assert context["memory_ids"] == [context["items"][0]["memory_id"]]
    assert "source原文不得进入上下文" not in str(context)

    await delete_preference_memory(
        db_session, owner_id=owner.id, memory_id=context["memory_ids"][0]
    )
    assert await build_preference_context(db_session, owner_id=owner.id) == {
        "items": [],
        "memory_ids": [],
    }


@pytest.mark.asyncio
async def test_reader_skill_input_contains_auditable_preference_context_only(
    auth_client: AsyncClient,
    db_session,
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=True))
    message = await _persist_message(
        db_session, owner_id=owner.id, body="请解释这一段。"
    )
    memory = UserPreferenceMemory(
        owner_id=owner.id,
        source_message_id=message.id,
        kind="response_style",
        value="concise",
        confidence=1.0,
        explicit=True,
    )
    db_session.add(memory)
    await db_session.flush()

    payload = await build_reader_skill_input(
        db_session,
        SimpleNamespace(user_message_id=message.id, novel_id=message.novel_id),
    )

    assert payload["preference_context"] == {
        "items": [
            {"memory_id": memory.id, "kind": "response_style", "value": "concise"}
        ],
        "memory_ids": [memory.id],
    }
    assert "请解释这一段。" == payload["question"]
    assert "source_message_id" not in str(payload)


@pytest.mark.asyncio
async def test_delete_one_and_all_are_owner_scoped(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=True))
    foreign = User(
        username="foreign-memory-owner",
        email="foreign-memory-owner@example.com",
        hashed_password="x",
    )
    db_session.add(foreign)
    await db_session.flush()
    db_session.add(AgentSettings(owner_id=foreign.id, memory_enabled=True))

    own_one = await _persist_message(
        db_session, owner_id=owner.id, body="以后回答简洁一点。"
    )
    own_two = await _persist_message(
        db_session, owner_id=owner.id, body="以后默认用中文回答。"
    )
    foreign_message = await _persist_message(
        db_session, owner_id=foreign.id, body="以后回答简洁一点。"
    )
    own_memories = await extract_from_persisted_message(db_session, own_one.id)
    await extract_from_persisted_message(db_session, own_two.id)
    foreign_memories = await extract_from_persisted_message(
        db_session, foreign_message.id
    )
    assert len(own_memories) == len(foreign_memories) == 1

    forbidden = await auth_client.delete(
        f"/api/memory/preferences/{foreign_memories[0].id}"
    )
    assert forbidden.status_code == 404

    deleted = await auth_client.delete(f"/api/memory/preferences/{own_memories[0].id}")
    assert deleted.status_code == 204
    assert (await auth_client.get("/api/memory/preferences")).json()["total"] == 1

    deleted_all = await auth_client.delete("/api/memory/preferences")
    assert deleted_all.status_code == 204
    assert (await auth_client.get("/api/memory/preferences")).json() == {
        "items": [],
        "total": 0,
    }

    auth_client.headers["Authorization"] = (
        f"Bearer {create_access_token({'sub': str(foreign.id)})}"
    )
    foreign_list = await auth_client.get("/api/memory/preferences")
    assert foreign_list.status_code == 200
    assert foreign_list.json()["total"] == 1


@pytest.mark.asyncio
async def test_reader_message_http_write_admits_preference_after_persistence(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    db_session.add(AgentSettings(owner_id=owner.id, memory_enabled=True))
    novel = Novel(owner_id=owner.id, title="HTTP preference novel", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="一段用于测试阅读消息上下文的正文。",
        word_count=18,
    )
    db_session.add(chapter)
    await db_session.flush()

    conversation = await auth_client.post(
        f"/api/novels/{novel.id}/conversations", json={"title": "习惯"}
    )
    assert conversation.status_code == 201, conversation.text

    accepted = await auth_client.post(
        f"/api/novels/{novel.id}/conversations/{conversation.json()['id']}/messages",
        json={
            "client_message_id": "preference-http-1",
            "body": "以后回答简洁一点。",
            "chapter_id": chapter.id,
        },
    )
    assert accepted.status_code == 202, accepted.text
    listed = await auth_client.get("/api/memory/preferences")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
