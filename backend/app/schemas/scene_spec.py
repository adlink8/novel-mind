"""Scene Spec and Prompt Revision strict contracts (Phase 32-01, REQ-VIS-03).

D-32-01..D-32-04: ``SceneSpec`` is the canonical candidate Artifact; provider
prompts are derived, provider-neutral prompt revisions and never become source
truth. This module owns:

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
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCENE_SPEC_SCHEMA_VERSION = "scene-spec.v1"
PROMPT_SCHEMA_VERSION = "prompt-revision.v1"
SCENE_SPEC_ARTIFACT_KIND = "scene_spec"
PROMPT_ARTIFACT_KIND = "prompt_revision"

# Mirrors the ORM vocabulary so schema/model/migration stay byte-identical.
SPEC_DETAIL_KINDS = (
    "subject",
    "action",
    "setting",
    "composition",
    "style",
    "continuity",
)
SPEC_SOURCES = ("evidence", "visual_bible", "user_interpretation")
SPEC_CONSTRAINT_SCOPES = (
    "costume",
    "era",
    "identity",
    "style",
    "physical",
    "continuity",
)
SPEC_UNCERTAINTY_REASONS = (
    "missing_evidence",
    "conflicting_claim",
    "future_spoiler",
    "ambiguous_reference",
)
SPEC_REVIEW_ACTIONS = ("approve", "reject", "supersede", "needs_relink")
SPEC_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)
SPEC_ACTOR_SOURCES = ("human", "machine")
# Ordered canonical prompt sections (RESEARCH A2 / D-32-02). Provider adapters
# may render these sections but never add or reorder canon.
SPEC_SECTION_ORDER = (
    "subject",
    "action",
    "setting",
    "composition",
    "style",
    "continuity",
    "negative_constraints",
    "uncertainties",
)


class StrictSceneSpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecSource(StrEnum):
    EVIDENCE = "evidence"
    VISUAL_BIBLE = "visual_bible"
    USER_INTERPRETATION = "user_interpretation"


class SpecDetailKind(StrEnum):
    SUBJECT = "subject"
    ACTION = "action"
    SETTING = "setting"
    COMPOSITION = "composition"
    STYLE = "style"
    CONTINUITY = "continuity"


class ConstraintScope(StrEnum):
    COSTUME = "costume"
    ERA = "era"
    IDENTITY = "identity"
    STYLE = "style"
    PHYSICAL = "physical"
    CONTINUITY = "continuity"


class UncertaintyReason(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_CLAIM = "conflicting_claim"
    FUTURE_SPOILER = "future_spoiler"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"


class SpecReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SUPERSEDE = "supersede"
    NEEDS_RELINK = "needs_relink"


class SpecReviewState(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class SpecActorSource(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class SceneSpecGateError(ValueError):
    """Fail-closed gate violation while validating a SceneSpec/PromptRevision."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable lineage)
# ---------------------------------------------------------------------------


def canonical_scene_spec_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Evidence / Visual Bible refs and detail contracts
# ---------------------------------------------------------------------------


class SpecEvidenceRef(StrictSceneSpecModel):
    """Primary-text evidence locator; offsets/hash/cutoff are server-verified."""

    evidence_key: str = Field(min_length=1, max_length=180)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str | None = Field(default=None, max_length=2000)
    cutoff_chapter: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_offsets_and_cutoff(self) -> "SpecEvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if self.chapter_number > self.cutoff_chapter:
            raise ValueError(
                "evidence chapter_number must not exceed the spoiler cutoff_chapter"
            )
        return self


