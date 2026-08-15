"""Structured Output Integrity 服务端严格适配器（26-06 / REQ-AGENT-08 / D-16）。

唯一 Artifact finalizer（`finalize.py`）在 `create_artifact_with_first_revision`
**之前**调用本模块的 fail-closed gate。规则:
  - 只有通过严格 wire schema、run lineage、完整性 trail 重放与 leaf-evidence
    校验的 repaired payload 才能成为官方 Artifact。
  - normalization 绝不授予 authority：受保护字段（owner/cutoff/authority/branch/
    fork/approval）缺失或非法 → blocked，**不补默认值**；repaired_hash 漂移
    （payload 在规范化后被改动）→ blocked。
  - blocked/invalid → 0 Artifact、0 Revision；不触发 ApprovalRequest / Publisher /
    promotion / active-pointer 写入。
  - evidence_refs 的冻结 manifest 白名单校验仍由
    `reader_chat.validate_answer_against_manifest`（finalize 内）承担；本模块做
    schema、lineage 与 trail 的权威门，并复用现有 `CitedAnswerArtifact` /
    `ExternalEvidenceArtifact` wire 模型（不新增第二套事实或 citation authority）。

拆分说明（refactor split）：本模块保留门面本体 —— ``evaluate_integrity`` 调度
与全部 public/private 符号的显式 re-export。17 个 ``_evaluate_*`` 实现按功能域
拆到同目录模块（``_integrity_narrative`` / ``_integrity_visual`` /
``_integrity_derivative``），共享基座（``IntegrityDecision``、canonical hash、
``_check_common_lineage``、公共 BLOCKED_* 常量）在叶模块 ``_integrity_core``。
``__all__`` 显式声明全部符号，``from app.services.agent_runtime
.structured_output_integrity import X`` 的 import surface 不变。
"""

from __future__ import annotations

from typing import Any

from app.models.agent_runtime import SkillRun

