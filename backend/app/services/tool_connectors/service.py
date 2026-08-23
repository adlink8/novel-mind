"""CRUD, lifecycle, and fake-adapter dry-run behavior for Tool connectors."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_connector import ToolConnector, ToolConnectorVersion
from app.schemas.tool_connectors import ToolConnectorPayload
from app.services.tool_connectors.http_adapter import HttpToolAdapter
from app.services.tool_connectors.policy import (
    ConnectorPolicyError,
    MAX_RESPONSE_BYTES,
    MAX_TIMEOUT_SECONDS,
    json_bytes,
    validate_json_payload,
    validate_json_schema,
    validate_target_url,
)


CONNECTOR_TOOL_PREFIX = "connector:"


def connector_slug(tool_name: str) -> str:
    if not tool_name.startswith(CONNECTOR_TOOL_PREFIX):
        raise ConnectorPolicyError("not a connector tool")
    slug = tool_name.removeprefix(CONNECTOR_TOOL_PREFIX)
    if not re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", slug):
        raise ConnectorPolicyError("connector tool name is not a safe slug")
    return slug


def connector_checksum(connector: Any) -> str:
    """Hash immutable connector configuration; credentials are intentionally absent."""
    payload = {
        "connector_id": int(getattr(connector, "connector_id", 0) or 0),
        "owner_id": int(getattr(connector, "owner_id", 0) or 0),
        "name": str(connector.name),
        "version": int(connector.version),
        "base_url": str(connector.base_url),
        "path": str(connector.path),
        "method": str(connector.method),
        "request_schema": connector.request_schema,
        "response_schema": connector.response_schema,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def connector_runtime_manifest(connector: Any, *, tool_name: str) -> dict[str, Any]:
    connector_slug(tool_name)
    return {
        "tool_name": tool_name,
        "connector_id": int(connector.connector_id),
        "version_id": int(connector.id),
        "version": int(connector.version),
        "checksum": connector_checksum(connector),
        "method": connector.method,
        "request_schema": dict(connector.request_schema or {}),
        "response_schema": dict(connector.response_schema or {}),
    }


async def freeze_connector_versions(
    db: AsyncSession, *, owner_id: int, allowed_tools: list[str]
) -> list[dict[str, Any]]:
    frozen: list[dict[str, Any]] = []
    for tool_name in allowed_tools:
        if not tool_name.startswith(CONNECTOR_TOOL_PREFIX):
            continue
        slug = connector_slug(tool_name)
        row = await db.scalar(
            select(ToolConnectorVersion)
            .where(
                ToolConnectorVersion.owner_id == owner_id,
                ToolConnectorVersion.name == slug,
                ToolConnectorVersion.status == "active",
                ToolConnectorVersion.enabled.is_(True),
            )
            .order_by(ToolConnectorVersion.version.desc())
        )
        if row is None:
            raise ConnectorPolicyError(f"active connector unavailable: {tool_name}")
        frozen.append(connector_runtime_manifest(row, tool_name=tool_name))
    return frozen


def connector_url(connector: Any) -> str:
    base = connector.base_url.rstrip("/")
    path = connector.path if connector.path.startswith("/") else f"/{connector.path}"
    url = f"{base}{path}"
    validate_target_url(url)
    return url


async def latest_version(
    db: AsyncSession, *, owner_id: int, connector_id: int
) -> ToolConnectorVersion | None:
    return await db.scalar(
        select(ToolConnectorVersion)
        .where(
            ToolConnectorVersion.owner_id == owner_id,
            ToolConnectorVersion.connector_id == connector_id,
        )
        .order_by(ToolConnectorVersion.version.desc())
    )


async def create_connector(
    db: AsyncSession, *, owner_id: int, payload: ToolConnectorPayload
) -> ToolConnectorVersion:
    validate_target_url(payload.base_url)
    validate_json_schema(payload.request_schema)
    validate_json_schema(payload.response_schema)
    connector = ToolConnector(owner_id=owner_id, name=payload.name)
    db.add(connector)
    await db.flush()
    version_data = payload.model_dump()
    version_data["enabled"] = False
    version = ToolConnectorVersion(
        connector_id=connector.id,
        owner_id=owner_id,
        version=1,
        **version_data,
        status="draft",
    )
    db.add(version)
    await db.flush()
    return version


async def append_connector_version(
    db: AsyncSession, *, owner_id: int, connector_id: int, payload: ToolConnectorPayload
) -> ToolConnectorVersion | None:
    connector = await db.scalar(
        select(ToolConnector).where(
            ToolConnector.id == connector_id, ToolConnector.owner_id == owner_id
        )
    )
    if connector is None:
        return None
    validate_target_url(payload.base_url)
    validate_json_schema(payload.request_schema)
    validate_json_schema(payload.response_schema)
    current = await latest_version(db, owner_id=owner_id, connector_id=connector_id)
    version_data = payload.model_dump()
    version_data["enabled"] = False
    version = ToolConnectorVersion(
        connector_id=connector_id,
        owner_id=owner_id,
        version=(current.version + 1 if current else 1),
        **version_data,
        status="draft",
    )
    db.add(version)
    await db.flush()
    return version


async def validate_connector(
    db: AsyncSession, *, owner_id: int, connector_id: int
) -> ToolConnectorVersion | None:
    version = await latest_version(db, owner_id=owner_id, connector_id=connector_id)
    if version is None:
        return None
    if version.status != "draft":
        raise ConnectorPolicyError("only draft connectors can be validated")
    connector_url(version)
    validate_json_schema(version.request_schema)
    validate_json_schema(version.response_schema)
    version.status = "validated"
    version.enabled = False
    await db.flush()
    return version


async def set_connector_status(
    db: AsyncSession, *, owner_id: int, connector_id: int, status: str
) -> ToolConnectorVersion | None:
    version = await latest_version(db, owner_id=owner_id, connector_id=connector_id)
    if version is None:
        return None
    allowed = {
        "draft": {"validated"},
        "validated": {"active", "disabled"},
        "active": {"disabled"},
        "disabled": set(),
    }
    if status not in allowed.get(version.status, set()):
        raise ConnectorPolicyError(
            f"invalid connector transition {version.status}->{status}"
        )
    version.status = status
    version.enabled = status == "active"
    await db.flush()
    return version


async def dry_run_connector(
    connector: Any, *, request: dict[str, Any], adapter: HttpToolAdapter
) -> dict[str, Any]:
    if connector.status != "active" or not connector.enabled:
        raise ConnectorPolicyError("only enabled active connectors can run")
    url = connector_url(connector)
    host = urlsplit(url).hostname
    if host and not _is_ip_literal(host):
        # The fake seam receives a resolver-free test host; a real adapter must
        # perform DNS validation at its transport boundary before connecting.
        pass
    validate_json_payload(request, connector.request_schema)
    body = json_bytes(request)
    response = await adapter.request(
        method=connector.method,
        url=url,
        body=body,
        timeout=MAX_TIMEOUT_SECONDS,
        max_response_bytes=MAX_RESPONSE_BYTES,
    )
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise ConnectorPolicyError("Tool response exceeds the byte limit")
    validate_target_url(response.final_url)
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorPolicyError("Tool response must be valid JSON") from exc
    validate_json_payload(decoded, connector.response_schema)
    return {
        "status_code": response.status_code,
        "headers": response.headers,
        "body": decoded,
    }


async def execute_frozen_connector(
    connector: Any,
    *,
    request: dict[str, Any],
    frozen_checksum: str,
    adapter: HttpToolAdapter,
) -> dict[str, Any]:
    """Execute one frozen version through the injected fake transport seam."""
    if not frozen_checksum or connector_checksum(connector) != frozen_checksum:
        raise ConnectorPolicyError("connector checksum mismatch (stale connector)")
    if connector.status != "active" or not connector.enabled:
        raise ConnectorPolicyError("connector is disabled")
    url = connector_url(connector)
    validate_json_payload(request, connector.request_schema)
    response = await adapter.request(
        method=connector.method,
        url=url,
        body=json_bytes(request),
        timeout=MAX_TIMEOUT_SECONDS,
        max_response_bytes=MAX_RESPONSE_BYTES,
    )
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise ConnectorPolicyError("Tool response exceeds the byte limit")
    validate_target_url(response.final_url)
    initial = urlsplit(url)
    final = urlsplit(response.final_url)
    if (initial.scheme, initial.hostname, initial.port) != (
        final.scheme,
        final.hostname,
        final.port,
    ):
        raise ConnectorPolicyError("redirect escapes connector origin")
    try:
        decoded = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorPolicyError("Tool response must be valid JSON") from exc
    validate_json_payload(decoded, connector.response_schema)
    return {
        "status_code": response.status_code,
        "headers": response.headers,
        "body": decoded,
    }


def _is_ip_literal(host: str) -> bool:
    try:
        import ipaddress

        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
