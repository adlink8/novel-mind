"""PUT slice for the owner-scoped Agent settings API."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import AIModelConfig, User

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_put_agent_settings_persists_typed_config(
    auth_client: AsyncClient, db_session
):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    model = AIModelConfig(
        owner_id=owner.id,
        name="owner-agent-model",
        provider="custom",
        model_id="owner-model",
    )
    db_session.add(model)
    await db_session.flush()

    payload = {
        "auto_deep_analysis": True,
        "memory_enabled": False,
        "memory_retention_days": 90,
        "show_analysis_progress": False,
        "notify_analysis_complete": True,
        "auto_create_candidate_artifacts": True,
        "task_model_bindings": {
            "qa": model.id,
            "deep_analysis": model.id,
            "continuation": model.id,
            "illustration": model.id,
            "rag_eval": model.id,
            "embedding": model.id,
        },
    }

    response = await auth_client.put("/api/settings/agent", json=payload)

    assert response.status_code == 200
    assert response.json() == payload
    reread = await auth_client.get("/api/settings/agent")
    assert reread.status_code == 200
    assert reread.json() == payload