from ._integrity_core import (
    BLOCKED_LINEAGE_INPUT_HASH,
    BLOCKED_LINEAGE_NOVEL,
    BLOCKED_LINEAGE_OWNER,
    BLOCKED_LINEAGE_SKILL,
    BLOCKED_NO_EVIDENCE,
    BLOCKED_PROTECTED_SYNTHESIS,
    BLOCKED_SCHEMA,
    BLOCKED_STALE_REPAIRED_HASH,
    BLOCKED_STATUS,
    BLOCKED_TRAIL_INCONSISTENT,
    BLOCKED_UNKNOWN_TYPE,
    FORBIDDEN_PROTECTED_KEYS,
    IntegrityDecision,
    _check_common_lineage,
    _first_validation_error,
    _strip_trail,
    canonical_content_hash,
)
from ._integrity_derivative import (
    BLOCKED_ANCHOR_PROPOSAL_APPROVAL_BYPASS,
    BLOCKED_ANCHOR_PROPOSAL_BRANCH,
    BLOCKED_ANCHOR_PROPOSAL_PAYLOAD,
    BLOCKED_ANCHOR_PROPOSAL_SOURCE_DRIFT,
    BLOCKED_BRANCH_VISUAL_BIBLE_APPROVAL_BYPASS,
    BLOCKED_BRANCH_VISUAL_BIBLE_BRANCH,
    BLOCKED_BRANCH_VISUAL_BIBLE_PAYLOAD,
    BLOCKED_BRANCH_VISUAL_BIBLE_SOURCE_DRIFT,
    BLOCKED_CANON_FORK_APPROVAL_BYPASS,
    BLOCKED_CANON_FORK_BRANCH,
    BLOCKED_CANON_FORK_DELTA_APPROVAL_BYPASS,
    BLOCKED_CANON_FORK_DELTA_HASH,
    BLOCKED_CANON_FORK_PAYLOAD,
    BLOCKED_CANON_FORK_SOURCE_DRIFT,
    BLOCKED_DERIVATIVE_DRAFT_APPROVAL_BYPASS,
    BLOCKED_DERIVATIVE_DRAFT_BRANCH,
    BLOCKED_DERIVATIVE_DRAFT_EVIDENCE_MISMATCH,
    BLOCKED_DERIVATIVE_DRAFT_PAYLOAD,
    BLOCKED_DERIVATIVE_DRAFT_SUGGESTION,
    BLOCKED_DERIVATIVE_EDIT_APPROVAL_BYPASS,
    BLOCKED_DERIVATIVE_EDIT_BRANCH,
    BLOCKED_DERIVATIVE_EDIT_CONTENT_HASH,
    BLOCKED_DERIVATIVE_EDIT_PAYLOAD,
    BLOCKED_DERIVATIVE_EDIT_SOURCE_DRIFT,
    BLOCKED_EXPORT_PREPARATION_APPROVAL_BYPASS,
    BLOCKED_EXPORT_PREPARATION_BRANCH,
    BLOCKED_EXPORT_PREPARATION_EVIDENCE_MISMATCH,
    BLOCKED_EXPORT_PREPARATION_PAYLOAD,
    BLOCKED_EXPORT_PREPARATION_SOURCE_DRIFT,
    BLOCKED_ILLUSTRATION_APPROVAL_BYPASS,
    BLOCKED_ILLUSTRATION_BRANCH,
    BLOCKED_ILLUSTRATION_PAYLOAD,
    BLOCKED_ILLUSTRATION_SOURCE_DRIFT,
    _branch_suggestion_keys,
    _evaluate_branch_visual_bible,
    _evaluate_canon_fork_proposal,
    _evaluate_derivative_draft,
    _evaluate_derivative_edit_proposal,
    _evaluate_export_preparation,
    _evaluate_illustration_anchor_proposal,
    _evaluate_illustration_revision,
)
from ._integrity_narrative import (
    BLOCKED_CHAPTER_ANALYSIS_PAYLOAD,
    BLOCKED_DIGEST_MISUSE,
    BLOCKED_EVALUATION_REPORT,
    BLOCKED_EVALUATION_REPORT_STALE,
    BLOCKED_EVALUATION_SOURCE_SNAPSHOT,
    BLOCKED_EXTERNAL_CANON,
    BLOCKED_FUTURE_HINT,
    BLOCKED_MAINLINE_CANON,
    BLOCKED_OUTLINE_CANON,
    _evaluate_chapter_analysis,
    _evaluate_cited_answer,
    _evaluate_external_evidence,
    _evaluate_skill_evaluation,
    _evaluate_story_arc,
    _evaluate_world_model_candidate,
)
from ._integrity_visual import (
    BLOCKED_PROMPT_APPROVAL_BYPASS,
    BLOCKED_PROMPT_EVIDENCE_MISMATCH,
    BLOCKED_PROMPT_PAYLOAD,
    BLOCKED_SCENE_CANDIDATE_APPROVAL_BYPASS,
    BLOCKED_SCENE_CANDIDATE_EVIDENCE_MISMATCH,
    BLOCKED_SCENE_CANDIDATE_PAYLOAD,
    BLOCKED_SCENE_SPEC_APPROVAL_BYPASS,
    BLOCKED_SCENE_SPEC_EVIDENCE_MISMATCH,
    BLOCKED_SCENE_SPEC_PAYLOAD,
    BLOCKED_SCENE_SPEC_SOURCE_DRIFT,
    BLOCKED_VISUAL_BIBLE_APPROVAL_BYPASS,
    BLOCKED_VISUAL_BIBLE_EVIDENCE_MISMATCH,
    BLOCKED_VISUAL_BIBLE_PAYLOAD,
    _evidence_prefix_matches,
    _evaluate_prompt,
    _evaluate_scene_candidate,
    _evaluate_scene_spec,
    _evaluate_visual_bible,
    _spec_evidence_keys,
)

