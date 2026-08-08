"""技能注册 / 目录 / 版本 wire 模型（D-09 最小契约字段）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel


class SkillVersionRegister(StrictAgentRuntimeModel):
    """skill.yaml 契约的 D-09 最小字段集（后端注册入口）。"""

    novel_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=32, pattern=r"^\d+\.\d+\.\d+$")
    description: str | None = Field(default=None, max_length=4000)
    allowed_tools: list[str] = Field(min_length=0)
    read_permissions: list[str] = Field(default_factory=list)
    write_permissions: list[str] = Field(default_factory=list)
    forbidden_spaces: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    approval_required_for: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "allowed_tools", "read_permissions", "write_permissions", "forbidden_spaces"
    )
    @classmethod
    def _list_of_nonempty(cls, value: list[str]) -> list[str]:
        for item in value:
            if not item or not str(item).strip():
                raise ValueError("list entries must be non-empty strings")
        return value


class SkillRegistryView(StrictAgentRuntimeModel):
    """技能目录行（元数据，不含 schema 全文）。"""

    id: int
    owner_id: int
    novel_id: int
    name: str
    description: str | None = None
    status: Literal["draft", "active", "deprecated"]
    created_at: datetime
    updated_at: datetime


class SkillVersionView(StrictAgentRuntimeModel):
    """技能版本行（含 D-09 契约全文，供 agent-service 读取）。"""

    id: int
    registry_id: int
    owner_id: int
    novel_id: int
    name: str
    version: str
    description: str | None = None
    yaml_checksum: str
    allowed_tools: list[str] = Field(default_factory=list)
    read_permissions: list[str] = Field(default_factory=list)
    write_permissions: list[str] = Field(default_factory=list)
    forbidden_spaces: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    approval_required_for: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    status: Literal["draft", "active", "deprecated"]
    created_at: datetime
