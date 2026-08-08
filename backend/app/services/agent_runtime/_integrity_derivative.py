"""Illustration / derivative-domain integrity evaluators（拆分自 structured_output_integrity）。

持有 7 个插画与衍生类信封 evaluator：illustration_revision /
illustration_anchor_proposal / canon_fork_proposal / derivative_edit_proposal /
derivative_draft / branch_visual_bible / export_preparation，及其 Phase 33-39
专属 blocked 常量与 BranchSuggestion 字段助手（``_branch_suggestion_keys``）。
共享基座来自 ``_integrity_core``（叶模块），本模块不依赖其它域模块（零环）。

拆分纪律与原文件一致（fail-closed gate）：任何失败 → 稳定 blocked，零写入；
FastAPI 与各确定性 validator / publisher / materializer 保留 permission /
evidence / state-transition / publication 权威。
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from app.models.agent_runtime import SkillRun
from app.schemas.agent_runtime import (
    BranchVisualBibleArtifact,
    CanonForkProposalArtifact,
    DerivativeEditProposalArtifact,
    DraftArtifact,
    ExportPreparationArtifact,
    IllustrationAnchorProposalArtifact,
    IllustrationRevisionArtifact,
)
from app.services.agent_runtime._integrity_core import (
    BLOCKED_NO_EVIDENCE,
    BLOCKED_SCHEMA,
    IntegrityDecision,
    _check_common_lineage,
    _first_validation_error,
)

# Phase 33 IllustrationRevision 确定性边界（D-33-01..D-33-04）。
BLOCKED_ILLUSTRATION_PAYLOAD = (
    "integrity: illustration revision payload failed domain validation"
)
BLOCKED_ILLUSTRATION_APPROVAL_BYPASS = (
    "integrity: illustration approval bypass blocked — review_state must be candidate"
)
BLOCKED_ILLUSTRATION_SOURCE_DRIFT = "integrity: illustration revision source_snapshot_hash drifts from envelope source_versions"
BLOCKED_ILLUSTRATION_BRANCH = (
    "integrity: illustration revision branch/authority_space mismatch — "
    "derivative mode requires branch + fork, original mode forbids them"
)
# Phase 34 IllustrationAnchorProposal 确定性边界（D-34-01..D-34-04）。
BLOCKED_ANCHOR_PROPOSAL_PAYLOAD = (
    "integrity: illustration anchor proposal payload failed domain validation"
)
BLOCKED_ANCHOR_PROPOSAL_APPROVAL_BYPASS = (
    "integrity: illustration anchor proposal bypass blocked — proposal_status "
    "must be proposed"
)
BLOCKED_ANCHOR_PROPOSAL_SOURCE_DRIFT = (
    "integrity: illustration anchor proposal source_snapshot_hash drifts from "
    "envelope source_versions"
)
BLOCKED_ANCHOR_PROPOSAL_BRANCH = (
    "integrity: illustration anchor proposal branch/authority_space mismatch — "
    "derivative mode requires branch + fork, original mode forbids them"
)
# Phase 35 Canon Fork 确定性边界（D-35-01..D-35-04）。
BLOCKED_CANON_FORK_PAYLOAD = (
    "integrity: canon fork proposal payload failed domain validation"
)
BLOCKED_CANON_FORK_APPROVAL_BYPASS = (
    "integrity: canon fork proposal bypass blocked — proposal_status must be proposed"
)
BLOCKED_CANON_FORK_DELTA_APPROVAL_BYPASS = (
    "integrity: canon delta bypass blocked — delta_status must be proposed"
)
BLOCKED_CANON_FORK_SOURCE_DRIFT = (
    "integrity: canon fork proposal source_snapshot_hash drifts from envelope "
    "source_versions"
)
BLOCKED_CANON_FORK_BRANCH = (
    "integrity: canon fork proposal branch mismatch — envelope branch must match "
    "the run branch"
)
BLOCKED_CANON_FORK_DELTA_HASH = (
    "integrity: canon delta content_hash does not replay from the delta content"
)
# Phase 36 DerivativeEditProposal 确定性边界（D-36-01..D-36-04）。
BLOCKED_DERIVATIVE_EDIT_PAYLOAD = (
    "integrity: derivative edit proposal payload failed domain validation"
)
BLOCKED_DERIVATIVE_EDIT_APPROVAL_BYPASS = (
    "integrity: derivative edit proposal bypass blocked — proposal_status must "
    "be proposed"
)
BLOCKED_DERIVATIVE_EDIT_SOURCE_DRIFT = (
    "integrity: derivative edit proposal source_snapshot_hash drifts from "
    "envelope source_versions"
)
BLOCKED_DERIVATIVE_EDIT_CONTENT_HASH = (
    "integrity: derivative edit proposal content_hash does not replay from the "
    "proposal content"
)
BLOCKED_DERIVATIVE_EDIT_BRANCH = (
    "integrity: derivative edit proposal branch/authority_space mismatch — "
    "envelope branch must match the run branch and derivative mode requires "
    "branch + fork"
)
# Phase 37 Derivative Draft 确定性边界（D-37-02/D-37-05）。
BLOCKED_DERIVATIVE_DRAFT_PAYLOAD = (
    "integrity: derivative draft payload failed domain validation"
)
BLOCKED_DERIVATIVE_DRAFT_APPROVAL_BYPASS = (
    "integrity: derivative draft approval bypass blocked — status must be "
    "candidate at finalize"
)
BLOCKED_DERIVATIVE_DRAFT_BRANCH = (
    "integrity: derivative draft branch/authority_space mismatch — envelope "
    "branch must match the run branch and derivative mode requires branch + fork"
)
BLOCKED_DERIVATIVE_DRAFT_EVIDENCE_MISMATCH = (
    "integrity: derivative draft citation/branch/divergence evidence keys must "
    "be a subset of envelope evidence_refs"
)
BLOCKED_DERIVATIVE_DRAFT_SUGGESTION = (
    "integrity: derivative draft BranchSuggestion must carry exactly the six "
    "fields and enabled_by_default=false; an enabled default or a missing "
    "conflict/delta/evidence field fails closed"
)
# Phase 38 Branch Visual Bible 确定性边界（D-38-03/D-38-04 / REQ-FORK-04）。
BLOCKED_BRANCH_VISUAL_BIBLE_PAYLOAD = (
    "integrity: branch visual bible revision payload failed domain validation"
)
BLOCKED_BRANCH_VISUAL_BIBLE_APPROVAL_BYPASS = (
    "integrity: branch visual bible approval bypass blocked — review_state "
    "must be candidate at finalize"
)
BLOCKED_BRANCH_VISUAL_BIBLE_BRANCH = (
    "integrity: branch visual bible branch/authority_space mismatch — envelope "
    "branch must match the run branch and derivative mode requires branch + fork"
)
BLOCKED_BRANCH_VISUAL_BIBLE_SOURCE_DRIFT = (
    "integrity: branch visual bible source_snapshot_hash drifts from envelope "
    "source_versions"
)
# Phase 39 Export Preparation 确定性边界（D-39-01/D-39-02 / REQ-FORK-05）。
BLOCKED_EXPORT_PREPARATION_PAYLOAD = (
    "integrity: export preparation payload failed domain validation"
)
BLOCKED_EXPORT_PREPARATION_APPROVAL_BYPASS = (
    "integrity: export preparation approval bypass blocked — review_state "
    "must be candidate at finalize"
)
BLOCKED_EXPORT_PREPARATION_BRANCH = (
    "integrity: export preparation branch/authority_space mismatch — envelope "
    "branch must match the run branch and derivative mode requires branch + fork"
)
BLOCKED_EXPORT_PREPARATION_SOURCE_DRIFT = (
    "integrity: export preparation source_snapshot_hash drifts from envelope "
    "source_versions"
)
BLOCKED_EXPORT_PREPARATION_EVIDENCE_MISMATCH = (
    "integrity: export preparation evidence keys must be a subset of envelope "
    "evidence_refs"
)


def _evaluate_illustration_revision(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 33 IllustrationRevision 信封 integrity gate（D-33-01..D-33-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``illustration_revision`` 负载上做确定性域边界校验：
      - ``illustration_revision`` 必须是严格 ``IllustrationRevisionPayload``
        （revision/asset 血缘、SceneSpec/prompt/Visual Bible/source-snapshot
        血缘、provider/model/generator 血缘、rights/consistency/budget 证据
        全部必须、可重放）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass / proposal_ready / published 伪造）→
        blocked（只有 Phase 33 确定性 validator 才能推进状态；Phase 33 永不
        创建 ApprovalRequest、不调用 publisher、不发 published 状态，
        Phase 34 拥有 approval/publication）；
      - 负载的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - branch/authority_space 门：derivative 模式必须有 branch + fork，
        original 模式禁止 branch/fork（wrong scope → blocked）；
      - 信封 branch 必须与 run.branch 血缘一致（wrong branch/fork → blocked）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与 Phase 33 确定性 validator 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进插图网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = IllustrationRevisionArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. illustration_revision 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("illustration_revision")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_ILLUSTRATION_PAYLOAD)
    if payload.get("review_state") != "candidate":
        return IntegrityDecision(False, BLOCKED_ILLUSTRATION_APPROVAL_BYPASS)
    try:
        revision = IllustrationRevisionArtifact.model_validate(
            envelope
        ).illustration_revision
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_ILLUSTRATION_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. source snapshot 血缘绑定（D-33-01）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != revision.source_snapshot_hash:
        return IntegrityDecision(False, BLOCKED_ILLUSTRATION_SOURCE_DRIFT)

    # 5. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_ILLUSTRATION_BRANCH)
    if revision.authority_space == "derivative":
        if not branch or not revision.fork:
            return IntegrityDecision(False, BLOCKED_ILLUSTRATION_BRANCH)
    else:  # original authority space
        if branch or revision.fork:
            return IntegrityDecision(False, BLOCKED_ILLUSTRATION_BRANCH)

    return IntegrityDecision(True)


def _evaluate_illustration_anchor_proposal(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 34 IllustrationAnchorProposal 信封 integrity gate（D-34-01..D-34-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``illustration_anchor_proposal`` 负载上做确定性域边界校验：
      - 负载必须是严格 ``IllustrationAnchorProposalPayload``（proposal key、
        authority space、chapter/精确 source span/hash、source snapshot、
        proposal-ready 资产引用、presentation、requested action 全部必须；
        approval_request_id / proposal_id 由服务端分配）；
      - ``proposal_status`` 恒为 ``proposed``——Agent 声称任何非 proposed
        proposal_status（approval bypass / pending_approval / valid / published
        伪造）→ blocked（只有服务端 proposal/approval/publisher 能推进状态；
        Phase 34 绝不静默发布）；
      - 负载的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - branch/authority_space 门：derivative 模式必须有 branch + fork，
        original 模式禁止 branch/fork；信封 branch 必须与 run.branch 血缘一致。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 publisher 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进锚点提议网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = IllustrationAnchorProposalArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. illustration_anchor_proposal 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("illustration_anchor_proposal")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_PAYLOAD)
    if payload.get("proposal_status") != "proposed":
        return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_APPROVAL_BYPASS)
    try:
        IllustrationAnchorProposalArtifact.model_validate(
            envelope
        ).illustration_anchor_proposal
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_ANCHOR_PROPOSAL_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. source snapshot 血缘绑定（D-34-01）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != payload.get("source_snapshot_hash"):
        return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_SOURCE_DRIFT)

    # 5. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_BRANCH)
    if payload.get("authority_space") == "derivative":
        if not branch or not payload.get("fork"):
            return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_BRANCH)
    else:  # original authority space
        if branch or payload.get("fork"):
            return IntegrityDecision(False, BLOCKED_ANCHOR_PROPOSAL_BRANCH)

    return IntegrityDecision(True)


def _evaluate_canon_fork_proposal(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 35 CanonForkProposal 信封 integrity gate（D-35-01..D-35-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``proposal`` / ``delta`` 负载上做确定性域边界校验：
      - 负载必须是严格 ``CanonForkProposalArtifact``（proposal + delta 全部必须；
        approval_request_id / fork_id 由服务端分配）；
      - ``proposal_status`` / ``delta_status`` 恒为 ``proposed``——Agent 声称任何
        非 proposed 状态（approval bypass / pending_approval / approved / published
        伪造）→ blocked（只有服务端 proposal/approval/Fork materializer 能推进
        状态；Phase 35 绝不物化 fork，D-35-03）；
      - proposal 的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - delta 的 content_hash 必须从 delta content 重放（drift → blocked）；
      - branch 门：信封 branch 必须与 run.branch 血缘一致；proposal/delta 的
        branch/fork 必须与信封一致。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 Fork materializer 保留
    permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 fork 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = CanonForkProposalArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. proposal/delta 负载：严格域契约 + approval bypass 门。
    proposal = envelope.get("proposal")
    delta = envelope.get("delta")
    if not isinstance(proposal, dict) or not isinstance(delta, dict):
        return IntegrityDecision(False, BLOCKED_CANON_FORK_PAYLOAD)
    if proposal.get("proposal_status") != "proposed":
        return IntegrityDecision(False, BLOCKED_CANON_FORK_APPROVAL_BYPASS)
    if delta.get("delta_status") != "proposed":
        return IntegrityDecision(False, BLOCKED_CANON_FORK_DELTA_APPROVAL_BYPASS)
    try:
        CanonForkProposalArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_CANON_FORK_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. source snapshot 血缘绑定（D-35-03）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != proposal.get("source_snapshot_hash"):
        return IntegrityDecision(False, BLOCKED_CANON_FORK_SOURCE_DRIFT)

    # 5. delta content hash 重放（drift → blocked）。口径与 materializer 一致：
    #    sha256(raw UTF-8 bytes)，绝不把内容当 JSON 序列化后再哈希。
    delta_content = delta.get("content")
    delta_hash = delta.get("content_hash")
    if not isinstance(delta_content, str) or not isinstance(delta_hash, str):
        return IntegrityDecision(False, BLOCKED_CANON_FORK_DELTA_HASH)
    if hashlib.sha256(delta_content.encode("utf-8")).hexdigest() != delta_hash:
        return IntegrityDecision(False, BLOCKED_CANON_FORK_DELTA_HASH)

    # 6. branch 门：信封 branch 必须与 run.branch 血缘一致；proposal 的 branch
    #    必须与信封一致（wrong branch → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_CANON_FORK_BRANCH)
    if proposal.get("branch") != branch:
        return IntegrityDecision(False, BLOCKED_CANON_FORK_BRANCH)

    return IntegrityDecision(True)


def _evaluate_derivative_edit_proposal(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 36 DerivativeEditProposal 信封 integrity gate（D-36-01..D-36-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``proposal`` 负载上做确定性域边界校验：
      - 负载必须是严格 ``DerivativeEditProposalPayload``（proposal_key、
        authority_space、project/chapter scope、base_revision CAS 锚、content +
        content_hash、source snapshot、evidence refs；approval_request_id /
        artifact_id 由服务端分配）；
      - ``proposal_status`` 恒为 ``proposed``——Agent 声称任何非 proposed 状态
        （approval bypass / pending_approval / applied 伪造）→ blocked（只有服务端
        proposal/approval/确定性 Revision Service 能推进状态；Phase 36 绝不直接
        应用，D-36-02）；
      - proposal 的 content_hash 必须从 content 重放（drift → blocked）；
      - proposal 的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定；
      - branch 门：信封 branch 必须与 run.branch 血缘一致；authority_space 恒为
        derivative 且 branch/fork 绑定（derivative mode 必须有 branch + fork，
        original 主线禁止 branch/fork）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 Revision Service 保留
    permission / evidence / state-transition / apply 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 derivative 编辑网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = DerivativeEditProposalArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. proposal 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("proposal")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_PAYLOAD)
    if payload.get("proposal_status") != "proposed":
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_APPROVAL_BYPASS)
    try:
        DerivativeEditProposalArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_DERIVATIVE_EDIT_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. content hash 重放（drift → blocked）。口径与 Revision Service 一致：
    #    sha256 of canonical Markdown（markdown_checksum）。
    from app.services.derivative_editor.chapters import markdown_checksum

    content = payload.get("content")
    content_hash = payload.get("content_hash")
    if not isinstance(content, str) or not isinstance(content_hash, str):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_CONTENT_HASH)
    if markdown_checksum(content) != content_hash:
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_CONTENT_HASH)

    # 5. source snapshot 血缘绑定（D-36-02）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if snapshot is not None and snapshot != payload.get("source_snapshot_hash"):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_SOURCE_DRIFT)

    # 6. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_BRANCH)
    if payload.get("branch") != branch:
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_BRANCH)
    if payload.get("authority_space") == "derivative":
        if not branch or not payload.get("fork"):
            return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_BRANCH)
    else:
        if branch or payload.get("fork"):
            return IntegrityDecision(False, BLOCKED_DERIVATIVE_EDIT_BRANCH)

    return IntegrityDecision(True)


def _branch_suggestion_keys(suggestion: dict[str, Any]) -> set[str]:
    """A BranchSuggestion must carry exactly the six frozen fields (D-37-05)."""
    return set(suggestion.keys())


def _evaluate_derivative_draft(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 37 DraftArtifact 信封 integrity gate（D-37-02/D-37-05）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``draft`` / ``continuity_report`` / ``branch_suggestions`` 负载上做确定性
    域边界校验：
      - ``status`` 恒为 ``candidate``——Agent 声称任何非 candidate 状态
        （approval bypass / published 伪造）→ blocked（只有确定性 validator +
        显式 allow_divergence → revalidation → 独立 publish_derivative_revision
        approval 能推进状态；D-37-03/D-37-05）；
      - draft 必须是严格 ``DraftPayload``（intent/draft_text/citation_keys/
        divergence/branch_suggestions + fork/source snapshot/package/manifest/
        draft/canon-delta hashes）；
      - ``branch`` 门：信封 branch 必须与 run.branch 血缘一致；authority_space
        恒为 ``derivative`` 且必须携带 fork（wrong branch/fork → blocked）；
      - 每个 BranchSuggestion（draft 内 + 顶层）必须携带**恰好六字段**且
        ``enabled_by_default=false``（D-37-05；suggestion 是 disabled-by-default
        候选，不自动 fork、不授予/复用任何 approval）；
      - draft 的 citation_keys ∪ suggestion evidence_refs ∪ divergence
        affected_evidence 必须 ⊆ 信封顶层 ``evidence_refs``（leaf-evidence
        资格门）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 validator / 确定性
    revision publisher 保留 permission / evidence / state-transition /
    publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进草稿网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = DraftArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_BRANCH)
    draft = envelope.get("draft")
    if not isinstance(draft, dict):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_PAYLOAD)
    if draft.get("authority_space") != "derivative":
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_BRANCH)
    if not branch or not draft.get("fork"):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_BRANCH)

    # 4. BranchSuggestion 六字段 + enabled_by_default=false（D-37-05）。
    expected_fields = {
        "choice_text",
        "branch_summary",
        "triggering_conflict",
        "canon_delta_hash",
        "evidence_refs",
        "enabled_by_default",
    }
    for source, items in (
        ("draft.branch_suggestions", draft.get("branch_suggestions") or []),
        ("branch_suggestions", envelope.get("branch_suggestions") or []),
    ):
        for index, suggestion in enumerate(items):
            if not isinstance(suggestion, dict):
                return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_SUGGESTION)
            if _branch_suggestion_keys(suggestion) != expected_fields:
                return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_SUGGESTION)
            if suggestion.get("enabled_by_default") is not False:
                return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_SUGGESTION)

    # 5. leaf evidence 资格门：citation/suggestion/divergence 必须 ⊆ 信封。
    envelope_keys = set(envelope.get("evidence_refs") or [])
    citation_keys = set(draft.get("citation_keys") or [])
    suggestion_keys = {
        ref
        for items in (
            draft.get("branch_suggestions") or [],
            envelope.get("branch_suggestions") or [],
        )
        for suggestion in items
        for ref in (suggestion.get("evidence_refs") or [])
    }
    divergence_keys = set(
        (draft.get("divergence") or {}).get("affected_evidence") or []
    )
    all_keys = citation_keys | suggestion_keys | divergence_keys
    if not all_keys.issubset(envelope_keys):
        return IntegrityDecision(False, BLOCKED_DERIVATIVE_DRAFT_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)


def _evaluate_branch_visual_bible(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 38 BranchVisualBibleArtifact 信封 integrity gate（D-38-03/D-38-04）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``revision``（BranchIllustrationRevision）负载上做确定性域边界校验：
      - ``revision`` 必须是严格 ``BranchIllustrationRevisionPayload``
        （visual version / source snapshot / frozen Scene Spec / candidate
        asset / identity/source/generator lineage / divergence manifest /
        consistency verdict / validator report 全部必须、可重放）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass / approved / published 伪造）→ blocked
        （只有确定性 validator + 独立 ``publish_derivative_visual`` Web
        ApprovalRequest → review seam 能推进状态；D-38-03/D-38-04）；
      - ``branch`` 门：信封 branch 必须与 run.branch 血缘一致；
        ``authority_space`` 恒为 ``derivative`` 且必须携带 fork
        （wrong branch/fork → blocked；Original Visual Bible 不可变）；
      - 负载的 source_snapshot_hash 必须与信封 ``source_versions`` 血缘绑定。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 review seam /
    published_assets 保留 permission / evidence / state-transition /
    publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 branch visual 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = BranchVisualBibleArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. revision 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("revision")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_BRANCH_VISUAL_BIBLE_PAYLOAD)
    if payload.get("review_state") != "candidate":
        return IntegrityDecision(False, BLOCKED_BRANCH_VISUAL_BIBLE_APPROVAL_BYPASS)
    try:
        revision = BranchVisualBibleArtifact.model_validate(envelope).revision
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_BRANCH_VISUAL_BIBLE_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. source snapshot 血缘绑定（D-38-01）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if (
        snapshot is not None
        and snapshot != revision.source_snapshot.source_snapshot_hash
    ):
        return IntegrityDecision(False, BLOCKED_BRANCH_VISUAL_BIBLE_SOURCE_DRIFT)

    # 5. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_BRANCH_VISUAL_BIBLE_BRANCH)
    if revision.authority_space != "derivative" or not branch or not revision.fork:
        return IntegrityDecision(False, BLOCKED_BRANCH_VISUAL_BIBLE_BRANCH)

    return IntegrityDecision(True)


def _evaluate_export_preparation(
    envelope: dict[str, Any], run: SkillRun
) -> IntegrityDecision:
    """Phase 39 ExportPreparationArtifact 信封 integrity gate（D-39-01/D-39-02）。

    与其余信封纪律一致（evidence/lineage/status/trail/protected），并在
    ``preparation``（ExportPreparationPayload）负载上做确定性域边界校验：
      - ``preparation`` 必须是严格 ``ExportPreparationPayload``（project/fork
        scope、source snapshot ref、base revision ref、content_hash、evidence
        refs、generator_lineage、validator_report 全部必须、可重放）；
      - ``review_state`` 恒为 ``candidate``——Agent 声称任何非 candidate
        review_state（approval bypass / approved / published 伪造）→ blocked
        （只有确定性 validator + 独立 ``approve_export`` Web ApprovalRequest
        → materializer 能推进状态；D-39-01/D-39-02）；
      - ``branch`` 门：信封 branch 必须与 run.branch 血缘一致；
        ``authority_space`` 恒为 ``derivative`` 且必须携带 fork（wrong
        branch/fork → blocked；Original Canon 不可变，REQ-FORK-05）；
      - 负载的 source_snapshot.source_snapshot_hash 必须与信封
        ``source_versions`` 血缘绑定；
      - 负载的 evidence_refs 必须 ⊆ 信封顶层 ``evidence_refs``（leaf-evidence
        资格门）。
    任何失败 → 稳定 blocked，零写入；FastAPI 与确定性 materializer /
    download 保留 permission / evidence / state-transition / publication 权威。
    """
    # 0. heuristic candidate-only 无 EvidenceRef 资格 → 不能进 export 网关。
    if not envelope.get("evidence_refs"):
        return IntegrityDecision(False, BLOCKED_NO_EVIDENCE)

    # 1. 严格 wire schema。
    try:
        model = ExportPreparationArtifact.model_validate(envelope)
    except ValidationError as exc:
        return IntegrityDecision(
            False, f"{BLOCKED_SCHEMA} ({_first_validation_error(exc)})"
        )

    # 2. 共享 lineage/status/trail/protected 门。
    blocked = _check_common_lineage(envelope=envelope, run=run, wire=model)
    if blocked is not None:
        return blocked

    # 3. preparation 负载：严格域契约 + approval bypass 门。
    payload = envelope.get("preparation")
    if not isinstance(payload, dict):
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_PAYLOAD)
    if payload.get("review_state") != "candidate":
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_APPROVAL_BYPASS)
    try:
        preparation = ExportPreparationArtifact.model_validate(envelope).preparation
    except ValidationError as exc:
        return IntegrityDecision(
            False,
            f"{BLOCKED_EXPORT_PREPARATION_PAYLOAD} ({_first_validation_error(exc)})",
        )

    # 4. source snapshot 血缘绑定（D-39-01）。
    source_versions = envelope.get("source_versions") or {}
    snapshot = source_versions.get("source_snapshot_hash")
    if (
        snapshot is not None
        and snapshot != preparation.source_snapshot.source_snapshot_hash
    ):
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_SOURCE_DRIFT)

    # 5. branch/authority_space 门（wrong branch/fork → fail closed）。
    branch = envelope.get("branch")
    if branch != run.branch:
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_BRANCH)
    if (
        preparation.authority_space != "derivative"
        or not branch
        or not preparation.fork
    ):
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_BRANCH)

    # 6. leaf evidence 资格门：preparation.evidence_refs ⊆ 信封 evidence_refs。
    envelope_keys = set(envelope.get("evidence_refs") or [])
    payload_keys = set(preparation.evidence_refs or [])
    if not payload_keys.issubset(envelope_keys):
        return IntegrityDecision(False, BLOCKED_EXPORT_PREPARATION_EVIDENCE_MISMATCH)

    return IntegrityDecision(True)
