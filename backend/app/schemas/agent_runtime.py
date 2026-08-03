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


# ────────────────────────── World Model Candidate Artifact 信封（Phase 27） ──────────────────────────


class WorldModelCandidateArtifact(StrictAgentRuntimeModel):
    """智能体产物的 World Model Candidate 信封（Phase 27 / D-01..D-06）。

    Agent 产出的是**候选投影**（typed world-model projections），不是事实；只有
    确定性 WorldModel Validator/Gate 能把候选转换为 validated/published 投影。
    所有 evidence_refs 必须属于 run 冻结 manifest 白名单（finalize 服务端校验，
    D-07/D-08）。``candidates`` 是 agent 提案的类型化候选（events/edges/
    knowledge/rules/exceptions/entities 等 claims），其 authority label 保持
    原样——绝不静默升级为 ``canon_fact``（D-01）；具体 claim 的合法性由
    WorldModelGate 在发布时逐条裁决。与 CitedAnswerArtifact 信封纪律一致：
    type / schema_version / owner / novel / branch / producing skill+version /
    model lineage / source versions / input_hash / evidence_refs / status /
    parent_revision / normalization（26-06 修复血缘 trail）。
    """

    type: Literal["world_model_candidate"] = "world_model_candidate"
    schema_version: Literal["world-model-candidate.v1"] = "world-model-candidate.v1"
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
    candidates: dict[str, Any]
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail


# ────────────────────────── Phase 28 Narrative Memory Artifact 信封（D-08/D-09） ──────────────────────────


class ChapterAnalysisArtifact(StrictAgentRuntimeModel):
    """智能体产物的 ChapterAnalysisArtifact 信封（Phase 28 / D-08）。

    Agent 产出的是**candidate-only 的章节上下文/连续性分析**，不是事实；确定性
    terminal-state/evidence 校验与 finalizer 拥有 permission/evidence/
    state-transition/publication 权威。``analysis`` 携带受 bounds 约束的候选
    上下文：``chapter_digest`` / ``chunk_digests`` 是 namespaced 压缩负载
    （绝不作为 EvidenceRef 或检索索引输入）；``previous_context_summary`` /
    ``next_context_hint`` / ``continuity_notes`` 受 ``max_length`` /
    ``cutoff`` / ``spoiler_policy_version`` 约束；``next_context_hint`` 仅消歧、
    绝不泄漏未来事实（越界 → 置 null 并以 ``next_hint_reason_code`` 记录稳定
    阻断原因）。``tool_runs`` 携带 ToolRun 血缘。与 CitedAnswerArtifact 信封
    纪律一致：type / schema_version / owner / novel / branch / producing
    skill+version / model lineage / source versions / input_hash /
    evidence_refs / status / parent_revision / normalization（26-06 trail）。
    """

    type: Literal["chapter_analysis"] = "chapter_analysis"
    schema_version: Literal["chapter-analysis.v1"] = "chapter-analysis.v1"
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
    analysis: dict[str, Any]
    tool_runs: list[dict[str, Any]] = Field(min_length=1)
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail


class StoryArcArtifact(StrictAgentRuntimeModel):
    """智能体产物的 StoryArcArtifact 信封（Phase 28 / D-05/D-07/D-09）。

    Agent 产出的是**candidate-only 的故事弧候选**：``outline_candidate``
    （OutlineCandidateArtifact）与 ``mainline_candidate``
    （MainlineCandidateArtifact）保留 source snapshot/range、input hashes、
    证据血缘、边界不确定性与 ``candidate_status="candidate"``，**绝不进入
    Canon**（D-09）；确定性 terminal-state/coverage validators 拥有
    permission/evidence/state-transition/publication 权威。``tool_runs``
    携带 ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致（lineage /
    evidence_refs / status / parent_revision / normalization）。
    """

    type: Literal["story_arc"] = "story_arc"
    schema_version: Literal["story-arc.v1"] = "story-arc.v1"
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
    outline_candidate: dict[str, Any]
    mainline_candidate: dict[str, Any]
    tool_runs: list[dict[str, Any]] = Field(min_length=1)
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail


# ────────────────────────── Phase 29 Skill Evaluation Artifact 信封（REQ-QA-01..03 / REQ-AGENT-03） ──────────────────────────


class EvaluatedSkillRunLineage(StrictAgentRuntimeModel):
    """被评估冻结 SkillRun + ToolRun 血缘（Phase 29 / D-02/D-05）。

    只允许 completed/failed 冻结终态：running 等可变 Agent 状态绝不作为评估
    证据；评估冻结 Skill/model/source/Artifact/dataset 版本，绝不重跑可变
    Agent 会话状态。
    """

    run_id: int = Field(gt=0)
    status: Literal["completed", "failed"]
    branch: str | None = None
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_runs: list[dict[str, Any]] = Field(min_length=1)

    @field_validator("tool_runs")
    @classmethod
    def _tool_runs_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict) or not item.get("tool_name"):
                raise ValueError("each tool run requires tool_name")
        return value


class EvaluatedArtifactLineage(StrictAgentRuntimeModel):
    """被评估冻结 Artifact 修订血缘（Phase 29 / D-02/D-05）。"""

    artifact_id: int = Field(gt=0)
    revision_id: int = Field(gt=0)
    type: str = Field(min_length=1, max_length=40)
    schema_version: str = Field(min_length=1, max_length=32)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["candidate", "validated", "approved", "published", "rejected"]