__all__ = [
    "FORBIDDEN_PROTECTED_KEYS",
    "BLOCKED_SCHEMA",
    "BLOCKED_LINEAGE_OWNER",
    "BLOCKED_LINEAGE_NOVEL",
    "BLOCKED_LINEAGE_SKILL",
    "BLOCKED_LINEAGE_INPUT_HASH",
    "BLOCKED_STATUS",
    "BLOCKED_STALE_REPAIRED_HASH",
    "BLOCKED_TRAIL_INCONSISTENT",
    "BLOCKED_PROTECTED_SYNTHESIS",
    "BLOCKED_NO_EVIDENCE",
    "BLOCKED_UNKNOWN_TYPE",
    "BLOCKED_EXTERNAL_CANON",
    "BLOCKED_CHAPTER_ANALYSIS_PAYLOAD",
    "BLOCKED_DIGEST_MISUSE",
    "BLOCKED_FUTURE_HINT",
    "BLOCKED_OUTLINE_CANON",
    "BLOCKED_MAINLINE_CANON",
    "BLOCKED_EVALUATION_REPORT",
    "BLOCKED_EVALUATION_REPORT_STALE",
    "BLOCKED_EVALUATION_SOURCE_SNAPSHOT",
    "BLOCKED_VISUAL_BIBLE_PAYLOAD",
    "BLOCKED_VISUAL_BIBLE_APPROVAL_BYPASS",
    "BLOCKED_VISUAL_BIBLE_EVIDENCE_MISMATCH",
    "BLOCKED_SCENE_CANDIDATE_PAYLOAD",
    "BLOCKED_SCENE_CANDIDATE_APPROVAL_BYPASS",
    "BLOCKED_SCENE_CANDIDATE_EVIDENCE_MISMATCH",
    "BLOCKED_SCENE_SPEC_PAYLOAD",
    "BLOCKED_SCENE_SPEC_APPROVAL_BYPASS",
    "BLOCKED_SCENE_SPEC_EVIDENCE_MISMATCH",
    "BLOCKED_SCENE_SPEC_SOURCE_DRIFT",
    "BLOCKED_PROMPT_PAYLOAD",
    "BLOCKED_PROMPT_APPROVAL_BYPASS",
    "BLOCKED_PROMPT_EVIDENCE_MISMATCH",
    "BLOCKED_ILLUSTRATION_PAYLOAD",
    "BLOCKED_ILLUSTRATION_APPROVAL_BYPASS",
    "BLOCKED_ILLUSTRATION_SOURCE_DRIFT",
    "BLOCKED_ILLUSTRATION_BRANCH",
    "BLOCKED_ANCHOR_PROPOSAL_PAYLOAD",
    "BLOCKED_ANCHOR_PROPOSAL_APPROVAL_BYPASS",
    "BLOCKED_ANCHOR_PROPOSAL_SOURCE_DRIFT",
    "BLOCKED_ANCHOR_PROPOSAL_BRANCH",
    "BLOCKED_CANON_FORK_PAYLOAD",
    "BLOCKED_CANON_FORK_APPROVAL_BYPASS",
    "BLOCKED_CANON_FORK_DELTA_APPROVAL_BYPASS",
    "BLOCKED_CANON_FORK_SOURCE_DRIFT",
    "BLOCKED_CANON_FORK_BRANCH",
    "BLOCKED_CANON_FORK_DELTA_HASH",
    "BLOCKED_DERIVATIVE_EDIT_PAYLOAD",
    "BLOCKED_DERIVATIVE_EDIT_APPROVAL_BYPASS",
    "BLOCKED_DERIVATIVE_EDIT_SOURCE_DRIFT",
    "BLOCKED_DERIVATIVE_EDIT_CONTENT_HASH",
    "BLOCKED_DERIVATIVE_EDIT_BRANCH",
    "BLOCKED_DERIVATIVE_DRAFT_PAYLOAD",
    "BLOCKED_DERIVATIVE_DRAFT_APPROVAL_BYPASS",
    "BLOCKED_DERIVATIVE_DRAFT_BRANCH",
    "BLOCKED_DERIVATIVE_DRAFT_EVIDENCE_MISMATCH",
    "BLOCKED_DERIVATIVE_DRAFT_SUGGESTION",
    "BLOCKED_BRANCH_VISUAL_BIBLE_PAYLOAD",
    "BLOCKED_BRANCH_VISUAL_BIBLE_APPROVAL_BYPASS",
    "BLOCKED_BRANCH_VISUAL_BIBLE_BRANCH",
    "BLOCKED_BRANCH_VISUAL_BIBLE_SOURCE_DRIFT",
    "BLOCKED_EXPORT_PREPARATION_PAYLOAD",
    "BLOCKED_EXPORT_PREPARATION_APPROVAL_BYPASS",
    "BLOCKED_EXPORT_PREPARATION_BRANCH",
    "BLOCKED_EXPORT_PREPARATION_SOURCE_DRIFT",
    "BLOCKED_EXPORT_PREPARATION_EVIDENCE_MISMATCH",
    "IntegrityDecision",
    "canonical_content_hash",
    "_strip_trail",
    "_first_validation_error",
    "evaluate_integrity",
    "_check_common_lineage",
    "_evaluate_cited_answer",
    "_evaluate_external_evidence",
    "_evaluate_world_model_candidate",
    "_evaluate_chapter_analysis",
    "_evaluate_story_arc",
    "_evaluate_skill_evaluation",
    "_evaluate_visual_bible",
    "_evaluate_scene_candidate",
    "_spec_evidence_keys",
    "_evidence_prefix_matches",
    "_evaluate_scene_spec",
    "_evaluate_prompt",
    "_evaluate_illustration_revision",
    "_evaluate_illustration_anchor_proposal",
    "_evaluate_canon_fork_proposal",
    "_evaluate_derivative_edit_proposal",
    "_branch_suggestion_keys",
    "_evaluate_derivative_draft",
    "_evaluate_branch_visual_bible",
    "_evaluate_export_preparation",
]


