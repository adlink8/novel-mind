"""Agent Runtime 严格 wire 模型基础：StrictAgentRuntimeModel 基类与
统一信封基建（Cited Answer D-10 / External Evidence D-09）。

约定:
  - 所有模型继承 StrictAgentRuntimeModel（extra="forbid"），未知字段一律拒绝
    （fail closed，防止被提示注入的 agent 悄悄塞入未声明字段）。
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


class NormalizationTrail(StrictAgentRuntimeModel):
    """结构化输出修复血缘（26-06 / REQ-AGENT-08 / D-16）。

    repaired_hash 是对**不含本 trail 的** repaired payload 的 canonical SHA-256
    （与 agent-service normalizer 的 canonicalHash 口径一致）；raw_hash 是原始模型
    输出的 canonical SHA-256（raw 本体保留在 agent-service 侧作为 immutable audit
    evidence）。normalization_actions 每项至少含 path/action/after。
    """

    raw_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    repaired_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("normalization_actions")
    @classmethod
    def _actions_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("each normalization action must be an object")
            if not item.get("path") or not item.get("action"):
                raise ValueError("each normalization action requires path and action")
            if "after" not in item:
                raise ValueError("each normalization action requires after")
        return value


class CitedAnswerArtifact(StrictAgentRuntimeModel):
    """智能体产物的 Cited Answer 信封（D-10/D-14）。

    finalize 写入 artifact_revisions.content 的完整载荷；所有证据引用必须属于
    run 冻结 manifest 白名单（validate_answer_against_manifest 服务端校验）。
    normalization 是必须的修复血缘 trail：normalizer 的输出进入审计/Artifact
    lineage，服务端 adapter 在任何写入前重放校验（26-06 / REQ-AGENT-08）。
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
    normalization: NormalizationTrail


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
