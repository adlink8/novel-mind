"""
Skill Runtime 与 Artifact Contract 的严格 wire 模型（25.2-03 / D-09..D-14）。

约定:
  - 所有模型继承 StrictAgentRuntimeModel（extra="forbid"），未知字段一律拒绝
    （fail closed，防止被提示注入的 agent 悄悄塞入未声明字段）。
  - SkillRunCreate / 技能注册 payload 校验 D-09 最小契约字段。
  - CitedAnswerArtifact 信封镜像 D-10 字段：type / schema_version / owner /
    novel / branch / producing skill+version / model lineage / source versions /
    input_hash / evidence_refs / status / parent_revision。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.reader_chat import StrictReaderChatModel


class StrictAgentRuntimeModel(BaseModel):
    """严格 wire 模型：未知字段拒绝 + 支持 ORM 对象直接验证（from_attributes）。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


# ────────────────────────── 技能注册 / 版本 ──────────────────────────


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


# ────────────────────────── 技能运行 ──────────────────────────


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


# ────────────────────────── 产物与修订 ──────────────────────────


class ArtifactView(StrictAgentRuntimeModel):
    """产物行（D-10 血缘字段）。"""

    id: int
    owner_id: int
    novel_id: int
    skill_version_id: int
    run_id: int
    branch: str | None = None
    type: str
    schema_version: str
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    source_versions: dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    current_revision_id: int | None = None
    created_at: datetime
    updated_at: datetime


class ArtifactRevisionView(StrictAgentRuntimeModel):
    """不可变产物修订行。"""

    id: int
    artifact_id: int
    owner_id: int
    novel_id: int
    revision_no: int
    content_hash: str
    parent_revision_id: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    content: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ────────────────────────── Cited Answer Artifact 信封（D-10） ──────────────────────────


class CitedAnswerArtifact(StrictAgentRuntimeModel):
    """智能体产物的 Cited Answer 信封（D-10/D-14）。

    finalize 写入 artifact_revisions.content 的完整载荷；所有证据引用必须属于
    run 冻结 manifest 白名单（validate_answer_against_manifest 服务端校验）。
    """

    type: Literal["cited_answer"] = "cited_answer"
    schema_version: Literal["cited-answer.v1"] = "cited-answer.v1"
    owner_id: int
    novel_id: int
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    answer: dict[str, Any]
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None


# ────────────────────────── External Evidence Artifact 信封（D-09） ──────────────────────────


class ExternalEvidenceSource(StrictAgentRuntimeModel):
    """外部（MCP）来源条目：retrieved_from 恒为 "mcp"（D-09）。"""

    server: str = Field(min_length=1, max_length=120)
    tool: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=512)
    retrieved_from: Literal["mcp"] = "mcp"


class ExternalEvidenceClaim(StrictAgentRuntimeModel):
    """外部主张条目：text + 在来源结果中的下标（D-09）。"""

    text: str = Field(min_length=1, max_length=4000)
    source_index: int = Field(ge=0)


class ExternalEvidenceArtifact(StrictAgentRuntimeModel):
    """外部（MCP）结果的 D-09 信封——仅以 external_evidence 类型物化。

    prohibited_from_canon 是**服务端常量** Literal[True]：wire 形状本身无法断言
    其他值（T-25.3-03-02，Pitfall 6）。与 25.2-03 CitedAnswerArtifact 信封纪律
    一致（type / schema_version / lineage 字段），但携带 D-09 字段，且永不进入
    CitedAnswerArtifact 的 evidence_refs——finalizer 的
    validate_answer_against_manifest 只认识 original_text_evidence 引用。
    """

    type: Literal["external_evidence"] = "external_evidence"
    schema_version: Literal[1] = 1
    sources: list[ExternalEvidenceSource] = Field(min_length=1)
    retrieval_time: datetime  # ISO-8601
    claims: list[ExternalEvidenceClaim] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "low"
    prohibited_from_canon: Literal[True] = True
    release_status: Literal["external"] = "external"


# 供 OpenAPI 引用，避免未使用告警。
_ = (StrictReaderChatModel,)
