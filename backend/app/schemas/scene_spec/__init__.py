"""Scene Spec and Prompt Revision strict contracts (Phase 32-01, REQ-VIS-03).

D-32-01..D-32-04: ``SceneSpec`` is the canonical candidate Artifact; provider
prompts are derived, provider-neutral prompt revisions and never become source
truth. This package owns:

- strict typed wire contracts with ``extra="forbid"`` and frozen immutable
  lineage payloads (``SceneSpecContract`` / ``SceneDetail`` /
  ``NegativeConstraint`` / ``SceneUncertainty`` / ``PromptRevisionContract`` /
  ``PromptArtifactLineage``);
- the closed detail-kind, source, constraint-scope and uncertainty-reason
  vocabularies plus the candidate-only review state machine;
- canonical hash helpers so scene-spec and prompt hashes are byte-replayable;
- server-side gates that fail closed on provider-specific fields, unbacked or
  future-spoiler details, snapshot/cutoff lineage drift, Visual Bible revision
  drift, duplicate detail keys, stale prompt lineage and unresolved details
  disguised as canon.

Canonical source rule (D-32-02): every detail/constraint text is either linked
to primary evidence, linked to an approved Visual Bible revision (stable id +
revision hash), or explicitly labeled ``user_interpretation`` with an author and
rationale. Unsupported or unresolved material lives in ``uncertainties`` only;
it is never rendered into a positive prompt section.

本包是原 ``scene_spec.py`` 单文件的包化拆分，公共 import 面保持零改动：
``from app.schemas.scene_spec import X`` 仍然可用。子模块按族分组：
constants（词汇表常量）、models（Spec/Prompt 契约 + hash 函数）、
validation（服务端校验门 + 评审状态机）、views（只读 view 模型）。
"""

from __future__ import annotations

from app.schemas.scene_spec.constants import (
    PROMPT_ARTIFACT_KIND,
    PROMPT_SCHEMA_VERSION,
    SCENE_SPEC_ARTIFACT_KIND,
    SCENE_SPEC_SCHEMA_VERSION,
    SPEC_ACTOR_SOURCES,
    SPEC_CONSTRAINT_SCOPES,
    SPEC_DETAIL_KINDS,
    SPEC_REVIEW_ACTIONS,
    SPEC_REVIEW_STATES,
    SPEC_SECTION_ORDER,
    SPEC_SOURCES,
    SPEC_UNCERTAINTY_REASONS,
)

from app.schemas.scene_spec.models import (
    ConstraintScope,
    NegativeConstraint,
    PromptArtifactLineage,
    PromptRevisionContract,
    SceneDetail,
    SceneSpecContract,
    SceneSpecGateError,
    SceneUncertainty,
    SpecActorSource,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewAction,
    SpecReviewState,
    SpecSource,
    StrictSceneSpecModel,
    UncertaintyReason,
    VisualBibleRef,
    build_prompt_sections,
    canonical_scene_spec_hash,
    constraint_canonical_payload,
    detail_canonical_payload,
    prompt_input_payload,
    prompt_output_payload,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    recompute_scene_spec_hash,
    scene_spec_content_payload,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
)

from app.schemas.scene_spec.validation import (
    LEGAL_SPEC_REVIEW_TRANSITIONS,
    SPEC_REVIEW_ACTION_TO_STATE,
    SpecReviewEventInput,
    build_prompt_lineage,
    is_legal_spec_review_action,
    review_state_after,
    validate_legal_spec_review_action,
    validate_prompt_revision_contract,
    validate_review_event,
    validate_scene_spec_contract,
)

from app.schemas.scene_spec.views import (
    FrozenPromptRevisionView,
    FrozenSceneSpecView,
    NegativeConstraintView,
    PromptRevisionView,
    SceneDetailView,
    SceneSpecView,
    SceneUncertaintyView,
)

__all__ = [
    # 词汇表常量（constants）
    "SCENE_SPEC_SCHEMA_VERSION",
    "PROMPT_SCHEMA_VERSION",
    "SCENE_SPEC_ARTIFACT_KIND",
    "PROMPT_ARTIFACT_KIND",
    "SPEC_DETAIL_KINDS",
    "SPEC_SOURCES",
    "SPEC_CONSTRAINT_SCOPES",
    "SPEC_UNCERTAINTY_REASONS",
    "SPEC_REVIEW_ACTIONS",
    "SPEC_REVIEW_STATES",
    "SPEC_ACTOR_SOURCES",
    "SPEC_SECTION_ORDER",
    # 基类 + 枚举 + 门错误（models）
    "StrictSceneSpecModel",
    "SpecSource",
    "SpecDetailKind",
    "ConstraintScope",
    "UncertaintyReason",
    "SpecReviewAction",
    "SpecReviewState",
    "SpecActorSource",
    "SceneSpecGateError",
    # Spec / Prompt 契约族 + hash 函数（models）
    "SpecEvidenceRef",
    "VisualBibleRef",
    "SceneDetail",
    "NegativeConstraint",
    "SceneUncertainty",
    "SceneSpecContract",
    "PromptRevisionContract",
    "PromptArtifactLineage",
    "canonical_scene_spec_hash",
    "detail_canonical_payload",
    "constraint_canonical_payload",
    "scene_spec_content_payload",
    "recompute_scene_spec_hash",
    "spec_negative_constraint_texts",
    "spec_uncertainty_texts",
    "build_prompt_sections",
    "prompt_input_payload",
    "recompute_prompt_input_hash",
    "prompt_output_payload",
    "recompute_prompt_hash",
    # 服务端校验门 + 评审状态机（validation）
    "validate_scene_spec_contract",
    "validate_prompt_revision_contract",
    "build_prompt_lineage",
    "SPEC_REVIEW_ACTION_TO_STATE",
    "LEGAL_SPEC_REVIEW_TRANSITIONS",
    "is_legal_spec_review_action",
    "validate_legal_spec_review_action",
    "review_state_after",
    "SpecReviewEventInput",
    "validate_review_event",
    # 只读 view 模型（views）
    "SceneDetailView",
    "NegativeConstraintView",
    "SceneUncertaintyView",
    "SceneSpecView",
    "FrozenSceneSpecView",
    "PromptRevisionView",
    "FrozenPromptRevisionView",
]
