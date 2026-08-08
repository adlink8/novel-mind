"""SkillRun 族 wire 模型（25.2-02 handoff / 25.2-05 finalize / 意图路由）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel


class SkillRunCreate(StrictAgentRuntimeModel):
    """接受一次技能运行的请求：技能版本 + 输入（含固定问题）。"""

    skill_version_id: int = Field(gt=0)
    input: dict[str, Any]
    branch: str | None = Field(default=None, max_length=80)


class SkillRunView(StrictAgentRuntimeModel):
    """技能运行行（不含内部令牌明文）。"""

    id: int
    owner_id: int
    novel_id: int
    skill_version_id: int
    status: Literal["queued", "running", "cancelled", "completed", "failed"]
    status_reason: str | None = None
    stop_reason: str | None = None
    branch: str | None = None
    input_hash: str
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    source_versions: dict[str, Any] = Field(default_factory=dict)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    cancel_requested: bool
    retry_count: int
    created_at: datetime
    updated_at: datetime


class SkillRunAccepted(StrictAgentRuntimeModel):
    """202 响应：run + 一次性 per-run 内部令牌（25.2-02 handoff）。"""

    run: SkillRunView
    internal_token: str


class SkillRunFinalize(StrictAgentRuntimeModel):
    """agent-service 在 agent_end 时触发的确定性 finalize 请求（25.2-05）。"""

    stop_reason: Literal[
        "stop", "aborted", "cancelled", "error", "max_tokens", "other"
    ] = "stop"
    envelope: dict[str, Any] = Field(default_factory=dict)
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    source_versions: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    frozen_manifest: dict[str, Any] | None = None


class RouteSkillRequest(StrictAgentRuntimeModel):
    """意图→skill 自动路由请求（AGENT-RUNTIME-CONTRACT：Agent 选 skill）。

    ``question`` 必填（非空）；``source_status`` 可选，携带查询维度可用性，
    供显式意图未命中时回退到不足维度（与 chat_backfill 同语义）。
    """

    question: str = Field(min_length=1, max_length=1000)
    source_status: dict[str, str] | None = Field(default=None)
