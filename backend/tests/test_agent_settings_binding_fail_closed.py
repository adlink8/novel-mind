"""Fail-closed public API tests for Agent model bindings."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import hash_password
from app.models import AIModelConfig, User

pytestmark = pytest.mark.unit


def _payload(model_id: int) -> dict:
    return {
        "auto_deep_analysis": False,
        "memory_enabled": True,
        "memory_retention_days": None,
        "show_analysis_progress": True,
        "notify_analysis_complete": True,
        "auto_create_candidate_artifacts": False,
        "task_model_bindings": {
            "qa": model_id,
            "deep_analysis": None,
            "continuation": None,
            "illustration": None,
            "rag_eval": None,
            "embedding": None,
        },
    }


@pytest.mark.asyncio
async def test_put_agent_settings_rejects_missing_model_without_mutation(
    auth_client: AsyncClient,
):
    response = await auth_client.put("/api/settings/agent", json=_payload(999999))

    assert response.status_code == 422
    reread = await auth_client.get("/api/settings/agent")
    assert reread.status_code == 200
    assert reread.json()["task_model_bindings"]["qa"] is None


@pytest.mark.asyncio
async def test_put_agent_settings_rejects_foreign_model_without_mutation(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    foreign_owner = User(
        username="foreign-agent-owner",
        email="foreign-agent-owner@example.com",
        hashed_password=hash_password("testpass123"),
    )
    db_session.add(foreign_owner)
    await db_session.flush()
    foreign_model = AIModelConfig(
        owner_id=foreign_owner.id,
        name="foreign-agent-model",
        provider="custom",
        model_id="foreign-model",
    )
    db_session.add(foreign_model)
    await db_session.flush()

    response = await auth_client.put(
        "/api/settings/agent", json=_payload(foreign_model.id)
    )

    assert response.status_code == 422
    assert owner.id != foreign_owner.id
    reread = await auth_client.get("/api/settings/agent")
    assert reread.json()["task_model_bindings"]["qa"] is None