def evaluate_integrity(*, envelope: dict[str, Any], run: SkillRun) -> IntegrityDecision:
    """唯一 finalizer 在任何写入前调用的 fail-closed integrity gate。

    :param envelope: agent-service 提交的（已规范化）信封 payload。
    :param run:     当前 SkillRun 行（owner/novel/skill_version/input_hash 权威）。
    """
    artifact_type = envelope.get("type")
    if artifact_type == "cited_answer":
        return _evaluate_cited_answer(envelope, run)
    if artifact_type == "external_evidence":
        return _evaluate_external_evidence(envelope)
    if artifact_type == "world_model_candidate":
        return _evaluate_world_model_candidate(envelope, run)
    if artifact_type == "chapter_analysis":
        return _evaluate_chapter_analysis(envelope, run)
    if artifact_type == "story_arc":
        return _evaluate_story_arc(envelope, run)
    if artifact_type == "skill_evaluation":
        return _evaluate_skill_evaluation(envelope, run)
    if artifact_type == "visual_bible":
        return _evaluate_visual_bible(envelope, run)
    if artifact_type == "scene_candidate":
        return _evaluate_scene_candidate(envelope, run)
    if artifact_type == "scene_spec":
        return _evaluate_scene_spec(envelope, run)
    if artifact_type == "prompt":
        return _evaluate_prompt(envelope, run)
    if artifact_type == "illustration_revision":
        return _evaluate_illustration_revision(envelope, run)
    if artifact_type == "illustration_anchor_proposal":
        return _evaluate_illustration_anchor_proposal(envelope, run)
    if artifact_type == "canon_fork_proposal":
        return _evaluate_canon_fork_proposal(envelope, run)
    if artifact_type == "derivative_edit_proposal":
        return _evaluate_derivative_edit_proposal(envelope, run)
    if artifact_type == "derivative_draft":
        return _evaluate_derivative_draft(envelope, run)
    if artifact_type == "branch_visual_bible":
        return _evaluate_branch_visual_bible(envelope, run)
    if artifact_type == "export_preparation":
        return _evaluate_export_preparation(envelope, run)
    return IntegrityDecision(False, BLOCKED_UNKNOWN_TYPE)
