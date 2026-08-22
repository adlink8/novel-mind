"""Phase 27-31 领域 Artifact 信封：WorldModel / ChapterAnalysis / StoryArc /
SkillEvaluation / VisualBible / SceneCandidate。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_runtime.base import (
    NormalizationTrail,
    StrictAgentRuntimeModel,
)


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
