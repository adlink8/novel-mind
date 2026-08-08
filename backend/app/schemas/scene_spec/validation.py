"""服务端校验门 + 评审状态机（Phase 32-01, REQ-VIS-03）。

原 ``scene_spec.py`` 单文件的包化拆分产物。本模块承载：
- fail-closed 校验门（``validate_scene_spec_contract`` /
  ``validate_prompt_revision_contract`` / ``build_prompt_lineage``）；
- 追加式、显式、幂等的评审状态机（``review_state_after`` /
  ``SpecReviewEventInput`` / ``validate_review_event``）。

D-32-01..D-32-04: server-side gates that fail closed on provider-specific
fields, unbacked or future-spoiler details, snapshot/cutoff lineage drift,
Visual Bible revision drift, duplicate detail keys, stale prompt lineage and
unresolved details disguised as canon.
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.scene_spec.constants import SPEC_SECTION_ORDER
from app.schemas.scene_spec.models import (
    PromptArtifactLineage,
    PromptRevisionContract,
    SceneSpecContract,
    SceneSpecGateError,
    SpecActorSource,
    SpecEvidenceRef,
    SpecReviewAction,
    SpecReviewState,
    StrictSceneSpecModel,
    VisualBibleRef,
    build_prompt_sections,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    recompute_scene_spec_hash,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
)


# ---------------------------------------------------------------------------
# Server-side gates
# ---------------------------------------------------------------------------


def _validate_evidence_lineage(
    ref: SpecEvidenceRef,
    *,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> None:
    if ref.source_snapshot_id != source_snapshot_id:
        raise SceneSpecGateError(
            f"evidence {ref.evidence_key!r} source_snapshot_id does not match the spec"
        )
    if ref.source_snapshot_hash != source_snapshot_hash:
        raise SceneSpecGateError(
            f"evidence {ref.evidence_key!r} source_snapshot_hash does not match the spec"
        )
    if ref.cutoff_chapter != cutoff_chapter:
        raise SceneSpecGateError(
            f"evidence {ref.evidence_key!r} cutoff_chapter does not match the spec"
        )
    if ref.chapter_number > cutoff_chapter:
        raise SceneSpecGateError(
            f"evidence {ref.evidence_key!r} chapter_number exceeds the spec "
            "spoiler cutoff"
        )


def _validate_visual_bible_lineage(
    ref: VisualBibleRef, *, visual_bible_revision_hash: str
) -> None:
    if ref.revision_hash != visual_bible_revision_hash:
        raise SceneSpecGateError(
            f"visual_bible ref {ref.stable_id!r} revision_hash does not match the "
            "spec's Visual Bible revision; recompile against the current revision"
        )


def validate_scene_spec_contract(spec: SceneSpecContract) -> None:
    """Cross-field gates: unique keys, snapshot/cutoff/VB-revision lineage and
    replayable content hash. Unsupported/future-spoiler details fail closed."""
    detail_keys = [detail.detail_key for detail in spec.details]
    if len(set(detail_keys)) != len(detail_keys):
        raise SceneSpecGateError("duplicate detail_key in spec")

    constraint_keys = [c.constraint_key for c in spec.negative_constraints]
    if len(set(constraint_keys)) != len(constraint_keys):
        raise SceneSpecGateError("duplicate constraint_key in spec")

    uncertainty_keys = [u.uncertainty_key for u in spec.uncertainties]
    if len(set(uncertainty_keys)) != len(uncertainty_keys):
        raise SceneSpecGateError("duplicate uncertainty_key in spec")

    for detail in spec.details:
        if detail.spoiler_cutoff != spec.cutoff_chapter:
            raise SceneSpecGateError(
                f"detail {detail.detail_key!r} spoiler_cutoff does not match the "
                "spec cutoff_chapter"
            )
        for ref in detail.evidence_refs:
            _validate_evidence_lineage(
                ref,
                source_snapshot_id=spec.source_snapshot_id,
                source_snapshot_hash=spec.source_snapshot_hash,
                cutoff_chapter=spec.cutoff_chapter,
            )
        for ref in detail.visual_bible_refs:
            _validate_visual_bible_lineage(
                ref, visual_bible_revision_hash=spec.visual_bible_revision_hash
            )

    for constraint in spec.negative_constraints:
        if constraint.spoiler_cutoff != spec.cutoff_chapter:
            raise SceneSpecGateError(
                f"constraint {constraint.constraint_key!r} spoiler_cutoff does not "
                "match the spec cutoff_chapter"
            )
        for ref in constraint.evidence_refs:
            _validate_evidence_lineage(
                ref,
                source_snapshot_id=spec.source_snapshot_id,
                source_snapshot_hash=spec.source_snapshot_hash,
                cutoff_chapter=spec.cutoff_chapter,
            )
        for ref in constraint.visual_bible_refs:
            _validate_visual_bible_lineage(
                ref, visual_bible_revision_hash=spec.visual_bible_revision_hash
            )

    if recompute_scene_spec_hash(spec) != spec.content_hash:
        raise SceneSpecGateError("scene spec content_hash does not match content")


def validate_prompt_revision_contract(
    revision: PromptRevisionContract, spec: SceneSpecContract
) -> None:
    """A compiled prompt must be exactly reproducible from its SceneSpec.

    Fails closed on a stale SceneSpec hash, Visual Bible revision drift, source
    snapshot/cutoff drift, provider-specific sections, dropped negative
    constraints, unresolved details rendered as canon and non-replayable hashes.
    """
    expected_spec_hash = recompute_scene_spec_hash(spec)
    if revision.scene_spec_hash != expected_spec_hash:
        raise SceneSpecGateError(
            "prompt scene_spec_hash does not match the SceneSpec; recompile against "
            "the current revision"
        )
    if revision.visual_bible_revision_hash != spec.visual_bible_revision_hash:
        raise SceneSpecGateError(
            "prompt visual_bible_revision_hash does not match the SceneSpec"
        )
    if revision.source_snapshot_id != spec.source_snapshot_id:
        raise SceneSpecGateError(
            "prompt source_snapshot_id does not match the SceneSpec"
        )
    if revision.source_snapshot_hash != spec.source_snapshot_hash:
        raise SceneSpecGateError(
            "prompt source_snapshot_hash does not match the SceneSpec"
        )
    if revision.cutoff_chapter != spec.cutoff_chapter:
        raise SceneSpecGateError("prompt cutoff_chapter does not match the SceneSpec")
    if revision.schema_hash != spec.schema_hash:
        raise SceneSpecGateError("prompt schema_hash does not match the SceneSpec")

    unknown_sections = set(revision.sections) - set(SPEC_SECTION_ORDER)
    if unknown_sections:
        raise SceneSpecGateError(
            f"prompt carries provider-specific sections {sorted(unknown_sections)}; "
            "canonical sections are provider-neutral"
        )

    if revision.sections != build_prompt_sections(spec):
        raise SceneSpecGateError(
            "prompt sections do not match the deterministic canonical rendering"
        )
    if revision.negative_constraints != spec_negative_constraint_texts(spec):
        raise SceneSpecGateError("prompt dropped or altered negative constraints")
    if spec.uncertainties:
        if revision.uncertainties != spec_uncertainty_texts(spec):
            raise SceneSpecGateError("prompt did not surface all uncertainties")
    elif revision.uncertainties:
        raise SceneSpecGateError(
            "prompt invented uncertainties not present in the spec"
        )

    if recompute_prompt_input_hash(revision, spec) != revision.input_hash:
        raise SceneSpecGateError("prompt input_hash does not replay from the spec")
    if recompute_prompt_hash(revision) != revision.prompt_hash:
        raise SceneSpecGateError("prompt prompt_hash does not replay from the revision")
    if revision.input_hash == revision.prompt_hash:
        raise SceneSpecGateError("prompt input_hash must differ from prompt_hash")


def build_prompt_lineage(
    revision: PromptRevisionContract, spec: SceneSpecContract
) -> PromptArtifactLineage:
    """Deterministic lineage envelope for a compiled prompt (D-32-03)."""
    return PromptArtifactLineage(
        scene_spec_hash=revision.scene_spec_hash,
        visual_bible_revision_hash=revision.visual_bible_revision_hash,
        source_snapshot_id=revision.source_snapshot_id,
        source_snapshot_hash=revision.source_snapshot_hash,
        cutoff_chapter=revision.cutoff_chapter,
        schema_hash=revision.schema_hash,
        prompt_schema_hash=revision.prompt_schema_hash,
        compiler_version=revision.compiler_version,
        adapter_id=revision.adapter_id,
        adapter_version=revision.adapter_version,
        config_hash=revision.config_hash,
        input_hash=revision.input_hash,
        prompt_hash=revision.prompt_hash,
    )


# ---------------------------------------------------------------------------
# Review actions (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------

SPEC_REVIEW_ACTION_TO_STATE: dict[SpecReviewAction, SpecReviewState] = {
    SpecReviewAction.APPROVE: SpecReviewState.APPROVED,
    SpecReviewAction.REJECT: SpecReviewState.REJECTED,
    SpecReviewAction.NEEDS_RELINK: SpecReviewState.NEEDS_RELINK,
    SpecReviewAction.SUPERSEDE: SpecReviewState.SUPERSEDED,
}

LEGAL_SPEC_REVIEW_TRANSITIONS: dict[SpecReviewState, frozenset[SpecReviewAction]] = {
    SpecReviewState.CANDIDATE: frozenset(
        {
            SpecReviewAction.APPROVE,
            SpecReviewAction.REJECT,
            SpecReviewAction.NEEDS_RELINK,
            SpecReviewAction.SUPERSEDE,
        }
    ),
    SpecReviewState.NEEDS_RELINK: frozenset(
        {
            SpecReviewAction.APPROVE,
            SpecReviewAction.REJECT,
            SpecReviewAction.SUPERSEDE,
        }
    ),
    SpecReviewState.APPROVED: frozenset(
        {
            SpecReviewAction.SUPERSEDE,
            SpecReviewAction.NEEDS_RELINK,
        }
    ),
    SpecReviewState.REJECTED: frozenset({SpecReviewAction.SUPERSEDE}),
    SpecReviewState.SUPERSEDED: frozenset(),
}


def is_legal_spec_review_action(
    state: SpecReviewState | str, action: SpecReviewAction | str
) -> bool:
    current = SpecReviewState(state)
    requested = SpecReviewAction(action)
    return requested in LEGAL_SPEC_REVIEW_TRANSITIONS[current]


def validate_legal_spec_review_action(
    state: SpecReviewState | str, action: SpecReviewAction | str
) -> None:
    current = SpecReviewState(state)
    requested = SpecReviewAction(action)
    if not is_legal_spec_review_action(current, requested):
        raise SceneSpecGateError(
            f"illegal review action {requested.value!r} from state {current.value!r}"
        )


def review_state_after(
    state: SpecReviewState | str, action: SpecReviewAction | str
) -> SpecReviewState:
    current = SpecReviewState(state)
    requested = SpecReviewAction(action)
    validate_legal_spec_review_action(current, requested)
    return SPEC_REVIEW_ACTION_TO_STATE[requested]


class SpecReviewEventInput(StrictSceneSpecModel):
    """One append-only review action candidate; result state is server-derived."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    revision_id: int = Field(gt=0)
    event_key: str = Field(min_length=1, max_length=160)
    action: SpecReviewAction
    actor_source: SpecActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_review_state: SpecReviewState


def validate_review_event(
    event: SpecReviewEventInput,
    *,
    seen_event_keys: frozenset[str] | set[str] | None = None,
) -> SpecReviewState:
    """Validate a review action and return its derived result state.

    Idempotency: a repeated ``event_key`` is rejected here; the durable layer
    enforces the unique event_key constraint so a duplicate action can never
    create a second approval.
    """
    seen = set(seen_event_keys or ())
    if event.event_key in seen:
        raise SceneSpecGateError(
            f"duplicate review event_key {event.event_key!r} (idempotency)"
        )
    return review_state_after(event.from_review_state, event.action)
