"""Restricted connector run/proxy contract tests; transport must stay fake."""

from __future__ import annotations

import pytest

from app.services.tool_connectors.http_adapter import FakeHttpAdapter, HttpAdapterResponse
from app.services.tool_connectors.service import connector_checksum, execute_frozen_connector
from app.services.tool_connectors.policy import ConnectorPolicyError


def _connector(**overrides):
    values = {
        "connector_id": 7,
        "owner_id": 1,
        "id": 11,
        "name": "weather_lookup",
        "version": 2,
        "status": "active",
        "enabled": True,
        "base_url": "https://api.example.com",
        "path": "/v1/weather",
        "method": "GET",
        "request_schema": {"type": "object", "additionalProperties": False},
        "response_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    }
    values.update(overrides)
    return type("Connector", (), values)()


@pytest.mark.contract
@pytest.mark.asyncio
async def test_run_proxy_rechecks_frozen_checksum_and_redirect_origin():
    connector = _connector()
    adapter = FakeHttpAdapter(
        HttpAdapterResponse(
            status_code=302,
            headers={"location": "https://evil.example/admin"},
            body=b'{"ok": true}',
            final_url="https://evil.example/admin",
        )
    )

    checksum = connector_checksum(connector)
    with pytest.raises(ConnectorPolicyError, match="redirect"):
        await execute_frozen_connector(
            connector,
            request={},
            frozen_checksum=checksum,
            adapter=adapter,
        )

    with pytest.raises(ConnectorPolicyError, match="checksum"):
        await execute_frozen_connector(
            connector,
            request={},
            frozen_checksum=None,
            adapter=adapter,
        )
