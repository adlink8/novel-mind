"""Phase 32-34 领域 Artifact 信封：SceneSpec / Prompt / IllustrationRevision /
IllustrationAnchorProposal。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_runtime.base import (
    NormalizationTrail,
    StrictAgentRuntimeModel,
)


class SceneSpecArtifact(StrictAgentRuntimeModel):
    """智能体产物的 SceneSpecArtifact 信封（Phase 32 / D-32-01..D-32-04）。

    Agent 产出的是 **candidate-only** 的 SceneSpecContract：``scene_spec``
    携带完整 SceneSpecContract（spec_key / scene_candidate_hash /
    visual_bible_revision_hash / source snapshot / cutoff / compiler lineage /
    details / negative_constraints / uncertainties），其 ``review_state`` 恒为
    ``candidate``——用户审查/批准（``scene_spec:approve``）是显式、append-only
    的服务端状态迁移（D-32-04），只授权 Phase 33 消费，Agent 绝不能直接授予或
    伪造批准。确定性 Canon/Visual Bible 一致性与未支持细节 validator 拥有
    permission / evidence / state-transition / publication 权威（D-32-02）；无
    unsupported Canon，缺失引用只能以 reason-coded uncertainties 呈现。``tool_runs``
    携带 ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version /
    owner / novel / branch / producing skill+version / model lineage /
    source versions / input_hash / evidence_refs / status / parent_revision /
    normalization（26-06 修复血缘 trail）。
    """

    type: Literal["scene_spec"] = "scene_spec"
    schema_version: Literal["scene-spec.v1"] = "scene-spec.v1"
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
    scene_spec: dict[str, Any]
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


class PromptArtifact(StrictAgentRuntimeModel):
    """智能体产物的 PromptArtifact 信封（Phase 32 / D-32-01..D-32-04）。

    Agent 产出的是 **candidate-only** 的 provider-neutral 派生 prompt：
    ``prompt_revision`` 携带完整 PromptRevisionContract（adapter lineage +
    可重放 input_hash/prompt_hash），``scene_spec`` 携带其派生自的
    SceneSpecContract（prompt 派生血缘，D-32-03）——prompt 字符串永远不是权威
    （D-32-01）。``review_state`` 恒为 ``candidate``——用户审查/批准
    （``scene_spec:approve``）是显式、append-only 的服务端状态迁移（D-32-04），
    只授权 Phase 33 消费，Agent 绝不能直接授予或伪造批准。确定性 Canon/Visual
    Bible 一致性与未支持细节 validator 拥有 permission / evidence /
    state-transition / publication 权威（D-32-02）。``tool_runs`` 携带 ToolRun
    血缘。与 CitedAnswerArtifact 信封纪律一致（lineage / evidence_refs / status /
    parent_revision / normalization）。
    """

    type: Literal["prompt"] = "prompt"
    schema_version: Literal["prompt-revision.v1"] = "prompt-revision.v1"
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
    prompt_revision: dict[str, Any]
    scene_spec: dict[str, Any]
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


class IllustrationRevisionPayload(StrictAgentRuntimeModel):
    """Phase 33 IllustrationRevision 负载（D-33-01..D-33-04 / REQ-VIS-04）。

    携带完整 branch-aware 血缘：revision key/number、域 AssetRevision 引用、
    SceneSpec/prompt/Visual Bible/source-snapshot 血缘、provider/model/generator
    血缘、rights/provenance、一致性 review signal 与 budget 证据。``review_state``
    是 Phase 33 唯一官方状态机（candidate → validated → proposal_ready），
    **finalize 写入时恒为 candidate**——只有 Phase 33 确定性 validator 才能推进
    状态；Phase 33 永不创建 ApprovalRequest、不调用 publisher、不发 published
    状态（Phase 34 拥有 approval/publication）。
    """

    schema_version: Literal["illustration-revision.v1"] = "illustration-revision.v1"
    artifact_kind: Literal["illustration_revision"] = "illustration_revision"
    revision_key: str = Field(min_length=1, max_length=180)
    revision_number: int = Field(ge=1)
    asset_revision_id: int | None = Field(default=None, gt=0)
    authority_space: Literal["original", "derivative"] = "original"
    fork: str | None = Field(default=None, max_length=80)
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_revision_id: int | None = Field(default=None, gt=0)
    prompt_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_bible_revision_id: int | None = Field(default=None, gt=0)
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    provider: str = Field(min_length=1, max_length=64)
    provider_model: str = Field(min_length=1, max_length=120)
    provider_request_id: str | None = Field(default=None, max_length=160)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_version: str = Field(min_length=1, max_length=64)
    rights_status: Literal["unreviewed", "cleared", "pending", "denied"]
    consistency_verdict: Literal["pass", "concern", "fail", "unavailable"]
    fixture_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_settled_calls: int = Field(ge=0)
    budget_settled_cost_usd: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    review_state: Literal["candidate", "validated", "proposal_ready"] = "candidate"


class IllustrationRevisionArtifact(StrictAgentRuntimeModel):
    """智能体产物的 IllustrationRevision 信封（Phase 33 / D-33-01..D-33-04）。

    Agent 产出的是 **candidate-only** 的 IllustrationRevision：``illustration_revision``
    携带完整 branch-aware 血缘（SkillRun/ToolRun、owner/novel/branch、source/input
    hashes、evidence refs、runtime/model/generator lineage 与域 AssetRevision
    引用）。``review_state`` 恒为 ``candidate``（finalize 时）——只有 Phase 33
    确定性 validator（budget/rights/fidelity/consistency gate）能推进
    candidate → validated → proposal_ready；Phase 33 **绝不**创建
    ApprovalRequest、调用 publisher 或发出 published 状态（Phase 34 拥有
    approval/publication）。``tool_runs`` 携带 ToolRun 血缘。与 CitedAnswerArtifact
    信封纪律一致：type / schema_version / owner / novel / branch / producing
    skill+version / model lineage / source versions / input_hash / evidence_refs /
    status / parent_revision / normalization（26-06 修复血缘 trail）。
    """

    type: Literal["illustration_revision"] = "illustration_revision"
    schema_version: Literal["illustration-revision.v1"] = "illustration-revision.v1"
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
    illustration_revision: IllustrationRevisionPayload
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


class IllustrationAnchorProposalRange(StrictAgentRuntimeModel):
    """Exact source span (code-point offsets) + optional paragraph range (D-34-01)."""

    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    paragraph_start: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)


class IllustrationAnchorProposalCopy(StrictAgentRuntimeModel):
    """Accessible caption/alt/citation contract (D-34-02, never empty)."""

    caption: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(min_length=1, max_length=500)
    citation: str = Field(min_length=1, max_length=1000)


class IllustrationAnchorProposalPayload(StrictAgentRuntimeModel):
    """Phase 34 IllustrationAnchorProposal 负载（D-34-01..D-34-04 / REQ-VIS-05）。

    携带完整 branch-aware 血缘：proposal key、authority space、chapter/精确
    source span/hash、source snapshot、proposal-ready AssetRevision 引用、
    presentation、requested action 与 proposal_status。``proposal_status`` 是
    Phase 34 唯一官方状态机（proposed → pending_approval → valid），**finalize
    写入时恒为 proposed**——只有服务端 proposal/approval/publisher 能推进状态；
    Phase 34 绝不静默发布（D-34-01）。
    """

    schema_version: Literal["illustration-anchor-proposal.v1"] = (
        "illustration-anchor-proposal.v1"
    )
    artifact_kind: Literal["illustration_anchor_proposal"] = (
        "illustration_anchor_proposal"
    )
    proposal_key: str = Field(min_length=1, max_length=160)
    authority_space: Literal["original", "derivative"] = "original"
    fork: str | None = Field(default=None, max_length=80)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    range: IllustrationAnchorProposalRange
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_asset_revision_id: int = Field(gt=0)
    presentation: IllustrationAnchorProposalCopy
    requested_action: Literal["publish_illustration", "attach_illustration_to_text"]
    proposal_status: Literal["proposed", "pending_approval", "valid"] = "proposed"
    approval_request_id: int | None = Field(default=None, gt=0)
    proposal_id: int | None = Field(default=None, gt=0)


class IllustrationAnchorProposalArtifact(StrictAgentRuntimeModel):
    """智能体产物的 IllustrationAnchorProposal 信封（Phase 34 / D-34-01..D-34-04）。

    Agent 产出的是 **candidate-only** 的锚点提议：``illustration_anchor_proposal``
    携带完整 branch-aware 血缘（SkillRun/ToolRun、owner/novel/branch、source/input
    hashes、evidence refs、runtime/model lineage、proposal-ready AssetRevision
    引用与精确 source span）。``proposal_status`` 恒为 ``proposed``（finalize 时）
    ——只有服务端 proposal/approval/publisher 能推进 proposed → pending_approval
    → valid；Agent/浏览器绝不发布（D-34-01）。``tool_runs`` 携带 ToolRun 血缘。
    与 CitedAnswerArtifact 信封纪律一致：type / schema_version / owner / novel /
    branch / producing skill+version / model lineage / source versions /
    input_hash / evidence_refs / status / parent_revision / normalization
    （26-06 修复血缘 trail）。
    """

    type: Literal["illustration_anchor_proposal"] = "illustration_anchor_proposal"
    schema_version: Literal["illustration-anchor-proposal.v1"] = (
        "illustration-anchor-proposal.v1"
    )
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
    illustration_anchor_proposal: IllustrationAnchorProposalPayload
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
