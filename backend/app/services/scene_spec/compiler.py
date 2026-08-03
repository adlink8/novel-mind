"""Evidence-to-Spec compiler service (Phase 32-02, REQ-VIS-03).

D-32-01..D-32-04: a ``SceneSpec`` is the canonical candidate Artifact compiled
deterministically from a frozen ``SceneCandidate`` (Phase 31) plus the approved
Visual Bible revision it was frozen against. This module owns:

- ``compile_scene_spec`` — the pure, replayable evidence-to-spec compiler. It
  assembles subject/action/setting/composition/style/continuity details and
  negative constraints from the candidate coordinates + evidence ranges and
  the Visual Bible entities/claims/style/constraints. Every clause keeps its
  evidence/interpretation provenance, the spec is canonically serialized and
  hashed, and unsupported canon, spoiler leaks and conflicts fail closed or
  become reason-coded unresolved items (never canon, D-32-02).
- ``build_prompt_revision_from_spec`` — the provider-neutral deterministic
  PromptRevision derivation (D-32-01/03) so the whole chain
  Visual Bible → Scene Candidate → SceneSpec → PromptArtifact replays.
- ``SceneSpecService`` — the owner-scoped read/preview/create/diff seam with
  server-side gates: candidate-only frozen sets, approved Visual Bible
  revision revalidation, snapshot/cutoff lineage, append-only persistence with
  idempotent replay, and stale-spec detection when the Visual Bible or source
  snapshot drifts.

Fail-closed rules:
- the candidate chapter/spoiler, evidence snapshot/cutoff and Visual Bible
  manifest hash must match the compile input before any detail is derived;
- a detail whose evidence chapter exceeds the spoiler cutoff or whose Visual
  Bible entity disclosure cutoff is beyond the spec cutoff is never canon;
- conflicting canon claims and absent Visual Bible references become reason-
  coded uncertainties; they never enter a positive prompt section.
- preview never writes and never calls a provider (Phase 32-04 boundary).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.key_scene import (
    SceneCandidate as SceneCandidateRow,
    SceneCandidateSet as SceneCandidateSetRow,
    SceneEvidenceRange as SceneEvidenceRangeRow,
)
from app.models.novel import Novel
from app.models.scene_spec import (
    SceneSpecDetail as SceneSpecDetailRow,
    SceneSpecEvidenceRef as SceneSpecEvidenceRefRow,
    SceneSpecNegativeConstraint as SceneSpecNegativeConstraintRow,
    SceneSpecUncertainty as SceneSpecUncertaintyRow,
    SceneSpecVersion as SceneSpecVersionRow,
)
from app.models.visual_bible import (
    VisualBibleVersion as VisualBibleVersionRow,
    VisualClaim as VisualClaimRow,
    VisualEntity as VisualEntityRow,
    VisualEvidenceRef as VisualEvidenceRefRow,
    VisualReferenceAsset as VisualReferenceAssetRow,
)
from app.schemas.key_scene import (
    SceneCandidateContract,
    SceneEvidenceRange,
    candidate_content_hash,
)
from app.schemas.scene_spec import (
    PROMPT_SCHEMA_VERSION,
    SCENE_SPEC_SCHEMA_VERSION,
    ConstraintScope,
    NegativeConstraint,
    PromptRevisionContract,
    SceneDetail,
    SceneSpecContract,
    SceneSpecView,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewState,
    SpecSource,
    UncertaintyReason,
    VisualBibleRef,
    build_prompt_sections,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    recompute_scene_spec_hash,
    scene_spec_content_payload,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
    validate_prompt_revision_contract,
    validate_scene_spec_contract,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleVersionContract,
    VisualEntityType,
    VisualReviewState,
    validate_version_contract,
)

# Deterministic compiler lineage (D-32-03).
COMPILER_ID = "scene-spec.v1"
COMPILER_VERSION = "1.0.0"

SCENE_SPEC_SCHEMA_HASH = canonical_scene_spec_hash(
    {
        "kind": "scene_spec.schema",
        "schema_version": SCENE_SPEC_SCHEMA_VERSION,
    }
)
PROMPT_SCHEMA_HASH = canonical_scene_spec_hash(
    {
        "kind": "prompt_revision.schema",
        "schema_version": PROMPT_SCHEMA_VERSION,
    }
)
# Default compiler policy for the pure seam; callers may pin their own policy.
SCENE_SPEC_DEFAULT_POLICY_HASH = canonical_scene_spec_hash(
    {
        "kind": "scene_spec.policy",
        "compiler_id": COMPILER_ID,
        "compiler_version": COMPILER_VERSION,
    }
)
# Default provider-neutral adapter lineage for the mock derivation.
MOCK_PROMPT_ADAPTER_ID = "mock-provider"
MOCK_PROMPT_ADAPTER_VERSION = "1.0.0"
MOCK_PROMPT_CONFIG_HASH = canonical_scene_spec_hash(
    {
        "kind": "prompt_revision.config",
        "adapter_id": MOCK_PROMPT_ADAPTER_ID,
        "adapter_version": MOCK_PROMPT_ADAPTER_VERSION,
    }
)


class SceneSpecCompileError(ValueError):
    """Fail-closed compiler gate violation; no spec is ever produced."""


class SceneSpecServiceError(ValueError):
    """Base class for fail-closed scene-spec service errors."""


class SceneSpecNotFound(SceneSpecServiceError):
    """A spec/candidate/version is outside the explicit owner/novel scope."""


class SceneSpecConflict(SceneSpecServiceError):
    """A conflicting retry of an existing immutable spec_key."""


# ---------------------------------------------------------------------------
# Pure compile contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompileUnresolved:
    """One reason-coded unresolved item; never canon (D-32-02)."""

    uncertainty_key: str
    reason: UncertaintyReason
    detail: str


@dataclass(frozen=True)
class SceneSpecCompileInput:
    """Frozen compile request; scope and lineage come from server-verified rows.

    ``candidate`` is the approved frozen candidate contract, ``visual_bible``
    the approved Visual Bible revision contract it was frozen against. The
    explicit hash fields are the replay keys: ``candidate_content_hash`` and
    the Visual Bible manifest hash must revalidate before any derivation.
    """

    owner_id: int
    novel_id: int
    spec_key: str
    revision_number: int = 1
    candidate: SceneCandidateContract = field(default=None)  # type: ignore[assignment]
    scene_candidate_hash: str = field(default="")  # type: ignore[assignment]
    scene_candidate_id: int | None = None
    visual_bible: VisualBibleVersionContract = field(default=None)  # type: ignore[assignment]
    visual_bible_revision_hash: str = field(default="")  # type: ignore[assignment]
    visual_bible_revision_id: int | None = None
    source_snapshot_id: str = field(default="")  # type: ignore[assignment]
    source_snapshot_hash: str = field(default="")  # type: ignore[assignment]
    cutoff_chapter: int = 1
    policy_hash: str = SCENE_SPEC_DEFAULT_POLICY_HASH
    config_hash: str | None = None
    compiler_id: str = COMPILER_ID
    compiler_version: str = COMPILER_VERSION


@dataclass(frozen=True)
class CompiledSceneSpec:
    """Compiler result: the candidate spec plus reason-coded unresolved items."""

    spec: SceneSpecContract
    unresolved: tuple[CompileUnresolved, ...] = ()


# ---------------------------------------------------------------------------
# Pure compile helpers
# ---------------------------------------------------------------------------


def _require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise SceneSpecCompileError(message)


def _gate_candidate_lineage(input_: SceneSpecCompileInput) -> None:
    """Fail closed on candidate/snapshot/cutoff/spoiler lineage drift."""
    candidate = input_.candidate
    _require(candidate.chapter_number <= input_.cutoff_chapter, "candidate chapter_number exceeds the spoiler cutoff")
    _require(candidate.spoiler_cutoff == input_.cutoff_chapter, "candidate spoiler_cutoff does not match the compile cutoff")
    _require(candidate_content_hash(candidate) == input_.scene_candidate_hash, "candidate content hash does not replay")
    for ref in candidate.evidence_ranges:
        _require(
            ref.source_snapshot_id == input_.source_snapshot_id,
            f"candidate evidence {ref.evidence_key!r} source_snapshot_id does not match the compile input",
        )
        _require(
            ref.source_snapshot_hash == input_.source_snapshot_hash,
            f"candidate evidence {ref.evidence_key!r} source_snapshot_hash does not match the compile input",
        )
        _require(
            ref.cutoff_chapter == input_.cutoff_chapter,
            f"candidate evidence {ref.evidence_key!r} cutoff_chapter does not match the compile input",
        )
        _require(
            ref.chapter_number <= input_.cutoff_chapter,
            f"candidate evidence {ref.evidence_key!r} chapter_number exceeds the spoiler cutoff",
        )


def _gate_visual_bible_lineage(input_: SceneSpecCompileInput) -> None:
    """Fail closed on Visual Bible revision drift and contract violations.

    The Visual Bible revision and the key-scene set are each immutable and
    carry their own source-snapshot lineage in their own hash domain; the
    revision's manifest hash is the replay key and the set records the exact
    revision it was frozen against (verified by the service). The spec binds
    its own evidence lineage to the candidate's key-scene snapshot.
    """
    vb = input_.visual_bible
    _require(vb.manifest_hash == input_.visual_bible_revision_hash, "visual bible manifest hash does not match the revision hash")
    _require(vb.cutoff_chapter >= input_.cutoff_chapter, "visual bible cutoff_chapter is below the compile cutoff")
    try:
        validate_version_contract(vb)
    except ValueError as exc:
        raise SceneSpecCompileError(f"visual bible revision failed revalidation: {exc}") from exc


def _find_entity(
    vb: VisualBibleVersionContract, name: str
) -> Any | None:
    """Deterministic entity match by stable_id or entity_key (exact, ordered)."""
    for entity in vb.entities:
        if entity.stable_id == name or entity.entity_key == name:
            return entity
    return None


def _find_entity_by_type(
    vb: VisualBibleVersionContract,
    name: str,
    entity_type: VisualEntityType,
) -> Any | None:
    for entity in vb.entities:
        if entity.entity_type is not entity_type:
            continue
        if entity.stable_id == name or entity.entity_key == name:
            return entity
    return None


def _entity_claims(vb: VisualBibleVersionContract, stable_id: str) -> list[Any]:
    return [claim for claim in vb.claims if claim.entity_stable_id == stable_id]


def _vb_ref(
    vb: VisualBibleVersionContract,
    entity: Any,
    input_: SceneSpecCompileInput,
) -> VisualBibleRef:
    return VisualBibleRef(
        stable_id=entity.stable_id,
        claim_key=None,
        revision_id=input_.visual_bible_revision_id,
        revision_hash=input_.visual_bible_revision_hash,
    )


def _evidence_ref_from_range(
    ref: SceneEvidenceRange,
    input_: SceneSpecCompileInput,
    *,
    namespace: str,
) -> SpecEvidenceRef:
    """Map one candidate evidence range into a spec evidence ref.

    Evidence keys are namespaced per detail/constraint usage so every clause
    keeps its own citation and the spec evidence table stays unique-per-spec.
    """
    return SpecEvidenceRef(
        evidence_key=f"{ref.evidence_key}:{namespace}",
        source_snapshot_id=input_.source_snapshot_id,
        source_snapshot_hash=input_.source_snapshot_hash,
        chapter_id=ref.chapter_id,
        chapter_number=ref.chapter_number,
        source_start=ref.source_start,
        source_end=ref.source_end,
        content_hash=ref.content_hash,
        excerpt=ref.excerpt,
        cutoff_chapter=input_.cutoff_chapter,
    )


def _render_style_profile(style_profile: Mapping[str, Any]) -> str:
    """Deterministic sorted-key rendering of the Visual Bible style profile."""
    lines: list[str] = []
    for key in sorted(style_profile):
        value = style_profile[key]
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        lines.append(f"{key}: {rendered}")
    return "\n".join(lines)


def _compile_constraint(
    entry: Mapping[str, Any],
    input_: SceneSpecCompileInput,
    *,
    index: int,
) -> NegativeConstraint:
    """One negative constraint from the Visual Bible constraints list.

    The expected entry shape is ``{constraint_key, scope, text, source,
    author, rationale, evidence}``. Entries that cannot be deterministically
    rendered fail closed (no unsupported constraint can be disguised as canon).
    """
    constraint_key = entry.get("constraint_key") or entry.get("key")
    _require(isinstance(constraint_key, str) and constraint_key, f"constraint[{index}] has no constraint_key")
    scope_raw = entry.get("scope")
    _require(isinstance(scope_raw, str) and scope_raw in ConstraintScope._value2member_map_, f"constraint {constraint_key!r} has unsupported scope")
    text = entry.get("text")
    _require(isinstance(text, str) and text, f"constraint {constraint_key!r} has no text")
    source_raw = entry.get("source") or SpecSource.VISUAL_BIBLE.value
    _require(source_raw in SpecSource._value2member_map_, f"constraint {constraint_key!r} has unsupported source")
    source = SpecSource(source_raw)

    author = entry.get("author") if isinstance(entry.get("author"), str) else None
    rationale = entry.get("rationale") if isinstance(entry.get("rationale"), str) else None
    evidence_raw = entry.get("evidence")
    primary = (
        input_.candidate.evidence_ranges[0]
        if input_.candidate.evidence_ranges
        else None
    )
    evidence: list[SpecEvidenceRef] = []
    if isinstance(evidence_raw, list):
        for i, item in enumerate(evidence_raw):
            if not isinstance(item, Mapping):
                raise SceneSpecCompileError(f"constraint {constraint_key!r} evidence[{i}] is not an object")
            ref = SpecEvidenceRef.model_validate(dict(item))
            evidence.append(
                SpecEvidenceRef(
                    evidence_key=f"{constraint_key}:ev:{i}",
                    source_snapshot_id=input_.source_snapshot_id,
                    source_snapshot_hash=input_.source_snapshot_hash,
                    chapter_id=ref.chapter_id,
                    chapter_number=ref.chapter_number,
                    source_start=ref.source_start,
                    source_end=ref.source_end,
                    content_hash=ref.content_hash,
                    excerpt=ref.excerpt,
                    cutoff_chapter=ref.cutoff_chapter,
                )
            )
    elif primary is not None and source is SpecSource.EVIDENCE:
        evidence = [_evidence_ref_from_range(primary, input_, namespace=f"constraint:{constraint_key}")]

    vb_refs: list[VisualBibleRef] = []
    if source is SpecSource.VISUAL_BIBLE:
        vb_refs = [
            VisualBibleRef(
                stable_id=f"constraint:{constraint_key}",
                claim_key=None,
                revision_id=input_.visual_bible_revision_id,
                revision_hash=input_.visual_bible_revision_hash,
            )
        ]

    return NegativeConstraint(
        constraint_key=constraint_key,
        scope=ConstraintScope(scope_raw),
        source=source,
        text=text,
        author=author,
        rationale=rationale,
        evidence_refs=evidence,
        visual_bible_refs=vb_refs,
        spoiler_cutoff=input_.cutoff_chapter,
    )


# ---------------------------------------------------------------------------
# Pure compiler
# ---------------------------------------------------------------------------


def compile_scene_spec(input_: SceneSpecCompileInput) -> CompiledSceneSpec:
    """Deterministically compile one SceneSpec candidate (D-32-02/03).

    Returns a spec (with reason-coded uncertainties) or raises
    ``SceneSpecCompileError`` when lineage/spoiler/unsupported details fail
    closed. Unresolved items only ever populate ``uncertainties``; they never
    render into a positive prompt section.
    """
    _gate_candidate_lineage(input_)
    _gate_visual_bible_lineage(input_)

    vb = input_.visual_bible
    cutoff = input_.cutoff_chapter
    details: list[SceneDetail] = []
    constraints: list[NegativeConstraint] = []
    unresolved: list[CompileUnresolved] = []
    matched_refs: list[VisualBibleRef] = []
    matched_names: list[str] = []

    # ---- subject / continuity: cast members matched to Visual Bible entities
    for name in input_.candidate.coordinates.cast:
        entity = _find_entity(vb, name)
        if entity is None:
            unresolved.append(
                CompileUnresolved(
                    f"subject-missing:{name}",
                    UncertaintyReason.MISSING_EVIDENCE,
                    f"cast member {name!r} has no Visual Bible entity",
                )
            )
            continue
        if entity.disclosure_cutoff > cutoff:
            unresolved.append(
                CompileUnresolved(
                    f"subject-spoiler:{entity.stable_id}",
                    UncertaintyReason.FUTURE_SPOILER,
                    f"entity {entity.stable_id!r} disclosure_cutoff {entity.disclosure_cutoff} exceeds the spec cutoff {cutoff}",
                )
            )
            continue

        claims = _entity_claims(vb, entity.stable_id)
        canon_descriptions = {
            claim.description
            for claim in claims
            if claim.authority is VisualAuthority.CANON_FACT
        }
        if len(canon_descriptions) > 1:
            unresolved.append(
                CompileUnresolved(
                    f"subject-conflict:{entity.stable_id}",
                    UncertaintyReason.CONFLICTING_CLAIM,
                    f"entity {entity.stable_id!r} has conflicting canon_fact claims; detail withheld",
                )
            )
            continue

        ref = _vb_ref(vb, entity, input_)
        matched_refs.append(ref)
        matched_names.append(entity.stable_id)

        if entity.authority is VisualAuthority.USER_INTERPRETATION:
            interp = next(
                (c for c in claims if c.authority is VisualAuthority.USER_INTERPRETATION),
                None,
            )
            if interp is None or not interp.author or not interp.rationale:
                unresolved.append(
                    CompileUnresolved(
                        f"subject-interpretation:{entity.stable_id}",
                        UncertaintyReason.AMBIGUOUS_REFERENCE,
                        f"interpretation entity {entity.stable_id!r} lacks author/rationale; detail withheld",
                    )
                )
                continue
            details.append(
                SceneDetail(
                    detail_key=f"subject:{entity.stable_id}",
                    kind=SpecDetailKind.SUBJECT,
                    source=SpecSource.USER_INTERPRETATION,
                    text=interp.description,
                    author=interp.author,
                    rationale=interp.rationale,
                    spoiler_cutoff=cutoff,
                )
            )
            continue

        details.append(
            SceneDetail(
                detail_key=f"subject:{entity.stable_id}",
                kind=SpecDetailKind.SUBJECT,
                source=SpecSource.VISUAL_BIBLE,
                text=entity.description,
                visual_bible_refs=[ref],
                spoiler_cutoff=cutoff,
            )
        )

    # ---- composition: cast members whose entity is a prop/actor item or faction
    for name in input_.candidate.coordinates.cast:
        entity = _find_entity(vb, name)
        if entity is None or entity.entity_type not in (
            VisualEntityType.ITEM,
            VisualEntityType.FACTION,
        ):
            continue
        if entity.disclosure_cutoff > cutoff:
            continue
        ref = _vb_ref(vb, entity, input_)
        matched_refs.append(ref)
        matched_names.append(entity.stable_id)
        details.append(
            SceneDetail(
                detail_key=f"composition:{entity.stable_id}",
                kind=SpecDetailKind.COMPOSITION,
                source=SpecSource.VISUAL_BIBLE,
                text=entity.description,
                visual_bible_refs=[ref],
                spoiler_cutoff=cutoff,
            )
        )

    # ---- setting: place and time coordinates (VB place entity when present)
    place = input_.candidate.coordinates.place
    if place:
        entity = _find_entity_by_type(vb, place, VisualEntityType.PLACE)
        if entity is not None and entity.disclosure_cutoff <= cutoff:
            ref = _vb_ref(vb, entity, input_)
            matched_refs.append(ref)
            matched_names.append(entity.stable_id)
            details.append(
                SceneDetail(
                    detail_key=f"setting:place:{entity.stable_id}",
                    kind=SpecDetailKind.SETTING,
                    source=SpecSource.VISUAL_BIBLE,
                    text=entity.description,
                    visual_bible_refs=[ref],
                    spoiler_cutoff=cutoff,
                )
            )
        else:
            if entity is not None and entity.disclosure_cutoff > cutoff:
                unresolved.append(
                    CompileUnresolved(
                        f"setting-spoiler:{entity.stable_id}",
                        UncertaintyReason.FUTURE_SPOILER,
                        f"place entity {entity.stable_id!r} disclosure_cutoff exceeds the spec cutoff",
                    )
                )
            primary = input_.candidate.evidence_ranges[0] if input_.candidate.evidence_ranges else None
            if primary is not None:
                details.append(
                    SceneDetail(
                        detail_key=f"setting:place:{place}",
                        kind=SpecDetailKind.SETTING,
                        source=SpecSource.EVIDENCE,
                        text=f"地点：{place}",
                        evidence_refs=[
                            _evidence_ref_from_range(primary, input_, namespace=f"setting:place:{place}")
                        ],
                        spoiler_cutoff=cutoff,
                    )
                )
            else:
                unresolved.append(
                    CompileUnresolved(
                        f"setting-place:{place}",
                        UncertaintyReason.MISSING_EVIDENCE,
                        f"place {place!r} has no Visual Bible entity or candidate evidence",
                    )
                )

    time = input_.candidate.coordinates.time
    if time:
        primary = input_.candidate.evidence_ranges[0] if input_.candidate.evidence_ranges else None
        if primary is not None:
            details.append(
                SceneDetail(
                    detail_key=f"setting:time:{time}",
                    kind=SpecDetailKind.SETTING,
                    source=SpecSource.EVIDENCE,
                    text=f"时间：{time}",
                    evidence_refs=[
                        _evidence_ref_from_range(primary, input_, namespace=f"setting:time:{time}")
                    ],
                    spoiler_cutoff=cutoff,
                )
            )

    # ---- action: the candidate's primary evidence excerpt (evidence authority)
    primary = input_.candidate.evidence_ranges[0] if input_.candidate.evidence_ranges else None
    if primary is not None and primary.excerpt:
        details.append(
            SceneDetail(
                detail_key=f"action:{input_.candidate.scene_id}",
                kind=SpecDetailKind.ACTION,
                source=SpecSource.EVIDENCE,
                text=primary.excerpt,
                evidence_refs=[
                    _evidence_ref_from_range(primary, input_, namespace="action")
                ],
                spoiler_cutoff=cutoff,
            )
        )
    else:
        unresolved.append(
            CompileUnresolved(
                f"action:{input_.candidate.scene_id}",
                UncertaintyReason.MISSING_EVIDENCE,
                "candidate has no excerpted primary evidence for an action clause",
            )
        )

    # ---- style: deterministic rendering of the approved style profile
    if vb.style_profile:
        details.append(
            SceneDetail(
                detail_key="style:profile",
                kind=SpecDetailKind.STYLE,
                source=SpecSource.VISUAL_BIBLE,
                text=_render_style_profile(vb.style_profile),
                visual_bible_refs=[
                    VisualBibleRef(
                        stable_id="style_profile",
                        claim_key=None,
                        revision_id=input_.visual_bible_revision_id,
                        revision_hash=input_.visual_bible_revision_hash,
                    )
                ],
                spoiler_cutoff=cutoff,
            )
        )

    # ---- continuity: stable Visual Bible IDs carried into the prompt so
    # downstream adapters keep character/location identities (D-32-03).
    if matched_names:
        details.append(
            SceneDetail(
                detail_key="continuity:scene-entities",
                kind=SpecDetailKind.CONTINUITY,
                source=SpecSource.VISUAL_BIBLE,
                text="场景实体 stable IDs: " + ", ".join(sorted(set(matched_names))),
                visual_bible_refs=list(matched_refs),
                spoiler_cutoff=cutoff,
            )
        )

    # ---- negative constraints from the Visual Bible constraints list
    for index, entry in enumerate(vb.constraints or []):
        constraints.append(_compile_constraint(entry, input_, index=index))

    uncertainties: list[SceneUncertainty] = []
    for item in unresolved:
        uncertainties.append(
            SceneUncertainty(
                uncertainty_key=item.uncertainty_key,
                reason=item.reason,
                detail=item.detail,
            )
        )

    spec = SceneSpecContract(
        schema_version=SCENE_SPEC_SCHEMA_VERSION,
        artifact_kind="scene_spec",
        owner_id=input_.owner_id,
        novel_id=input_.novel_id,
        spec_key=input_.spec_key,
        revision_number=input_.revision_number,
        scene_candidate_hash=input_.scene_candidate_hash,
        scene_candidate_id=input_.scene_candidate_id,
        visual_bible_revision_hash=input_.visual_bible_revision_hash,
        visual_bible_revision_id=input_.visual_bible_revision_id,
        source_snapshot_id=input_.source_snapshot_id,
        source_snapshot_hash=input_.source_snapshot_hash,
        cutoff_chapter=input_.cutoff_chapter,
        schema_hash=SCENE_SPEC_SCHEMA_HASH,
        compiler_id=input_.compiler_id,
        compiler_version=input_.compiler_version,
        policy_hash=input_.policy_hash,
        config_hash=input_.config_hash,
        content_hash="0" * 64,
        details=details,
        negative_constraints=constraints,
        uncertainties=uncertainties,
        review_state=SpecReviewState.CANDIDATE,
    )
    spec = spec.model_copy(update={"content_hash": recompute_scene_spec_hash(spec)})

    try:
        validate_scene_spec_contract(spec)
    except ValueError as exc:
        raise SceneSpecCompileError(f"compiled spec failed its own contract gate: {exc}") from exc

    return CompiledSceneSpec(spec=spec, unresolved=tuple(unresolved))


# ---------------------------------------------------------------------------
# Deterministic PromptRevision derivation (provider-neutral, D-32-01/03)
# ---------------------------------------------------------------------------


def _render_prompt_text(sections: Mapping[str, str]) -> str:
    from app.schemas.scene_spec import SPEC_SECTION_ORDER

    parts: list[str] = []
    for key in SPEC_SECTION_ORDER:
        if key in sections:
            parts.append(f"[{key}]\n{sections[key]}")
    return "\n\n".join(parts)


def build_prompt_revision_from_spec(
    spec: SceneSpecContract,
    *,
    prompt_key: str,
    revision_number: int = 1,
    adapter_id: str = MOCK_PROMPT_ADAPTER_ID,
    adapter_version: str = MOCK_PROMPT_ADAPTER_VERSION,
    config_hash: str = MOCK_PROMPT_CONFIG_HASH,
) -> PromptRevisionContract:
    """Deterministically derive the provider-neutral PromptRevision candidate.

    The prompt string is never the authority (D-32-01): this renders the
    canonical sections in SPEC_SECTION_ORDER, keeps negative constraints and
    uncertainties separated, and replays both ``input_hash`` (adapter-neutral)
    and ``prompt_hash`` (rendered output). No provider is called.
    """
    sections = build_prompt_sections(spec)
    negative = spec_negative_constraint_texts(spec)
    uncertainties = spec_uncertainty_texts(spec)
    prompt_text = _render_prompt_text(sections)
    revision = PromptRevisionContract(
        schema_version=PROMPT_SCHEMA_VERSION,
        artifact_kind="prompt_revision",
        owner_id=spec.owner_id,
        novel_id=spec.novel_id,
        prompt_key=prompt_key,
        revision_number=revision_number,
        parent_prompt_revision_id=None,
        scene_spec_hash=spec.content_hash,
        visual_bible_revision_hash=spec.visual_bible_revision_hash,
        source_snapshot_id=spec.source_snapshot_id,
        source_snapshot_hash=spec.source_snapshot_hash,
        cutoff_chapter=spec.cutoff_chapter,
        schema_hash=spec.schema_hash,
        prompt_schema_hash=PROMPT_SCHEMA_HASH,
        compiler_version=spec.compiler_version,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        config_hash=config_hash,
        input_hash="0" * 64,
        prompt_hash="0" * 64,
        sections=dict(sections),
        negative_constraints=negative,
        uncertainties=uncertainties,
        prompt_text=prompt_text,
        redacted_preview=prompt_text,
        review_state=SpecReviewState.CANDIDATE,
    )
    revision = revision.model_copy(
        update={
            "input_hash": recompute_prompt_input_hash(revision, spec),
            "prompt_hash": recompute_prompt_hash(revision),
        }
    )
    try:
        validate_prompt_revision_contract(revision, spec)
    except ValueError as exc:
        raise SceneSpecCompileError(f"derived prompt failed its own contract gate: {exc}") from exc
    return revision


# ---------------------------------------------------------------------------
# Owner-scoped service seam (preview / create / read / diff)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneSpecPreviewRequest:
    """Server-side preview/create request; scope comes from the caller path."""

    spec_key: str
    candidate_set_id: int
    candidate_key: str
    visual_bible_version_id: int
    source_snapshot_id: str
    revision_number: int = 1
    policy_hash: str = SCENE_SPEC_DEFAULT_POLICY_HASH
    config_hash: str | None = None


@dataclass(frozen=True)
class SceneSpecPreviewResult:
    """Preview outcome: no persistence and no provider call (Phase 32-04)."""

    spec: SceneSpecContract
    view: SceneSpecView
    unresolved: tuple[CompileUnresolved, ...] = ()
    provider_calls: int = 0


@dataclass(frozen=True)
class PersistedSceneSpec:
    """Create outcome: the persisted version row plus replay flag."""

    version: SceneSpecVersionRow
    view: SceneSpecView
    replayed: bool = False


@dataclass(frozen=True)
class SceneSpecDiffSection:
    """One canonical section whose rendering changed between two compiles."""

    section_key: str
    original: str | None = None
    current: str | None = None


@dataclass(frozen=True)
class SceneSpecDiffResult:
    """Deterministic recompile diff + stale marker (D-32-03)."""

    original_spec_hash: str
    current_spec_hash: str
    stale: bool
    same: bool
    changed_sections: tuple[SceneSpecDiffSection, ...] = ()


def _reconstruct_candidate(
    set_row: SceneCandidateSetRow,
    candidate_row: SceneCandidateRow,
    evidence_rows: Sequence[SceneEvidenceRangeRow],
) -> SceneCandidateContract:
    """Reconstruct the immutable SceneCandidateContract from persisted rows."""
    refs: list[SceneEvidenceRange] = []
    for row in evidence_rows:
        if row.candidate_id != candidate_row.id:
            continue
        refs.append(
            SceneEvidenceRange(
                evidence_key=row.evidence_key,
                source_snapshot_id=row.source_snapshot_id,
                source_snapshot_hash=row.source_snapshot_hash,
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=row.excerpt,
                cutoff_chapter=row.cutoff_chapter,
            )
        )
    return SceneCandidateContract(
        candidate_key=candidate_row.candidate_key,
        candidate_order=candidate_row.candidate_order,
        scene_id=candidate_row.scene_id,
        chapter_id=candidate_row.chapter_id,
        chapter_number=candidate_row.chapter_number,
        source_start=candidate_row.source_start,
        source_end=candidate_row.source_end,
        source_hash=candidate_row.source_hash,
        coordinates=candidate_row.coordinates,
        spoiler_cutoff=candidate_row.spoiler_cutoff,
        salience_reasons=candidate_row.salience_reasons or [],
        score_total=candidate_row.score_total,
        score_breakdown=candidate_row.score_breakdown or {},
        diversity_key=candidate_row.diversity_key,
        detector_id=candidate_row.detector_id,
        detector_version=candidate_row.detector_version,
        policy_hash=candidate_row.policy_hash,
        evidence_ranges=refs,
        heuristic_signal=candidate_row.heuristic_signal,
        review_state=candidate_row.review_state,
    )


class SceneSpecService:
    """Owner-scoped SceneSpec read/preview/create/diff seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ compile seam

    async def compile_input(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> SceneSpecCompileInput:
        """Server-side revalidation before the pure compiler runs.

        Verifies, for the requesting owner:
        - novel ownership, the frozen candidate set and the approved candidate,
        - the approved Visual Bible revision the set was frozen against,
        - snapshot/cutoff lineage consistency between the set and the revision.
        """
        novel = await self._session.scalar(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if novel is None:
            raise SceneSpecNotFound("novel is not in the explicit owner/novel scope")

        set_row = await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == request.candidate_set_id,
            )
        )
        if set_row is None or set_row.review_state != VisualReviewState.APPROVED.value:
            raise SceneSpecNotFound(
                "frozen candidate set not found in the explicit owner/novel scope"
            )

        candidate_row = await self._session.scalar(
            select(SceneCandidateRow).where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.set_id == set_row.id,
                SceneCandidateRow.candidate_key == request.candidate_key,
            )
        )
        if candidate_row is None:
            raise SceneSpecNotFound(
                f"candidate {request.candidate_key!r} is not in the frozen set"
            )

        evidence_rows = (
            await self._session.scalars(
                select(SceneEvidenceRangeRow).where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_row.id,
                )
            )
        ).all()

        vb_version = await self._session.scalar(
            select(VisualBibleVersionRow).where(
                VisualBibleVersionRow.owner_id == owner_id,
                VisualBibleVersionRow.novel_id == novel_id,
                VisualBibleVersionRow.id == request.visual_bible_version_id,
            )
        )
        if (
            vb_version is None
            or vb_version.review_state != VisualReviewState.APPROVED.value
        ):
            raise SceneSpecNotFound(
                "approved Visual Bible revision not found in the explicit owner/novel scope"
            )
        if (
            set_row.approved_visual_bible_revision_id != vb_version.id
            or set_row.approved_visual_bible_revision_hash != vb_version.manifest_hash
        ):
            raise SceneSpecServiceError(
                "candidate set was not frozen against this Visual Bible revision; "
                "re-freeze the set against the current approved revision"
            )

        candidate = _reconstruct_candidate(set_row, candidate_row, evidence_rows)
        visual_bible = await self._load_visual_bible_contract(vb_version)

        return SceneSpecCompileInput(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=request.spec_key,
            revision_number=request.revision_number,
            candidate=candidate,
            scene_candidate_hash=candidate_content_hash(candidate),
            scene_candidate_id=candidate_row.id,
            visual_bible=visual_bible,
            visual_bible_revision_hash=vb_version.manifest_hash,
            visual_bible_revision_id=vb_version.id,
            source_snapshot_id=request.source_snapshot_id,
            source_snapshot_hash=set_row.source_snapshot_hash,
            cutoff_chapter=set_row.cutoff_chapter,
            policy_hash=request.policy_hash,
            config_hash=request.config_hash,
        )

    # ---------------------------------------------------------------- preview

    async def preview(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> SceneSpecPreviewResult:
        """Compile a preview without persisting anything and without a provider."""
        compile_input = await self.compile_input(
            owner_id=owner_id, novel_id=novel_id, request=request
        )
        compiled = compile_scene_spec(compile_input)
        spec = compiled.spec
        # No persistence; the view is built from the contract.
        return SceneSpecPreviewResult(
            spec=spec,
            view=self._view_from_contract(spec),
            unresolved=compiled.unresolved,
            provider_calls=0,
        )

    # ------------------------------------------------------------------ create

    async def create(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> PersistedSceneSpec:
        """Compile and persist one immutable candidate spec (append-only, replay)."""
        compile_input = await self.compile_input(
            owner_id=owner_id, novel_id=novel_id, request=request
        )
        compiled = compile_scene_spec(compile_input)
        spec = compiled.spec

        existing = await self._spec(owner_id=owner_id, novel_id=novel_id, spec_key=spec.spec_key)
        if existing is not None:
            if existing.content_hash == spec.content_hash:
                return PersistedSceneSpec(
                    version=existing,
                    view=await self._view_from_rows(
                        owner_id=owner_id, novel_id=novel_id, spec=existing
                    ),
                    replayed=True,
                )
            raise SceneSpecConflict(
                f"conflicting spec retry: spec_key {spec.spec_key!r} already exists "
                "with different immutable content"
            )

        projection_hash = spec.content_hash
        version_row = SceneSpecVersionRow(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_id=spec.scene_candidate_id,
            scene_candidate_hash=spec.scene_candidate_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            review_state=SpecReviewState.CANDIDATE.value,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            config_hash=spec.config_hash,
            content_hash=spec.content_hash,
            canonical_payload=scene_spec_content_payload(spec),
            canonical_payload_hash=projection_hash,
            idempotency_key=self._version_idempotency_key(spec),
            projection_hash=projection_hash,
        )
        self._session.add(version_row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._spec(owner_id=owner_id, novel_id=novel_id, spec_key=spec.spec_key)
            if existing is None:
                raise SceneSpecConflict(
                    "scene spec race: existing row not found after rollback"
                )
            if existing.content_hash != spec.content_hash:
                raise SceneSpecConflict(
                    f"conflicting spec retry: spec_key {spec.spec_key!r} already "
                    "exists with different immutable content"
                )
            return PersistedSceneSpec(
                version=existing,
                view=await self._view_from_rows(
                    owner_id=owner_id, novel_id=novel_id, spec=existing
                ),
                replayed=True,
            )

        await self._persist_content(owner_id=owner_id, novel_id=novel_id, spec=spec, version_row=version_row)
        await self._session.flush()
        return PersistedSceneSpec(
            version=version_row,
            view=await self._view_from_rows(
                owner_id=owner_id, novel_id=novel_id, spec=version_row
            ),
            replayed=False,
        )

    # ------------------------------------------------------------ read seams

    async def list(
        self, *, owner_id: int, novel_id: int
    ) -> list[SceneSpecView]:
        rows = (
            await self._session.scalars(
                select(SceneSpecVersionRow)
                .where(
                    SceneSpecVersionRow.owner_id == owner_id,
                    SceneSpecVersionRow.novel_id == novel_id,
                )
                .order_by(SceneSpecVersionRow.id.asc())
            )
        ).all()
        return [
            await self._view_from_rows(owner_id=owner_id, novel_id=novel_id, spec=row)
            for row in rows
        ]

    async def load(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> tuple[SceneSpecView, bool]:
        """Return (view, stale). ``stale`` means the Visual Bible revision or the
        source snapshot the spec was compiled against no longer matches the
        novel's current approved revision / snapshot (D-32-03)."""
        spec = await self._spec_by_id(owner_id=owner_id, novel_id=novel_id, spec_id=spec_id)
        if spec is None:
            raise SceneSpecNotFound(
                "scene spec not found in the explicit owner/novel scope"
            )
        view = await self._view_from_rows(owner_id=owner_id, novel_id=novel_id, spec=spec)
        stale = await self._is_stale(owner_id=owner_id, novel_id=novel_id, spec=spec)
        return view, stale

    async def diff(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> SceneSpecDiffResult:
        """Recompile the same candidate against the current approved revision and
        diff the deterministic canonical sections. A changed Visual Bible or
        source snapshot marks the stored spec stale and shows the drift."""
        spec = await self._spec_by_id(owner_id=owner_id, novel_id=novel_id, spec_id=spec_id)
        if spec is None:
            raise SceneSpecNotFound(
                "scene spec not found in the explicit owner/novel scope"
            )

        current_hash, _ = await self._current_snapshot(owner_id=owner_id, novel_id=novel_id)
        latest_vb = await self._latest_approved_version(
            owner_id=owner_id, novel_id=novel_id
        )
        stale = (
            spec.visual_bible_revision_hash != latest_vb.manifest_hash
            or spec.source_snapshot_hash != current_hash
        )
        original_sections = self._sections_from_payload(spec.canonical_payload)

        if latest_vb.id == spec.visual_bible_revision_id and current_hash == spec.source_snapshot_hash:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=False,
                same=True,
                changed_sections=(),
            )

        # Re-run the frozen candidate compile against the current revision.
        if spec.scene_candidate_id is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        candidate_row = await self._session.scalar(
            select(SceneCandidateRow).where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.id == spec.scene_candidate_id,
            )
        )
        if candidate_row is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        set_row = await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == candidate_row.set_id,
            )
        )
        if set_row is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        evidence_rows = (
            await self._session.scalars(
                select(SceneEvidenceRangeRow).where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_row.id,
                )
            )
        ).all()
        candidate = _reconstruct_candidate(set_row, candidate_row, evidence_rows)
        visual_bible = await self._load_visual_bible_contract(latest_vb)
        diff_input = SceneSpecCompileInput(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            candidate=candidate,
            scene_candidate_hash=candidate_content_hash(candidate),
            scene_candidate_id=candidate_row.id,
            visual_bible=visual_bible,
            visual_bible_revision_hash=latest_vb.manifest_hash,
            visual_bible_revision_id=latest_vb.id,
            source_snapshot_id=set_row.source_snapshot_id,
            source_snapshot_hash=set_row.source_snapshot_hash,
            cutoff_chapter=set_row.cutoff_chapter,
            policy_hash=spec.policy_hash,
            config_hash=spec.config_hash,
        )
        try:
            current = compile_scene_spec(diff_input).spec
        except (SceneSpecCompileError,):
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )

        current_sections = build_prompt_sections(current)
        changed: list[SceneSpecDiffSection] = []
        all_keys = sorted(set(original_sections) | set(current_sections))
        for key in all_keys:
            if original_sections.get(key) != current_sections.get(key):
                changed.append(
                    SceneSpecDiffSection(
                        section_key=key,
                        original=original_sections.get(key),
                        current=current_sections.get(key),
                    )
                )
        return SceneSpecDiffResult(
            original_spec_hash=spec.content_hash,
            current_spec_hash=current.content_hash,
            stale=stale,
            same=not changed,
            changed_sections=tuple(changed),
        )

    # -------------------------------------------------------------- persistence

    async def _persist_content(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecContract,
        version_row: SceneSpecVersionRow,
    ) -> None:
        from app.schemas.scene_spec import (
            constraint_canonical_payload,
            detail_canonical_payload,
        )

        detail_rows: dict[str, SceneSpecDetailRow] = {}
        for detail in spec.details:
            payload = detail_canonical_payload(detail)
            payload_hash = canonical_scene_spec_hash(payload)
            row = SceneSpecDetailRow(
                owner_id=owner_id,
                novel_id=novel_id,
                spec_id=version_row.id,
                detail_key=detail.detail_key,
                kind=detail.kind.value,
                source=detail.source.value,
                text=detail.text,
                author=detail.author,
                rationale=detail.rationale,
                spoiler_cutoff=detail.spoiler_cutoff,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=canonical_scene_spec_hash(
                    {
                        "kind": "scene_spec.detail",
                        "owner_id": owner_id,
                        "novel_id": novel_id,
                        "spec_key": spec.spec_key,
                        "detail_key": detail.detail_key,
                        "payload_hash": payload_hash,
                    }
                ),
                projection_hash=spec.content_hash,
                schema_version=spec.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            detail_rows[detail.detail_key] = row
            for ref in detail.evidence_refs:
                self._session.add(
                    self._evidence_row(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_row=version_row,
                        evidence_key=ref.evidence_key,
                        ref=ref,
                        spec_key=spec.spec_key,
                        detail_id=row.id,
                        constraint_id=None,
                    )
                )

        constraint_rows: dict[str, SceneSpecNegativeConstraintRow] = {}
        for constraint in spec.negative_constraints:
            payload = constraint_canonical_payload(constraint)
            payload_hash = canonical_scene_spec_hash(payload)
            row = SceneSpecNegativeConstraintRow(
                owner_id=owner_id,
                novel_id=novel_id,
                spec_id=version_row.id,
                constraint_key=constraint.constraint_key,
                scope=constraint.scope.value,
                source=constraint.source.value,
                text=constraint.text,
                author=constraint.author,
                rationale=constraint.rationale,
                spoiler_cutoff=constraint.spoiler_cutoff,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=canonical_scene_spec_hash(
                    {
                        "kind": "scene_spec.constraint",
                        "owner_id": owner_id,
                        "novel_id": novel_id,
                        "spec_key": spec.spec_key,
                        "constraint_key": constraint.constraint_key,
                        "payload_hash": payload_hash,
                    }
                ),
                projection_hash=spec.content_hash,
                schema_version=spec.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            constraint_rows[constraint.constraint_key] = row
            for ref in constraint.evidence_refs:
                self._session.add(
                    self._evidence_row(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_row=version_row,
                        evidence_key=ref.evidence_key,
                        ref=ref,
                        spec_key=spec.spec_key,
                        detail_id=None,
                        constraint_id=row.id,
                    )
                )

        for uncertainty in spec.uncertainties:
            self._session.add(
                SceneSpecUncertaintyRow(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    spec_id=version_row.id,
                    uncertainty_key=uncertainty.uncertainty_key,
                    reason=uncertainty.reason.value,
                    detail=uncertainty.detail,
                    idempotency_key=canonical_scene_spec_hash(
                        {
                            "kind": "scene_spec.uncertainty",
                            "owner_id": owner_id,
                            "novel_id": novel_id,
                            "spec_key": spec.spec_key,
                            "uncertainty_key": uncertainty.uncertainty_key,
                        }
                    ),
                )
            )

    def _evidence_row(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_row: SceneSpecVersionRow,
        evidence_key: str,
        ref: SpecEvidenceRef,
        spec_key: str,
        detail_id: int | None,
        constraint_id: int | None,
    ) -> SceneSpecEvidenceRefRow:
        return SceneSpecEvidenceRefRow(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_id=version_row.id,
            detail_id=detail_id,
            constraint_id=constraint_id,
            evidence_key=evidence_key,
            source_snapshot_id=ref.source_snapshot_id,
            source_snapshot_hash=ref.source_snapshot_hash,
            chapter_id=ref.chapter_id,
            chapter_number=ref.chapter_number,
            source_start=ref.source_start,
            source_end=ref.source_end,
            content_hash=ref.content_hash,
            excerpt=ref.excerpt,
            cutoff_chapter=ref.cutoff_chapter,
            idempotency_key=canonical_scene_spec_hash(
                {
                    "kind": "scene_spec.evidence",
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "spec_key": spec_key,
                    "evidence_key": evidence_key,
                }
            ),
        )

    # ----------------------------------------------------------------- views

    async def _view_from_rows(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecVersionRow,
    ) -> SceneSpecView:
        from app.schemas.scene_spec import (
            NegativeConstraintView,
            SceneDetailView,
            SceneUncertaintyView,
        )

        payload = spec.canonical_payload
        details = payload.get("details") or []
        constraints = payload.get("negative_constraints") or []
        uncertainties = payload.get("uncertainties") or []
        return SceneSpecView(
            id=spec.id,
            owner_id=spec.owner_id,
            novel_id=spec.novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_hash=spec.scene_candidate_hash,
            scene_candidate_id=spec.scene_candidate_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            content_hash=spec.content_hash,
            review_state=spec.review_state,
            details=[
                SceneDetailView(
                    detail_key=item["detail_key"],
                    kind=item["kind"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    spoiler_cutoff=item["spoiler_cutoff"],
                    evidence_keys=list(item.get("evidence_keys") or []),
                    visual_bible_stable_ids=[
                        ref["stable_id"] for ref in (item.get("visual_bible_refs") or [])
                    ],
                )
                for item in details
            ],
            negative_constraints=[
                NegativeConstraintView(
                    constraint_key=item["constraint_key"],
                    scope=item["scope"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in constraints
            ],
            uncertainties=[
                SceneUncertaintyView(
                    uncertainty_key=item["uncertainty_key"],
                    reason=item["reason"],
                    detail=item["detail"],
                )
                for item in uncertainties
            ],
        )

    @staticmethod
    def _view_from_contract(spec: SceneSpecContract) -> SceneSpecView:
        from app.schemas.scene_spec import (
            NegativeConstraintView,
            SceneDetailView,
            SceneUncertaintyView,
        )

        return SceneSpecView(
            id=0,
            owner_id=spec.owner_id,
            novel_id=spec.novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_hash=spec.scene_candidate_hash,
            scene_candidate_id=spec.scene_candidate_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            content_hash=spec.content_hash,
            review_state=spec.review_state,
            details=[
                SceneDetailView(
                    detail_key=detail.detail_key,
                    kind=detail.kind,
                    source=detail.source,
                    text=detail.text,
                    author=detail.author,
                    rationale=detail.rationale,
                    spoiler_cutoff=detail.spoiler_cutoff,
                    evidence_keys=[ref.evidence_key for ref in detail.evidence_refs],
                    visual_bible_stable_ids=[
                        ref.stable_id for ref in detail.visual_bible_refs
                    ],
                )
                for detail in spec.details
            ],
            negative_constraints=[
                NegativeConstraintView(
                    constraint_key=c.constraint_key,
                    scope=c.scope,
                    source=c.source,
                    text=c.text,
                    author=c.author,
                    rationale=c.rationale,
                    spoiler_cutoff=c.spoiler_cutoff,
                )
                for c in spec.negative_constraints
            ],
            uncertainties=[
                SceneUncertaintyView(
                    uncertainty_key=u.uncertainty_key,
                    reason=u.reason,
                    detail=u.detail,
                )
                for u in spec.uncertainties
            ],
        )

    @staticmethod
    def _sections_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
        from app.schemas.scene_spec import build_prompt_sections

        details = [
            SceneDetail(
                detail_key=item["detail_key"],
                kind=item["kind"],
                source=item["source"],
                text=item["text"],
                author=item.get("author"),
                rationale=item.get("rationale"),
                evidence_refs=[
                    SpecEvidenceRef(
                        evidence_key=key,
                        source_snapshot_id=payload.get("source_snapshot_id") or "ss",
                        source_snapshot_hash="0" * 64,
                        chapter_id=1,
                        chapter_number=1,
                        source_start=0,
                        source_end=1,
                        content_hash="0" * 64,
                        cutoff_chapter=1,
                    )
                    for key in (item.get("evidence_keys") or [])
                ],
                visual_bible_refs=[
                    VisualBibleRef(
                        stable_id=ref["stable_id"],
                        claim_key=ref.get("claim_key"),
                        revision_hash="0" * 64,
                    )
                    for ref in (item.get("visual_bible_refs") or [])
                ],
                spoiler_cutoff=item["spoiler_cutoff"],
            )
            for item in (payload.get("details") or [])
        ]
        spec = SceneSpecContract(
            schema_version=payload["schema_version"],
            artifact_kind="scene_spec",
            owner_id=payload["owner_id"],
            novel_id=payload["novel_id"],
            spec_key=payload["spec_key"],
            revision_number=payload["revision_number"],
            scene_candidate_hash=payload["scene_candidate_hash"],
            visual_bible_revision_hash=payload["visual_bible_revision_hash"],
            source_snapshot_id=payload["source_snapshot_id"],
            source_snapshot_hash=payload["source_snapshot_hash"],
            cutoff_chapter=payload["cutoff_chapter"],
            schema_hash=payload["schema_hash"],
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            policy_hash=payload["policy_hash"],
            config_hash=payload.get("config_hash"),
            content_hash="0" * 64,
            details=details,
            negative_constraints=[
                NegativeConstraint(
                    constraint_key=item["constraint_key"],
                    scope=item["scope"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    visual_bible_refs=[
                        VisualBibleRef(
                            stable_id=ref["stable_id"],
                            claim_key=ref.get("claim_key"),
                            revision_hash="0" * 64,
                        )
                        for ref in (item.get("visual_bible_refs") or [])
                    ],
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in (payload.get("negative_constraints") or [])
            ],
            uncertainties=[
                SceneUncertainty(
                    uncertainty_key=item["uncertainty_key"],
                    reason=item["reason"],
                    detail=item["detail"],
                )
                for item in (payload.get("uncertainties") or [])
            ],
        )
        return build_prompt_sections(spec)

    # ------------------------------------------------------------------ stale

    async def _is_stale(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecVersionRow,
    ) -> bool:
        current_hash, _ = await self._current_snapshot(owner_id=owner_id, novel_id=novel_id)
        latest_vb = await self._latest_approved_version(
            owner_id=owner_id, novel_id=novel_id
        )
        return (
            spec.visual_bible_revision_hash != latest_vb.manifest_hash
            or spec.source_snapshot_hash != current_hash
        )

    async def _current_snapshot(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[str, int]:
        """Fresh source snapshot address of the owning novel's chapter set."""
        from app.services.key_scenes.boundaries import SceneBoundaryService

        service = SceneBoundaryService(self._session)
        snapshot_hash, _chapters = await service.load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        return snapshot_hash, novel_id

    async def _latest_approved_version(
        self, *, owner_id: int, novel_id: int
    ) -> VisualBibleVersionRow:
        row = await self._session.scalar(
            select(VisualBibleVersionRow)
            .where(
                VisualBibleVersionRow.owner_id == owner_id,
                VisualBibleVersionRow.novel_id == novel_id,
                VisualBibleVersionRow.review_state == VisualReviewState.APPROVED.value,
            )
            .order_by(VisualBibleVersionRow.id.desc())
            .limit(1)
        )
        if row is None:
            raise SceneSpecNotFound(
                "novel has no approved Visual Bible revision; spec is stale by default"
            )
        return row

    # --------------------------------------------------------------- queries

    async def _spec(
        self, *, owner_id: int, novel_id: int, spec_key: str
    ) -> SceneSpecVersionRow | None:
        return await self._session.scalar(
            select(SceneSpecVersionRow).where(
                SceneSpecVersionRow.owner_id == owner_id,
                SceneSpecVersionRow.novel_id == novel_id,
                SceneSpecVersionRow.spec_key == spec_key,
            )
        )

    async def _spec_by_id(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> SceneSpecVersionRow | None:
        return await self._session.scalar(
            select(SceneSpecVersionRow).where(
                SceneSpecVersionRow.owner_id == owner_id,
                SceneSpecVersionRow.novel_id == novel_id,
                SceneSpecVersionRow.id == spec_id,
            )
        )

    @staticmethod
    def _version_idempotency_key(spec: SceneSpecContract) -> str:
        return canonical_scene_spec_hash(
            {
                "kind": "scene_spec.version",
                "owner_id": spec.owner_id,
                "novel_id": spec.novel_id,
                "spec_key": spec.spec_key,
                "content_hash": spec.content_hash,
            }
        )

    async def _load_visual_bible_contract(
        self, version: VisualBibleVersionRow
    ) -> VisualBibleVersionContract:
        """Reconstruct the immutable Visual Bible contract from persisted rows."""
        from app.schemas.visual_bible import (
            VisualBibleVersionContract,
            VisualClaimContract,
            VisualEntityContract,
            VisualEvidenceRef,
            VisualReferenceAssetContract,
            recompute_manifest_hash,
        )

        entity_rows = (
            await self._session.scalars(
                select(VisualEntityRow).where(
                    VisualEntityRow.owner_id == version.owner_id,
                    VisualEntityRow.novel_id == version.novel_id,
                    VisualEntityRow.version_id == version.id,
                )
            )
        ).all()
        entity_contracts = [
            VisualEntityContract(
                stable_id=row.stable_id,
                entity_key=row.entity_key,
                entity_type=row.entity_type,
                description=row.description,
                authority=row.authority,
                disclosure_cutoff=row.disclosure_cutoff,
            )
            for row in entity_rows
        ]
        claim_rows = (
            await self._session.scalars(
                select(VisualClaimRow).where(
                    VisualClaimRow.owner_id == version.owner_id,
                    VisualClaimRow.novel_id == version.novel_id,
                    VisualClaimRow.version_id == version.id,
                )
            )
        ).all()
        evidence_rows = (
            await self._session.scalars(
                select(VisualEvidenceRefRow).where(
                    VisualEvidenceRefRow.owner_id == version.owner_id,
                    VisualEvidenceRefRow.novel_id == version.novel_id,
                    VisualEvidenceRefRow.version_id == version.id,
                )
            )
        ).all()
        evidence_by_claim: dict[int, list[VisualEvidenceRef]] = {}
        for row in evidence_rows:
            evidence_by_claim.setdefault(row.claim_id, []).append(
                VisualEvidenceRef(
                    evidence_key=row.evidence_key,
                    source_snapshot_id=row.source_snapshot_id,
                    source_snapshot_hash=row.source_snapshot_hash,
                    chapter_id=row.chapter_id,
                    chapter_number=row.chapter_number,
                    source_start=row.source_start,
                    source_end=row.source_end,
                    content_hash=row.content_hash,
                    excerpt=row.excerpt,
                    cutoff_chapter=row.cutoff_chapter,
                )
            )
        claim_contracts = [
            VisualClaimContract(
                claim_key=row.claim_key,
                entity_stable_id=row.entity_stable_id,
                authority=row.authority,
                description=row.description,
                author=row.author,
                rationale=row.rationale,
                cutoff_chapter=row.cutoff_chapter,
                claim_hash=row.claim_hash,
                evidence_refs=evidence_by_claim.get(row.id, []),
            )
            for row in claim_rows
        ]
        asset_rows = (
            await self._session.scalars(
                select(VisualReferenceAssetRow).where(
                    VisualReferenceAssetRow.owner_id == version.owner_id,
                    VisualReferenceAssetRow.novel_id == version.novel_id,
                    VisualReferenceAssetRow.version_id == version.id,
                )
            )
        ).all()
        asset_contracts = [
            VisualReferenceAssetContract(
                asset_key=row.asset_key,
                asset_id=row.asset_id,
                mime_type=row.mime_type,
                bytes_hash=row.bytes_hash,
                rights_status=row.rights_status,
                provenance=row.provenance,
            )
            for row in asset_rows
        ]
        contract = VisualBibleVersionContract(
            schema_version=version.schema_version,
            artifact_kind="visual_bible",
            owner_id=version.owner_id,
            novel_id=version.novel_id,
            version_key=version.version_key,
            revision_number=version.revision_number,
            parent_version_id=version.parent_version_id,
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            cutoff_chapter=version.cutoff_chapter,
            schema_hash=version.schema_hash,
            policy_hash=version.policy_hash,
            prompt_hash=version.prompt_hash,
            model_hash=version.model_hash,
            config_hash=version.config_hash,
            manifest_hash=version.manifest_hash,
            style_profile=version.style_profile,
            constraints=version.constraints,
            entities=entity_contracts,
            claims=claim_contracts,
            reference_assets=asset_contracts,
            review_state=VisualReviewState(version.review_state),
        )
        if contract.manifest_hash != recompute_manifest_hash(contract):
            raise SceneSpecServiceError(
                "persisted Visual Bible revision does not replay its manifest hash"
            )
        return contract
