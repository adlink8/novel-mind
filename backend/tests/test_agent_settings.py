"""Owner-scoped Agent settings public API contract."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_get_agent_settings_returns_typed_owner_defaults(
    auth_client: AsyncClient,
):
    response = await auth_client.get("/api/settings/agent")

    assert response.status_code == 200
    assert response.json() == {
        "auto_deep_analysis": False,
        "memory_enabled": False,
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
    }


@pytest.mark.asyncio
async def test_put_agent_settings_persists_typed_owner_config(
    auth_client: AsyncClient,
):
    payload = {
        "auto_deep_analysis": True,
        "memory_enabled": False,
        "memory_retention_days": 30,
        "show_analysis_progress": False,
        "notify_analysis_complete": False,
        "auto_create_candidate_artifacts": True,
        "task_model_bindings": {
            "qa": None,
            "deep_analysis": None,
            "continuation": None,
            "illustration": None,
            "rag_eval": None,
            "embedding": None,
        },
    }

    response = await auth_client.put("/api/settings/agent", json=payload)

    assert response.status_code == 200
    assert response.json() == payload
    assert (await auth_client.get("/api/settings/agent")).json() == payload


@pytest.mark.asyncio
async def test_put_agent_settings_binds_existing_owner_model(
    auth_client: AsyncClient,
):
    model_response = await auth_client.post(
        "/api/models",
        json={
            "name": "Agent QA model",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
        },
    )
    assert model_response.status_code == 200
    model_id = model_response.json()["id"]

    payload = {
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

    response = await auth_client.put("/api/settings/agent", json=payload)

    assert response.status_code == 200
    assert response.json() == payload
    assert (await auth_client.get("/api/settings/agent")).json() == payload


@pytest.mark.asyncio
async def test_put_agent_settings_rejects_missing_model_binding(
    auth_client: AsyncClient,
):
    payload = {
        "auto_deep_analysis": False,
        "memory_enabled": True,
        "memory_retention_days": None,
        "show_analysis_progress": True,
        "notify_analysis_complete": True,
        "auto_create_candidate_artifacts": False,
        "task_model_bindings": {
            "qa": 999999,
            "deep_analysis": None,
            "continuation": None,
            "illustration": None,
            "rag_eval": None,
            "embedding": None,
        },
    }

    response = await auth_client.put("/api/settings/agent", json=payload)

    assert response.status_code == 422
    assert (await auth_client.get("/api/settings/agent")).json()[
        "task_model_bindings"
    ] == {
        "qa": None,
        "deep_analysis": None,
        "continuation": None,
        "illustration": None,
        "rag_eval": None,
        "embedding": None,
    }


@pytest.mark.asyncio
async def test_put_agent_settings_rejects_foreign_owner_model(
    auth_client: AsyncClient,
):
    owner_token = auth_client.headers["Authorization"]
    await auth_client.post(
        "/api/auth/register",
        json={
            "username": "other-agent-owner",
            "email": "other-agent-owner@example.com",
            "password": "testpass123",
        },
    )
    login = await auth_client.post(
        "/api/auth/login",
        json={"username": "other-agent-owner", "password": "testpass123"},
    )
    assert login.status_code == 200
    auth_client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    foreign_model = await auth_client.post(
        "/api/models",
        json={
            "name": "Foreign Agent model",
            "provider": "openai",
            "model_id": "gpt-4o-mini",
        },
    )
    assert foreign_model.status_code == 200

    auth_client.headers["Authorization"] = owner_token
    payload = {
        "auto_deep_analysis": False,
        "memory_enabled": True,
        "memory_retention_days": None,
        "show_analysis_progress": True,
        "notify_analysis_complete": True,
        "auto_create_candidate_artifacts": False,
        "task_model_bindings": {
            "qa": foreign_model.json()["id"],
            "deep_analysis": None,
            "continuation": None,
            "illustration": None,
            "rag_eval": None,
            "embedding": None,
        },
    }

    response = await auth_client.put("/api/settings/agent", json=payload)

    assert response.status_code == 422
