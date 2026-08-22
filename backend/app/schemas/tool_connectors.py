"""Strict public contract for desktop-configured restricted HTTPS Tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel


class ToolConnectorPayload(StrictAgentRuntimeModel):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    description: str | None = Field(default=None, max_length=4000)
    base_url: str = Field(min_length=1, max_length=2048)
    path: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "POST"]
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]
    enabled: bool = False

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        if not value.startswith("/") or "://" in value or "\\" in value:
            raise ValueError("path must be a relative URL path")
        return value


class ToolConnectorUpdate(ToolConnectorPayload):
    pass


class ToolConnectorStatusUpdate(StrictAgentRuntimeModel):
    status: Literal["active", "disabled"]


class ToolConnectorView(ToolConnectorPayload):
    id: int
    version_id: int
    connector_id: int
    version: int
    owner_id: int
    status: Literal["draft", "validated", "active", "disabled"]
    created_at: datetime


class ToolConnectorListResponse(StrictAgentRuntimeModel):
    items: list[ToolConnectorView]
    total: int


class ToolDryRunRequest(StrictAgentRuntimeModel):
    request: dict[str, Any] = Field(default_factory=dict)


class ToolDryRunResponse(StrictAgentRuntimeModel):
    status_code: int
    headers: dict[str, str]
    body: Any

