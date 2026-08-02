"""
ApprovalRequest 严格 wire 模型（25.3-04 / D-11 / D-15 / REQ-AGENT-07）。

约定（与 schemas/agent_runtime.py 同款）:
  - 继承 StrictAgentRuntimeModel（extra="forbid"），未知字段一律拒绝（fail closed）。
  - 本模块与 schemas/agent_runtime.py 分离：后者由同波 25.3-03 独占（ExternalEvidenceArtifact）。

D-15 绑定契约: 每个 ApprovalRequest 绑定 run_id / skill_version_id /
artifact_id / artifact_revision_id / owner_id / novel_id / branch_id / fork_id /
action / payload_hash / expiry 与决策 actor/time（decision_actor_id / decided_at）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.agent_runtime import StrictAgentRuntimeModel

# 审批状态机（与 models.agent_runtime.APPROVAL_REQUEST_STATUSES 镜像；单一事实源在模型）。
ApprovalStatus = Literal[
    "pending",
    "approved",
    "approved_for_session",
    "rejected",
    "expired",
    "cancelled",
]


class ApprovalRequestCreate(StrictAgentRuntimeModel):
    """agent-service 铸造审批请求的 payload（POST /api/agent/approval-requests）。

    owner_id 为可选的 D-15 绑定记录；服务端以**当前认证用户**为权威 owner，
    若显式提供且与认证用户不符则拒绝（审批伪造防御，T-25.3-04-02）。
    """

    run_id: int = Field(gt=0)
    skill_version_id: int | None = Field(default=None, gt=0)
    artifact_id: int | None = Field(default=None, gt=0)
    artifact_revision_id: int | None = Field(default=None, gt=0)
    owner_id: int | None = Field(default=None, gt=0)
    novel_id: int | None = Field(default=None, gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    fork_id: int | None = Field(default=None, gt=0)
    action: str = Field(min_length=1, max_length=120)
    # 规范化载荷摘要（SSE 帧携带给浏览器渲染；绝不承载原始工具 I/O）。
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    # D-15 重放追溯哈希（String(64)，可空）。
    payload_hash: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]{0,64}$")
    expires_at: datetime | None = None


class ApprovalRequestView(StrictAgentRuntimeModel):
    """审批请求行（浏览器的渲染与短轮询读取形状）。"""

    id: int
    owner_id: int
    run_id: int | None = None
    action: str
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    status: ApprovalStatus
    created_at: datetime
    decided_at: datetime | None = None
    expires_at: datetime | None = None


class ApprovalDecision(StrictAgentRuntimeModel):
    """confirm 请求体：once = 仅本次；session = 本 run 内后续同动作免批（D-11）。"""

    mode: Literal["once", "session"] = "once"
