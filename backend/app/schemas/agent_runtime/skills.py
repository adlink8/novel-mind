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
    prompt: str = Field(
        default="Follow the declarative Skill contract and use only supplied input and evidence.",
        min_length=1,
        max_length=12000,
    )
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
    prompt: str = ""
    yaml_checksum: str
    allowed_tools: list[str] = Field(default_factory=list)
    read_permissions: list[str] = Field(default_factory=list)
    write_permissions: list[str] = Field(default_factory=list)
    forbidden_spaces: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    approval_required_for: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    execution_mode: Literal["builtin", "declarative_only"] = "declarative_only"
    status: Literal["draft", "active", "deprecated"]
    created_at: datetime
    execution_status: Literal["declarative_only"] = "declarative_only"
    runtime_note: str = "已注册声明式 Skill；当前运行时不会执行 prompt 正文。"


class ConnectorRuntimeManifest(StrictAgentRuntimeModel):
    """Run-frozen connector contract; it carries no credentials or URL authority."""

    tool_name: str = Field(pattern=r"^connector:[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    connector_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    version: int = Field(gt=0)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    method: Literal["GET", "POST"]
    request_schema: dict[str, Any] = Field(default_factory=dict)
    response_schema: dict[str, Any] = Field(default_factory=dict)


class SkillRuntimeManifest(StrictAgentRuntimeModel):
    """run 接受时由服务端冻结的 canonical Skill 运行时清单。"""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    skill_version_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=32)
    description: str = ""
    prompt: str = Field(min_length=1, max_length=12000)
    execution_mode: Literal["builtin", "declarative_only"] = "declarative_only"
    allowed_tools: list[str] = Field(min_length=1)
    read_permissions: list[str] = Field(default_factory=list)
    write_permissions: list[str] = Field(default_factory=list)
    forbidden_spaces: list[str] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    approval_required_for: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    connector_versions: list[ConnectorRuntimeManifest] = Field(default_factory=list)


class SkillVersionStatusUpdate(StrictAgentRuntimeModel):
    status: Literal["draft", "active", "deprecated"]
