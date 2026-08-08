"""只读 view 模型（candidate-only, 不暴露 canon 之外的内容，Phase 32-01）。

原 ``scene_spec.py`` 单文件的包化拆分产物。本模块承载读信封：
``SceneDetailView`` / ``NegativeConstraintView`` / ``SceneUncertaintyView`` /
``SceneSpecView`` / ``FrozenSceneSpecView`` / ``PromptRevisionView`` /
``FrozenPromptRevisionView``。
"""

from __future__ import annotations

from pydantic import Field, model_validator

from app.schemas.scene_spec.models import (
    ConstraintScope,
    SceneSpecGateError,
    SpecDetailKind,
    SpecReviewState,
    SpecSource,
    StrictSceneSpecModel,
    UncertaintyReason,
)


# ---------------------------------------------------------------------------
# Read envelopes (candidate-only, no canon exposure)
# ---------------------------------------------------------------------------


class SceneDetailView(StrictSceneSpecModel):
    detail_key: str
    kind: SpecDetailKind
    source: SpecSource
    text: str
    author: str | None = None
    rationale: str | None = None
    spoiler_cutoff: int
    evidence_keys: list[str] = Field(default_factory=list)
    visual_bible_stable_ids: list[str] = Field(default_factory=list)


class NegativeConstraintView(StrictSceneSpecModel):
    constraint_key: str
    scope: ConstraintScope
    source: SpecSource
    text: str
    author: str | None = None
    rationale: str | None = None
    spoiler_cutoff: int


class SceneUncertaintyView(StrictSceneSpecModel):
    uncertainty_key: str
    reason: UncertaintyReason
    detail: str


class SceneSpecView(StrictSceneSpecModel):
    """Read envelope: candidate-only spec with evidence/Visual Bible lineage."""

    id: int
    owner_id: int
    novel_id: int
    spec_key: str
    revision_number: int
    scene_candidate_hash: str
    scene_candidate_id: int | None = None
    visual_bible_revision_hash: str
    visual_bible_revision_id: int | None = None
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    schema_hash: str
    compiler_id: str
    compiler_version: str
    policy_hash: str
    content_hash: str
    review_state: SpecReviewState
    details: list[SceneDetailView] = Field(default_factory=list)
    negative_constraints: list[NegativeConstraintView] = Field(default_factory=list)
    uncertainties: list[SceneUncertaintyView] = Field(default_factory=list)


class FrozenSceneSpecView(StrictSceneSpecModel):
    """Approved-only spec envelope for downstream consumers (Phase 33 input)."""

    id: int
    owner_id: int
    novel_id: int
    spec_key: str
    revision_number: int
    scene_candidate_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    content_hash: str
    review_state: SpecReviewState

    @model_validator(mode="after")
    def approved_only(self) -> "FrozenSceneSpecView":
        if self.review_state is not SpecReviewState.APPROVED:
            raise SceneSpecGateError(
                "unapproved or unresolved SceneSpec cannot enter downstream consumption"
            )
        return self


class PromptRevisionView(StrictSceneSpecModel):
    """Read envelope: candidate-only compiled prompt with full lineage."""

    id: int
    owner_id: int
    novel_id: int
    prompt_key: str
    revision_number: int
    parent_prompt_revision_id: int | None = None
    scene_spec_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    schema_hash: str
    prompt_schema_hash: str
    compiler_version: str
    adapter_id: str
    adapter_version: str
    config_hash: str
    input_hash: str
    prompt_hash: str
    sections: dict[str, str] = Field(default_factory=dict)
    negative_constraints: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    redacted_preview: str | None = None
    review_state: SpecReviewState


class FrozenPromptRevisionView(StrictSceneSpecModel):
    """Approved-only prompt envelope; the only valid Phase 33 generation input."""

    id: int
    owner_id: int
    novel_id: int
    prompt_key: str
    revision_number: int
    scene_spec_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    schema_version: str
    prompt_schema_hash: str
    adapter_id: str
    adapter_version: str
    config_hash: str
    input_hash: str
    prompt_hash: str
    sections: dict[str, str] = Field(default_factory=dict)
    negative_constraints: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    prompt_text: str
    review_state: SpecReviewState

    @model_validator(mode="after")
    def approved_only(self) -> "FrozenPromptRevisionView":
        if self.review_state is not SpecReviewState.APPROVED:
            raise SceneSpecGateError(
                "unapproved or unresolved PromptRevision cannot enter generation"
            )
        return self