class SkillEvaluationArtifact(StrictAgentRuntimeModel):
    """智能体产物的 SkillEvaluationArtifact 信封（Phase 29 / REQ-QA-01..03）。

    本信封是 Phase 29 确定性评估能力的 Agent 消费边界：``evaluated_run`` /
    ``evaluated_artifact`` 绑定被评估的冻结 SkillRun / ToolRun / Artifact 血缘，
    ``report`` 是密封 QualificationReport（verdict 只允许 qualified_candidate /
    blocked；checksum 可重放——后端确定性评估 runner 产出，不可由 Agent/UI
    更改）。**无 ApprovalRequest、无 Publisher、无 promotion**；verdict 权威只
    属于确定性评估 runner（immutable evaluation runner and milestone audit）。
    与 CitedAnswerArtifact 信封纪律一致：type / schema_version / owner / novel /
    branch / producing skill+version / model lineage / source versions /
    input_hash / evidence_refs / status / parent_revision / normalization
    （26-06 修复血缘 trail）。
    """

    type: Literal["skill_evaluation"] = "skill_evaluation"
    schema_version: Literal["skill-evaluation.v1"] = "skill-evaluation.v1"
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
    evaluated_run: EvaluatedSkillRunLineage
    evaluated_artifact: EvaluatedArtifactLineage
    # 密封 QualificationReport payload（完整 dump；domain 校验由 integrity gate
    # 对 qualification.report.QualificationReport 重放 checksum 完成）。
    report: dict[str, Any]
    tool_runs: list[dict[str, Any]] = Field(min_length=1)
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail

    @field_validator("tool_runs")
    @classmethod
    def _tool_runs_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict) or not item.get("tool_name"):
                raise ValueError("each tool run requires tool_name")
        return value


# ────────────────────────── Phase 30 Visual Bible Artifact 信封（REQ-VIS-01 / REQ-AGENT-02/03/04） ──────────────────────────


class VisualBibleArtifact(StrictAgentRuntimeModel):
    """智能体产物的 VisualBibleArtifact 信封（Phase 30 / D-30-01..D-30-04）。

    Agent 产出的是 **candidate-only** 的 Visual Bible 版本契约：``visual_bible``
    携带完整 ``VisualBibleVersionContract``（entities / claims / evidence refs /
    reference assets + 全量 lineage hash），其 ``review_state`` 恒为
    ``candidate``——approval 是显式、append-only 的服务端状态迁移
    （``visual_bible:approve`` 用户批准），Agent 绝不能直接授予或伪造批准。
    确定性 evidence/rights/authority-label validator 拥有 permission / evidence /
    state-transition / publication 权威（D-30-01/D-30-04）。``tool_runs`` 携带
    ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version /
    owner / novel / branch / producing skill+version / model lineage /
    source versions / input_hash / evidence_refs / status / parent_revision /
    normalization（26-06 修复血缘 trail）。
    """

    type: Literal["visual_bible"] = "visual_bible"
    schema_version: Literal["visual-bible.v1"] = "visual-bible.v1"
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
    visual_bible: dict[str, Any]
    tool_runs: list[dict[str, Any]] = Field(min_length=1)
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail

    @field_validator("tool_runs")
    @classmethod
    def _tool_runs_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict) or not item.get("tool_name"):
                raise ValueError("each tool run requires tool_name")
        return value


# ────────────────────────── Phase 31 Key Scene Artifact 信封（REQ-VIS-02 / REQ-AGENT-02/03/04） ──────────────────────────


class SceneCandidateArtifact(StrictAgentRuntimeModel):
    """智能体产物的 SceneCandidateArtifact 信封（Phase 31 / D-31-01..D-31-05）。

    Agent 产出的是 **candidate-only** 的关键场景候选集契约：
    ``scene_candidate_set`` 携带完整 ``SceneCandidateSetContract``（ordered
    candidates / diversity keys / evidence refs / spoiler cutoff / salience
    reasons / advisory ``speaker_dialogue_signal`` + 全量 lineage hash），其
    ``review_state`` 恒为 ``candidate``——用户选择/审查（``key_scene:approve``）
    是显式、append-only 的服务端状态迁移（D-31-04），Agent 绝不能直接授予或伪造
    批准。确定性 score/diversity/density/spoiler validator 拥有 permission /
    evidence / state-transition / publication 权威（D-31-01/D-31-03）。REQ-VIS-06
    speaker/dialogue heuristic 信号是诊断候选元数据（D-31-05），绝不进入
    evidence_refs / citation / Canon / 审批原因。``tool_runs`` 携带 ToolRun
    血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version / owner /
    novel / branch / producing skill+version / model lineage / source versions /
    input_hash / evidence_refs / status / parent_revision / normalization
    （26-06 修复血缘 trail）。
    """

    type: Literal["scene_candidate"] = "scene_candidate"
    schema_version: Literal["scene-candidate.v1"] = "scene-candidate.v1"
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
    scene_candidate_set: dict[str, Any]
    tool_runs: list[dict[str, Any]] = Field(min_length=1)
    status: Literal["candidate", "validated", "approved", "published", "rejected"]
    parent_revision: str | None = None
    normalization: NormalizationTrail

    @field_validator("tool_runs")
    @classmethod
    def _tool_runs_shape(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in value:
            if not isinstance(item, dict) or not item.get("tool_name"):
                raise ValueError("each tool run requires tool_name")
        return value


# 供 OpenAPI 引用，避免未使用告警。
_ = (StrictReaderChatModel,)