class VisualBibleRef(StrictSceneSpecModel):
    """A Visual Bible entity/claim reference; revision hash must match the spec
    the spec was frozen against (deterministic revision lineage, D-32-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_id: str = Field(min_length=1, max_length=180)
    claim_key: str | None = Field(default=None, min_length=1, max_length=180)
    revision_id: int | None = Field(default=None, gt=0)
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def _validate_source_shape(
    *,
    source: SpecSource,
    text_holder: str,
    author: str | None,
    rationale: str | None,
    evidence_refs: list[SpecEvidenceRef],
    visual_bible_refs: list[VisualBibleRef],
) -> None:
    """D-32-02 canonical-source rule for details and negative constraints."""
    if source is SpecSource.EVIDENCE:
        if not evidence_refs:
            raise ValueError(
                f"{text_holder} with source='evidence' requires at least one "
                "evidence ref; an unbacked detail cannot be canon"
            )
    elif source is SpecSource.VISUAL_BIBLE:
        if not visual_bible_refs:
            raise ValueError(
                f"{text_holder} with source='visual_bible' requires at least one "
                "Visual Bible ref (stable_id + revision_hash)"
            )
    else:  # user_interpretation
        if not author:
            raise ValueError(
                f"{text_holder} labeled 'user_interpretation' requires an author"
            )
        if not rationale:
            raise ValueError(
                f"{text_holder} labeled 'user_interpretation' requires a rationale"
            )
        if evidence_refs or visual_bible_refs:
            raise ValueError(
                f"{text_holder} labeled 'user_interpretation' must not carry "
                "evidence/Visual Bible refs (it is interpretation, not canon)"
            )


class SceneDetail(StrictSceneSpecModel):
    """One canonical scene detail; strictly source-bounded (D-32-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    detail_key: str = Field(min_length=1, max_length=180)
    kind: SpecDetailKind
    source: SpecSource
    text: str = Field(min_length=1, max_length=4000)
    author: str | None = Field(default=None, min_length=1, max_length=200)
    rationale: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[SpecEvidenceRef] = Field(default_factory=list, max_length=16)
    visual_bible_refs: list[VisualBibleRef] = Field(default_factory=list, max_length=16)
    spoiler_cutoff: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_source_shape(self) -> "SceneDetail":
        _validate_source_shape(
            source=self.source,
            text_holder=f"detail {self.detail_key!r}",
            author=self.author,
            rationale=self.rationale,
            evidence_refs=self.evidence_refs,
            visual_bible_refs=self.visual_bible_refs,
        )
        return self


class NegativeConstraint(StrictSceneSpecModel):
    """A forbidden/excluded detail; continuity breaks are caught here (D-32-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    constraint_key: str = Field(min_length=1, max_length=180)
    scope: ConstraintScope
    source: SpecSource
    text: str = Field(min_length=1, max_length=4000)
    author: str | None = Field(default=None, min_length=1, max_length=200)
    rationale: str | None = Field(default=None, max_length=2000)
    evidence_refs: list[SpecEvidenceRef] = Field(default_factory=list, max_length=16)
    visual_bible_refs: list[VisualBibleRef] = Field(default_factory=list, max_length=16)
    spoiler_cutoff: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_source_shape(self) -> "NegativeConstraint":
        _validate_source_shape(
            source=self.source,
            text_holder=f"constraint {self.constraint_key!r}",
            author=self.author,
            rationale=self.rationale,
            evidence_refs=self.evidence_refs,
            visual_bible_refs=self.visual_bible_refs,
        )
        return self


class SceneUncertainty(StrictSceneSpecModel):
    """An explicit unresolved item; never a canon detail (D-32-02)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uncertainty_key: str = Field(min_length=1, max_length=180)
    reason: UncertaintyReason
    detail: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# SceneSpec candidate envelope (canonical Artifact)
# ---------------------------------------------------------------------------


class SceneSpecContract(StrictSceneSpecModel):
    """Frozen SceneSpec candidate envelope; every lineage field is mandatory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["scene-spec.v1"] = "scene-spec.v1"
    artifact_kind: Literal["scene_spec"] = "scene_spec"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    spec_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    # Source SceneCandidate lineage (D-32-03): the candidate content hash is the
    # replay key; the DB row id (when assigned) is recorded for owner-scope checks.
    scene_candidate_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scene_candidate_id: int | None = Field(default=None, gt=0)
    # Approved Visual Bible revision the spec was compiled against.
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_bible_revision_id: int | None = Field(default=None, gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_id: str = Field(min_length=1, max_length=120)
    compiler_version: str = Field(min_length=1, max_length=64)
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    details: list[SceneDetail] = Field(default_factory=list, max_length=256)
    negative_constraints: list[NegativeConstraint] = Field(
        default_factory=list, max_length=64
    )
    uncertainties: list[SceneUncertainty] = Field(default_factory=list, max_length=64)
    review_state: SpecReviewState = SpecReviewState.CANDIDATE


# ---------------------------------------------------------------------------
# SceneSpec canonical payloads and replayable hashes
# ---------------------------------------------------------------------------


def detail_canonical_payload(detail: SceneDetail) -> dict[str, Any]:
    return {
        "detail_key": detail.detail_key,
        "kind": detail.kind.value,
        "source": detail.source.value,
        "text": detail.text,
        "author": detail.author,
        "rationale": detail.rationale,
        "evidence_keys": [ref.evidence_key for ref in detail.evidence_refs],
        "visual_bible_refs": [
            {"stable_id": ref.stable_id, "claim_key": ref.claim_key}
            for ref in detail.visual_bible_refs
        ],
        "spoiler_cutoff": detail.spoiler_cutoff,
    }


def constraint_canonical_payload(constraint: NegativeConstraint) -> dict[str, Any]:
    return {
        "constraint_key": constraint.constraint_key,
        "scope": constraint.scope.value,
        "source": constraint.source.value,
        "text": constraint.text,
        "author": constraint.author,
        "rationale": constraint.rationale,
        "evidence_keys": [ref.evidence_key for ref in constraint.evidence_refs],
        "visual_bible_refs": [
            {"stable_id": ref.stable_id, "claim_key": ref.claim_key}
            for ref in constraint.visual_bible_refs
        ],
        "spoiler_cutoff": constraint.spoiler_cutoff,
    }


def scene_spec_content_payload(spec: SceneSpecContract) -> dict[str, Any]:
    """Canonical content payload used for ``content_hash`` replay."""
    return {
        "artifact_kind": SCENE_SPEC_ARTIFACT_KIND,
        "schema_version": SCENE_SPEC_SCHEMA_VERSION,
        "owner_id": spec.owner_id,
        "novel_id": spec.novel_id,
        "spec_key": spec.spec_key,
        "revision_number": spec.revision_number,
        "scene_candidate_hash": spec.scene_candidate_hash,
        "visual_bible_revision_hash": spec.visual_bible_revision_hash,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "schema_hash": spec.schema_hash,
        "compiler_id": spec.compiler_id,
        "compiler_version": spec.compiler_version,
        "policy_hash": spec.policy_hash,
        "config_hash": spec.config_hash,
        "details": [detail_canonical_payload(detail) for detail in spec.details],
        "negative_constraints": [
            constraint_canonical_payload(c) for c in spec.negative_constraints
        ],
        "uncertainties": [
            {
                "uncertainty_key": u.uncertainty_key,
                "reason": u.reason.value,
                "detail": u.detail,
            }
            for u in spec.uncertainties
        ],
    }


def recompute_scene_spec_hash(spec: SceneSpecContract) -> str:
    return canonical_scene_spec_hash(scene_spec_content_payload(spec))


# ---------------------------------------------------------------------------
# Deterministic canonical prompt rendering (provider-neutral)
# ---------------------------------------------------------------------------


def spec_negative_constraint_texts(spec: SceneSpecContract) -> list[str]:
    return [f"{c.scope.value}: {c.text}" for c in spec.negative_constraints]


def spec_uncertainty_texts(spec: SceneSpecContract) -> list[str]:
    return [f"[{u.reason.value}] {u.detail}" for u in spec.uncertainties]


def build_prompt_sections(spec: SceneSpecContract) -> dict[str, str]:
    """Deterministic canonical, provider-neutral ordered prompt sections.

    Positive canon sections only carry ``details``; negative constraints and
    uncertainties are separate, clearly labeled sections so unresolved material
    can never be mistaken for canon (D-32-02).
    """
    sections: dict[str, str] = {}
    for kind in ("subject", "action", "setting", "composition", "style", "continuity"):
        lines = [detail.text for detail in spec.details if detail.kind.value == kind]
        if lines:
            sections[kind] = "\n".join(lines)
    if spec.negative_constraints:
        sections["negative_constraints"] = "\n".join(
            spec_negative_constraint_texts(spec)
        )
    if spec.uncertainties:
        sections["uncertainties"] = "\n".join(spec_uncertainty_texts(spec))
    return sections


# ---------------------------------------------------------------------------
# PromptRevision candidate envelope and lineage (derived Artifact, D-32-01/03)
# ---------------------------------------------------------------------------


class PromptRevisionContract(StrictSceneSpecModel):
    """Frozen compiled-prompt candidate; never source truth (D-32-01).

    The prompt string is derived from a canonical SceneSpec; every lineage field
    records the exact inputs so the prompt is deterministically replayable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["prompt-revision.v1"] = "prompt-revision.v1"
    artifact_kind: Literal["prompt_revision"] = "prompt_revision"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    prompt_key: str = Field(min_length=1, max_length=120)
    revision_number: int = Field(ge=1)
    parent_prompt_revision_id: int | None = Field(default=None, gt=0)
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1, max_length=64)
    adapter_id: str = Field(min_length=1, max_length=120)
    adapter_version: str = Field(min_length=1, max_length=64)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    # Canonical provider-neutral sections; only SPEC_SECTION_ORDER keys allowed.
    sections: dict[str, str] = Field(default_factory=dict)
    negative_constraints: list[str] = Field(default_factory=list, max_length=64)
    uncertainties: list[str] = Field(default_factory=list, max_length=64)
    prompt_text: str = Field(min_length=1, max_length=20000)
    redacted_preview: str | None = Field(default=None, max_length=20000)
    review_state: SpecReviewState = SpecReviewState.CANDIDATE


