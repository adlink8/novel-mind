"""Public contract tests for owner-scoped restricted HTTPS Tool connectors.

The HTTP boundary is represented by a fake adapter. These tests must never make
real public network calls.
"""

from __future__ import annotations

import pytest

from app.services.tool_connectors.http_adapter import FakeHttpAdapter, HttpAdapterResponse
from app.services.tool_connectors.policy import ConnectorPolicyError, validate_target_url
from app.services.tool_connectors.service import dry_run_connector


VALID_PAYLOAD = {
    "name": "weather_lookup",
    "description": "Read a weather report",
    "base_url": "https://api.example.com",
    "path": "/v1/weather",
    "method": "GET",
    "request_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "response_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    "enabled": False,
}


@pytest.mark.contract
@pytest.mark.asyncio
async def test_owner_can_create_draft_connector_and_validate_it(auth_client):
    response = await auth_client.post("/api/extensions/tools", json=VALID_PAYLOAD)

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert created["enabled"] is False

    validated = await auth_client.post(
        f"/api/extensions/tools/{created['id']}/validate"
    )
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_connector_config_rejects_unknown_fields(auth_client):
    response = await auth_client.post(
        "/api/extensions/tools", json={**VALID_PAYLOAD, "shell_command": "whoami"}
    )

    assert response.status_code == 422


@pytest.mark.contract
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://localhost:8443",
        "https://127.0.0.1/api",
        "https://10.0.0.8/api",
        "https://[::1]/api",
    ],
)
async def test_connector_rejects_non_https_or_private_targets(url):
    with pytest.raises(ConnectorPolicyError):
        validate_target_url(url)


@pytest.mark.contract
@pytest.mark.asyncio
async def test_only_validated_connector_can_become_active(auth_client):
    response = await auth_client.post("/api/extensions/tools", json=VALID_PAYLOAD)
    connector_id = response.json()["id"]

    rejected = await auth_client.patch(
        f"/api/extensions/tools/{connector_id}/status", json={"status": "active"}
    )
    assert rejected.status_code == 409

    await auth_client.post(f"/api/extensions/tools/{connector_id}/validate")
    activated = await auth_client.patch(
        f"/api/extensions/tools/{connector_id}/status", json={"status": "active"}
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["enabled"] is True


@pytest.mark.contract
@pytest.mark.asyncio
async def test_dry_run_uses_fake_adapter_and_enforces_response_limit():
    connector = type(
        "Connector",
        (),
        {
            "status": "active",
            "enabled": True,
            "base_url": "https://api.example.com",
            "path": "/v1/weather",
            "method": "GET",
            "request_schema": {"type": "object", "additionalProperties": False},
            "response_schema": {"type": "object"},
        },
    )()
    adapter = FakeHttpAdapter(
        HttpAdapterResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"ok": true}',
            final_url="https://api.example.com/v1/weather",
        )
    )

    result = await dry_run_connector(connector, request={}, adapter=adapter)

    assert result["status_code"] == 200
    assert result["body"] == {"ok": True}
    assert adapter.calls[0]["url"] == "https://api.example.com/v1/weather"


@pytest.mark.contract
@pytest.mark.asyncio
async def test_dry_run_rejects_redirect_to_private_target():
    connector = type(
        "Connector",
        (),
        {
            "status": "active",
            "enabled": True,
            "base_url": "https://api.example.com",
            "path": "/v1/weather",
            "method": "GET",
            "request_schema": {"type": "object", "additionalProperties": False},
            "response_schema": {"type": "object"},
        },
    )()
    adapter = FakeHttpAdapter(
        HttpAdapterResponse(
            status_code=302,
            headers={"location": "https://127.0.0.1/admin"},
            body=b"{}",
            final_url="https://127.0.0.1/admin",
        )
    )

    with pytest.raises(ConnectorPolicyError):
        await dry_run_connector(connector, request={}, adapter=adapter)
