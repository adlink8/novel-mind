"""Owner-visible capability catalog for the built-in agent tools."""

from __future__ import annotations

from typing import Literal

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel

ToolCategory = Literal["read", "candidate", "action"]


class ToolCapabilityView(StrictAgentRuntimeModel):
    name: str
    category: ToolCategory
    approval_required: bool
    user_configurable: bool


class ToolCapabilityCatalogView(StrictAgentRuntimeModel):
    items: list[ToolCapabilityView]
    total: int
    http_tools: Literal["not_enabled"] = "not_enabled"
    execution_boundary: Literal["builtin_declarative_only"] = "builtin_declarative_only"
