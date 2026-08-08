"""SceneSpec / PromptRevision 严格契约模型 + canonical hash（D-32-01..D-32-04）。

原 ``scene_spec.py`` 单文件的包化拆分产物。本模块承载：
- 基类 ``StrictSceneSpecModel`` 与枚举词表（``SpecSource`` 等）；
- 严格 wire 契约（``SceneSpecContract`` / ``SceneDetail`` /
  ``NegativeConstraint`` / ``SceneUncertainty`` / ``PromptRevisionContract`` /
  ``PromptArtifactLineage``）；
- canonical 序列化与可回放的 hash 函数（``canonical_scene_spec_hash`` 等）。

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

from app.schemas.scene_spec.constants import (
    PROMPT_ARTIFACT_KIND,
    PROMPT_SCHEMA_VERSION,
    SCENE_SPEC_ARTIFACT_KIND,
    SCENE_SPEC_SCHEMA_VERSION,
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
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
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
