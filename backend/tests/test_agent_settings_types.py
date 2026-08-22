"""Typed input boundary tests for owner Agent settings."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_put_agent_settings_rejects_coerced_boolean(auth_client: AsyncClient):
    response = await auth_client.put(
        "/api/settings/agent",
        json={
            "auto_deep_analysis": "true",
            "memory_enabled": True,
            "memory_retention_days": None,
            "show_analysis_progress": True,
            "notify_analysis_complete": True,
            "auto_create_candidate_artifacts": False,
            "task_model_bindings": {
                "qa": None,
                "deep_analysis": None,
                "continuation": None,
                "illustration": None,
                "rag_eval": None,
                "embedding": None,
            },
        },
    )

    assert response.status_code == 422