class PromptArtifactLineage(StrictSceneSpecModel):
    """Deterministic compiler lineage envelope (D-32-03).

    ``input_hash`` covers the canonical provider-neutral inputs (spec lineage +
    sections) and is therefore adapter-independent; ``prompt_hash`` covers the
    rendered adapter output. They must always differ.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiler_version: str = Field(min_length=1, max_length=64)
    adapter_id: str = Field(min_length=1, max_length=120)
    adapter_version: str = Field(min_length=1, max_length=64)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def input_and_output_hashes_differ(self) -> "PromptArtifactLineage":
        if self.input_hash == self.prompt_hash:
            raise ValueError(
                "input_hash must differ from prompt_hash (hash both inputs and output)"
            )
        return self


def prompt_input_payload(
    revision: PromptRevisionContract, spec: SceneSpecContract
) -> dict[str, Any]:
    """Canonical input lineage; adapter-independent (provider-neutral D-32-01)."""
    return {
        "artifact_kind": PROMPT_ARTIFACT_KIND,
        "schema_version": PROMPT_SCHEMA_VERSION,
        "owner_id": revision.owner_id,
        "novel_id": revision.novel_id,
        "scene_spec_hash": recompute_scene_spec_hash(spec),
        "visual_bible_revision_hash": revision.visual_bible_revision_hash,
        "source_snapshot_id": revision.source_snapshot_id,
        "source_snapshot_hash": revision.source_snapshot_hash,
        "cutoff_chapter": revision.cutoff_chapter,
        "schema_hash": revision.schema_hash,
        "prompt_schema_hash": revision.prompt_schema_hash,
        "compiler_version": revision.compiler_version,
        "config_hash": revision.config_hash,
        "sections": revision.sections,
        "negative_constraints": revision.negative_constraints,
        "uncertainties": revision.uncertainties,
    }


def recompute_prompt_input_hash(
    revision: PromptRevisionContract, spec: SceneSpecContract
) -> str:
    return canonical_scene_spec_hash(prompt_input_payload(revision, spec))


def prompt_output_payload(revision: PromptRevisionContract) -> dict[str, Any]:
    """Canonical output payload; adapter- and render-dependent."""
    return {
        "artifact_kind": PROMPT_ARTIFACT_KIND,
        "schema_version": PROMPT_SCHEMA_VERSION,
        "owner_id": revision.owner_id,
        "novel_id": revision.novel_id,
        "prompt_key": revision.prompt_key,
        "revision_number": revision.revision_number,
        "scene_spec_hash": revision.scene_spec_hash,
        "adapter_id": revision.adapter_id,
        "adapter_version": revision.adapter_version,
        "config_hash": revision.config_hash,
        "sections": revision.sections,
        "negative_constraints": revision.negative_constraints,
        "uncertainties": revision.uncertainties,
        "prompt_text": revision.prompt_text,
    }


def recompute_prompt_hash(revision: PromptRevisionContract) -> str:
    return canonical_scene_spec_hash(prompt_output_payload(revision))


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
        raise SceneSpecGateError("prompt source_snapshot_id does not match the SceneSpec")
    if revision.source_snapshot_hash != spec.source_snapshot_hash:
        raise SceneSpecGateError("prompt source_snapshot_hash does not match the SceneSpec")
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
        raise SceneSpecGateError("prompt invented uncertainties not present in the spec")

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

LEGAL_SPEC_REVIEW_TRANSITIONS: dict[
    SpecReviewState, frozenset[SpecReviewAction]
] = {
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
                "unapproved or unresolved SceneSpec cannot enter downstream "
                "consumption"
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
