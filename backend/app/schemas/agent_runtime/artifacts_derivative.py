"""Phase 35-39 领域 Artifact 信封：CanonFork / DerivativeEdit / Draft /
BranchVisualBible / ExportPreparation。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.schemas.agent_runtime.base import (
    NormalizationTrail,
    StrictAgentRuntimeModel,
)


class CanonForkProposalPayload(StrictAgentRuntimeModel):
    """Phase 35 CanonForkProposal 负载（D-35-01..D-35-04 / REQ-FORK-01）。

    携带完整 branch-aware 血缘：fork_key、source snapshot、server-derived
    cutoff、scope/manifest hashes、frozen citation lineage 与服务端授权记录。
    ``proposal_status`` 是 Phase 35 唯一官方状态机（proposed → pending_approval
    → approved），**finalize 写入时恒为 proposed**——只有服务端 proposal /
    approval / deterministic Fork materializer 能推进状态；Phase 35 绝不物化
    fork 或触碰 Original Canon（D-35-03）。
    """

    schema_version: Literal["canon-fork-proposal.v1"] = "canon-fork-proposal.v1"
    artifact_kind: Literal["canon_fork_proposal"] = "canon_fork_proposal"
    fork_key: str = Field(min_length=1, max_length=128)
    branch: str | None = Field(default=None, max_length=80)
    fork: str | None = Field(default=None, max_length=80)
    source_version_key: str = Field(min_length=1, max_length=128)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    through_chapter: int = Field(ge=1)
    full_book_authorized: bool = False
    cutoff_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation_lineage: list[dict[str, Any]] = Field(min_length=1)
    authorization: dict[str, Any]
    proposal_status: Literal["proposed", "pending_approval", "approved"] = "proposed"
    approval_request_id: int | None = Field(default=None, gt=0)
    fork_id: int | None = Field(default=None, gt=0)


class CanonDeltaPayload(StrictAgentRuntimeModel):
    """Phase 35 CanonDeltaArtifact 负载（D-35-01..D-35-04）。

    候选 derivative 内容 + base revision（frozen fork manifest_hash，stale base
    → fail closed）+ content hash + evidence refs。``delta_status`` 恒为
    ``proposed``（finalize 时）——只有服务端 Fork materializer 在批准后物化。
    """

    schema_version: Literal["canon-delta.v1"] = "canon-delta.v1"
    artifact_kind: Literal["canon_delta"] = "canon_delta"
    delta_key: str = Field(min_length=1, max_length=160)
    base_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str = Field(min_length=1, max_length=50000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(default_factory=list)
    delta_status: Literal["proposed", "pending_approval", "approved"] = "proposed"


class CanonForkProposalArtifact(StrictAgentRuntimeModel):
    """智能体产物的 CanonForkProposal 信封（Phase 35 / D-35-01..D-35-04）。


    Agent 产出的是 **candidate-only** 的 fork 提议：``proposal``（完整冻结
    CanonForkProposal）与 ``delta``（候选 CanonDeltaArtifact）携带完整
    branch-aware 血缘（SkillRun/ToolRun、owner/novel/branch、source/input
    hashes、evidence refs、runtime/model lineage、frozen manifest 与授权记录）。
    ``proposal_status`` / ``delta_status`` 恒为 ``proposed``（finalize 时）——
    只有服务端 proposal/approval/Fork materializer 能推进状态；Agent/浏览器绝不
    物化 fork、绝不触碰 Original Canon（D-35-03）。``tool_runs`` 携带 ToolRun
    血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version / owner /
    novel / branch / producing skill+version / model lineage / source versions /
    input_hash / evidence_refs / status / parent_revision / normalization
    （26-06 修复血缘 trail）。
    """

    type: Literal["canon_fork_proposal"] = "canon_fork_proposal"
    schema_version: Literal["canon-fork-proposal.v1"] = "canon-fork-proposal.v1"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    proposal: CanonForkProposalPayload
    delta: CanonDeltaPayload
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


class DerivativeEditProposalPayload(StrictAgentRuntimeModel):
    """Phase 36 DerivativeEditProposal 负载（D-36-01..D-36-04 / REQ-FORK-02）。

    携带完整 branch-aware 血缘：proposal_key、authority_space（恒为
    ``derivative``，Fanfiction Canon）、project/chapter scope、base_revision
    CAS 锚、候选 Markdown patch（content + content_hash）、source snapshot
    血缘、evidence refs 与 validator_report。``proposal_status`` 是 Phase 36
    唯一官方状态机（proposed → pending_approval → applied），**finalize 写入时
    恒为 proposed**——只有服务端 proposal/approval/确定性 Revision Service 能
    推进状态；Phase 36 绝不直接应用（D-36-02）。``authority_space`` 恒为
    derivative——绝不写 Original Canon / User Interpretation / user draft
    （autosave）revisions / published 状态。
    """

    schema_version: Literal["derivative-edit-proposal.v1"] = (
        "derivative-edit-proposal.v1"
    )
    artifact_kind: Literal["derivative_edit_proposal"] = "derivative_edit_proposal"
    proposal_key: str = Field(min_length=1, max_length=160)
    authority_space: Literal["derivative"] = "derivative"
    branch: str | None = Field(default=None, max_length=80)
    fork: str | None = Field(default=None, max_length=80)
    project_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    base_revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=50000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(default_factory=list)
    proposal_status: Literal["proposed", "pending_approval", "applied"] = "proposed"
    approval_request_id: int | None = Field(default=None, gt=0)
    artifact_id: int | None = Field(default=None, gt=0)
    validator_report: dict[str, Any] | None = Field(default=None)


class DerivativeEditProposalArtifact(StrictAgentRuntimeModel):
    """智能体产物的 DerivativeEditProposal 信封（Phase 36 / D-36-01..D-36-04）。

    Agent 产出的是 **candidate-only** 的派生 chapter 编辑提议：``proposal``
    携带完整 branch-aware 血缘（SkillRun/ToolRun、owner/novel/branch、
    project/chapter scope、base_revision CAS 锚、source/input hashes、
    content + content_hash、evidence refs、runtime/model lineage 与
    validator_report）。``proposal_status`` 恒为 ``proposed``（finalize 时）——
    只有服务端 proposal/approval/确定性 Revision Service（
    ``app.services.derivative_editor.revisions.apply_agent_edit``）能推进状态；
    Agent/浏览器绝不直接应用 proposal、绝不触碰 Original Canon / user draft
    （autosave）revisions / published 状态（D-36-02）。``tool_runs`` 携带
    ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version /
    owner / novel / branch / producing skill+version / model lineage /
    source versions / input_hash / evidence_refs / status / parent_revision /
    normalization（26-06 修复血缘 trail）。
    """

    type: Literal["derivative_edit_proposal"] = "derivative_edit_proposal"
    schema_version: Literal["derivative-edit-proposal.v1"] = (
        "derivative-edit-proposal.v1"
    )
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    proposal: DerivativeEditProposalPayload
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


class BranchSuggestionPayload(StrictAgentRuntimeModel):
    """Phase 37 disabled-by-default candidate BranchSuggestion（D-37-05 / REQ-FORK-06）。

    六字段契约：``choice_text`` / ``branch_summary`` / ``triggering_conflict`` /
    ``canon_delta_hash`` / ``evidence_refs`` / ``enabled_by_default=false``。
    只描述可供用户选择的分支选项——绝不自动 fork、不改变任何 Canon/branch
    状态、不能复用 ``allow_divergence`` approval；发布仍需独立的
    ``publish_derivative_revision`` approval。
    """

    choice_text: str = Field(min_length=1, max_length=2000)
    branch_summary: str = Field(min_length=1, max_length=2000)
    triggering_conflict: str = Field(min_length=1, max_length=2000)
    canon_delta_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(default_factory=list)
    # D-37-05：默认禁用；任何 enabled 默认值都被 wire schema 拒绝。
    enabled_by_default: Literal[False] = False


class ContinuityReportPayload(StrictAgentRuntimeModel):
    """Phase 37 确定性 gate 快照（candidate | blocked | needs_override）。"""

    verdict: Literal["candidate", "blocked", "needs_override"]
    reason: str | None = Field(default=None, max_length=160)
    detail: str | None = Field(default=None, max_length=2000)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    branch_suggestions: list[BranchSuggestionPayload] = Field(default_factory=list)


class DraftPayload(StrictAgentRuntimeModel):
    """Phase 37 DraftArtifact 负载（D-37-02/D-37-05 / REQ-FORK-03）。

    携带完整 branch-aware 血缘：intent、候选草稿、citation keys、显式
    CanonDelta、disabled-by-default BranchSuggestion[]、fork/source snapshot/
    package/manifest hash 与 exact draft_hash + canon_delta_hash。``authority_space``
    恒为 ``derivative``（Fanfiction Canon）——绝不写 Original Canon。suggestion 只
    是候选输出：不自动 fork、不授予/复用任何 approval。
    """

    schema_version: Literal["derivative-candidate.v1"] = "derivative-candidate.v1"
    artifact_kind: Literal["derivative_draft"] = "derivative_draft"
    authority_space: Literal["derivative"] = "derivative"
    intent: Literal["continuation", "rewrite"]
    draft_text: str = Field(min_length=1, max_length=40000)
    summary: str | None = Field(default=None, max_length=1000)
    citation_keys: list[str] = Field(default_factory=list)
    divergence: dict[str, Any] | None = None
    branch_suggestions: list[BranchSuggestionPayload] = Field(default_factory=list)
    fork: str | None = Field(default=None, max_length=80)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    canon_delta_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class DraftArtifact(StrictAgentRuntimeModel):
    """智能体产物的 DraftArtifact 信封（Phase 37 / D-37-02/D-37-05）。

    Agent 产出的是 **candidate-only** 的派生草稿：``draft``（完整候选负载）+
    ``continuity_report``（确定性 gate 快照）+ 顶层 ``branch_suggestions``
    （disabled-by-default，六字段契约）。``status`` 恒为 ``candidate``（finalize
    时）——只有确定性 validator + 显式 approval（allow_divergence →
    revalidation → 独立 publish_derivative_revision approval）能推进；Agent/
    浏览器绝不直接写 Original Canon / 域表 / published 状态。``tool_runs`` 携带
    ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type / schema_version /
    owner / novel / branch / producing skill+version / model lineage /
    source versions / input_hash / evidence_refs / status / parent_revision /
    normalization（26-06 修复血缘 trail）。
    """

    type: Literal["derivative_draft"] = "derivative_draft"
    schema_version: Literal["draft-artifact.v1"] = "draft-artifact.v1"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    draft: DraftPayload
    continuity_report: ContinuityReportPayload
    branch_suggestions: list[BranchSuggestionPayload] = Field(default_factory=list)
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


class BranchIllustrationVisualVersionRef(StrictAgentRuntimeModel):
    """已批准 derivative Visual Bible fork version ref（D-38-01，hash-pinned）。"""

    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=160)
    version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BranchIllustrationSourceSnapshotRef(StrictAgentRuntimeModel):
    """Source snapshot 血缘 ref（D-38-01：Original Visual Bible snapshot 只读）。"""

    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class BranchIllustrationCandidateAssetRef(StrictAgentRuntimeModel):
    """已存储 candidate asset ref（D-38-03 生成 asset_id + content checksum 重放）。"""

    candidate_asset_id: int = Field(gt=0)
    asset_id: str = Field(min_length=1, max_length=200)
    asset_key: str = Field(min_length=1, max_length=180)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)


class BranchIllustrationIdentityRow(StrictAgentRuntimeModel):
    """One identity row pinned to the exact Original Visual Bible entity."""

    stable_id: str = Field(min_length=1, max_length=180)
    entity_key: str = Field(min_length=1, max_length=180)
    entity_type: str = Field(pattern=r"^(character|place|item|faction|style)$")
    source_entity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BranchIllustrationSourceRef(StrictAgentRuntimeModel):
    """One Original asset reference (source asset id + bytes hash)."""

    asset_key: str = Field(min_length=1, max_length=180)
    asset_id: str = Field(min_length=1, max_length=200)
    source_asset_id: str = Field(min_length=1, max_length=200)
    source_bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BranchIllustrationRevisionPayload(StrictAgentRuntimeModel):
    """Phase 38 BranchIllustrationRevision 负载（D-38-03/D-38-04 / REQ-FORK-04）。

    携带完整 branch-aware 血缘：visual version ref、source snapshot ref、frozen
    canonical Scene Spec hash、candidate asset ref、identity/source/generator
    lineage、divergence manifest hash、consistency verdict 与 validator report。
    ``authority_space`` 恒为 ``derivative`` + ``fork`` 必须（Original Visual
    Bible 不可变，REQ-FORK-04）；``review_state`` 恒为 ``candidate``（finalize
    时）——只有确定性 validator + 独立 ``publish_derivative_visual`` Web
    ApprovalRequest → review seam 能推进 approved published。``approval_request_id``
    / ``publish_lineage`` 由服务端分配，模型输出不含。
    """

    schema_version: Literal["branch-illustration-revision.v1"] = (
        "branch-illustration-revision.v1"
    )
    artifact_kind: Literal["branch_illustration_revision"] = (
        "branch_illustration_revision"
    )
    authority_space: Literal["derivative"] = "derivative"
    fork: str = Field(min_length=1, max_length=80)
    visual_version: BranchIllustrationVisualVersionRef
    source_snapshot: BranchIllustrationSourceSnapshotRef
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_asset: BranchIllustrationCandidateAssetRef
    identity_lineage: list[BranchIllustrationIdentityRow] = Field(default_factory=list)
    source_refs: list[BranchIllustrationSourceRef] = Field(default_factory=list)
    generator_lineage: dict[str, Any] = Field(default_factory=dict)
    divergence_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    consistency_verdict: Literal["pass", "concern", "fail", "unavailable"]
    validator_report: dict[str, Any] = Field(default_factory=dict)
    review_state: Literal["candidate"] = "candidate"
    approval_request_id: int | None = Field(default=None, gt=0)
    publish_lineage: dict[str, Any] = Field(default_factory=dict)


class BranchVisualBibleArtifact(StrictAgentRuntimeModel):
    """智能体产物的 BranchVisualBibleArtifact 信封（Phase 38 / D-38-03/D-38-04）。

    Agent 产出的是 **candidate-only** 的 branch Visual Bible 修订：``revision``
    （完整 BranchIllustrationRevision）携带 branch-aware 血缘（SkillRun/ToolRun、
    owner/novel/branch/fork、source snapshot、visual version、frozen Scene Spec、
    candidate asset、identity/source/generator lineage、divergence manifest、
    consistency verdict 与 validator report）。``review_state`` 恒为 ``candidate``
    （finalize 时）——只有确定性 validator + 独立 ``publish_derivative_visual``
    Web ApprovalRequest → review seam（``review_candidate_asset`` →
    ``apply_derivative_asset_review``）能把 candidate 物化为 approved published
    asset；Agent/浏览器绝不发布、绝不触碰 Original Visual Bible（REQ-FORK-04）。
    ``tool_runs`` 携带 ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type /
    schema_version / owner / novel / branch / producing skill+version / model
    lineage / source versions / input_hash / evidence_refs / status /
    parent_revision / normalization（26-06 修复血缘 trail）。
    """

    type: Literal["branch_visual_bible"] = "branch_visual_bible"
    schema_version: Literal["branch-visual-bible.v1"] = "branch-visual-bible.v1"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    revision: BranchIllustrationRevisionPayload
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


class ExportPreparationSourceSnapshotRef(StrictAgentRuntimeModel):
    """Source snapshot 血缘 ref（D-39-01：project 冻结 fork 血缘只读）。"""

    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class ExportPreparationBaseRevisionRef(StrictAgentRuntimeModel):
    """Project 冻结 fork 血缘 ref（revision/version/snapshot 对齐，D-39-01）。"""

    project_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExportPreparationPayload(StrictAgentRuntimeModel):
    """Phase 39 ExportPreparation 负载（D-39-01/D-39-02 / REQ-FORK-05）。

    携带完整 branch-aware 血缘：project/fork scope、source snapshot ref、base
    revision ref、content_hash（候选声称的 frozen manifest/snapshot hash）、
    evidence refs、runtime/model/generator lineage 与 validator report。
    ``authority_space`` 恒为 ``derivative`` + ``fork`` 必须（Original Canon
    不可变，REQ-FORK-05）；``review_state`` 恒为 ``candidate``（finalize 时）
    ——只有确定性 validator + 独立 ``approve_export`` Web ApprovalRequest →
    materializer 能推进 approved。``approval_request_id`` /
    ``materialize_lineage`` 由服务端分配，模型输出不含。
    """

    schema_version: Literal["export-preparation.v1"] = "export-preparation.v1"
    artifact_kind: Literal["export_preparation"] = "export_preparation"
    authority_space: Literal["derivative"] = "derivative"
    fork: str = Field(min_length=1, max_length=80)
    project_id: int = Field(gt=0)
    project_key: str = Field(min_length=1, max_length=128)
    source_snapshot: ExportPreparationSourceSnapshotRef
    base_revision: ExportPreparationBaseRevisionRef
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    generator_lineage: dict[str, Any] = Field(default_factory=dict)
    validator_report: dict[str, Any] = Field(default_factory=dict)
    review_state: Literal["candidate"] = "candidate"
    approval_request_id: int | None = Field(default=None, gt=0)
    materialize_lineage: dict[str, Any] = Field(default_factory=dict)


class ExportPreparationArtifact(StrictAgentRuntimeModel):
    """智能体产物的 ExportPreparationArtifact 信封（Phase 39 / D-39-01/D-39-02）。

    Agent 产出的是 **candidate-only** 的 derivative export 准备：``preparation``
    （完整 ExportPreparationPayload）携带 branch-aware 血缘（SkillRun/ToolRun、
    owner/novel/branch/fork、project scope、source snapshot、base revision、
    content hash、evidence refs、runtime/model/generator lineage 与 validator
    report）。``review_state`` 恒为 ``candidate``（finalize 时）——只有确定性
    validator + 独立 ``approve_export`` Web ApprovalRequest → 确定性
    materializer（``app.services.derivative_export.materializer.
    materialize_export``）能把候选 artifact 推进为 approved 并产出可复现
    bundle；Agent/浏览器绝不物化、绝不触碰 Original Canon（REQ-FORK-05）。
    ``tool_runs`` 携带 ToolRun 血缘。与 CitedAnswerArtifact 信封纪律一致：type /
    schema_version / owner / novel / branch / producing skill+version / model
    lineage / source versions / input_hash / evidence_refs / status /
    parent_revision / normalization（26-06 修复血缘 trail）。
    """

    type: Literal["export_preparation"] = "export_preparation"
    schema_version: Literal["export-preparation.v1"] = "export-preparation.v1"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    branch: str | None = None
    producing_skill: str = Field(min_length=1, max_length=120)
    producing_skill_version: str = Field(min_length=1, max_length=32)
    skill_version_id: int = Field(gt=0)
    model_lineage: dict[str, Any]
    source_versions: dict[str, Any]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_refs: list[str] = Field(min_length=1)
    preparation: ExportPreparationPayload
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
