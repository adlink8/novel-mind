"""SkillRun 族 wire 模型（25.2-02 handoff / 25.2-05 finalize / 意图路由）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel
from app.schemas.agent_runtime.skills import SkillRuntimeManifest


class SkillRunCreate(StrictAgentRuntimeModel):
    """接受一次技能运行的请求：技能版本 + 输入（含固定问题）。"""

    skill_version_id: int = Field(gt=0)
    input: dict[str, Any]
    branch: str | None = Field(default=None, max_length=80)


class ToolRunSummary(StrictAgentRuntimeModel):
    """SkillRun 对外公开的 Pi runtime 工具调用摘要。

    只允许确定性计数，不承载 args、结果正文或错误正文；``errors`` 是
    ``calls`` 的子集。实际事实仍由 agent-service 从 Pi toolResult 生成，
    此模型只负责公开投影时的严格形状校验。
    """

    tool_name: str = Field(min_length=1, max_length=160)
    calls: int = Field(ge=1)
    errors: int = Field(ge=0)

    @model_validator(mode="after")
    def _errors_cannot_exceed_calls(self) -> "ToolRunSummary":
        if self.errors > self.calls:
            raise ValueError("errors cannot exceed calls")
        return self


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
    # Kept as an ORM validation source only.  Public responses expose the
    # redacted deterministic ``tool_runs`` projection, never the full frozen
    # manifest (connector metadata, evidence allowlists, batch internals).
    frozen_manifest: dict[str, Any] = Field(default_factory=dict, exclude=True)
    tool_runs: list[ToolRunSummary] = Field(default_factory=list)
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    source_versions: dict[str, Any] = Field(default_factory=dict)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    cancel_requested: bool
    retry_count: int
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _project_tool_runs_from_frozen_manifest(self) -> "SkillRunView":
        """只从 ORM 的冻结 manifest 公开 tool_runs，拒绝独立可伪造投影。"""

        raw_tool_runs = self.frozen_manifest.get("tool_runs")
        if raw_tool_runs is None:
            return self
        if not isinstance(raw_tool_runs, list):
            raise ValueError("frozen_manifest.tool_runs must be a list")
        self.tool_runs = [ToolRunSummary.model_validate(item) for item in raw_tool_runs]
        names = [item.tool_name for item in self.tool_runs]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("tool_runs must be sorted and unique by tool_name")
        return self


class SkillRunAccepted(StrictAgentRuntimeModel):
    """202 响应：run + 一次性 per-run 内部令牌（25.2-02 handoff）。"""

    run: SkillRunView
    internal_token: str
    runtime_manifest: SkillRuntimeManifest


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
