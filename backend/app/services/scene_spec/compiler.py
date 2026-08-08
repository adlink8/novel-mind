"""Evidence-to-Spec compiler service (Phase 32-02, REQ-VIS-03).

D-32-01..D-32-04: a ``SceneSpec`` is the canonical candidate Artifact compiled
deterministically from a frozen ``SceneCandidate`` (Phase 31) plus the approved
Visual Bible revision it was frozen against. This module owns the pure,
replayable ``compile_scene_spec`` seam — it assembles subject/action/setting/
composition/style/continuity details and negative constraints from the
candidate coordinates + evidence ranges and the Visual Bible
entities/claims/style/constraints, keeps every clause's evidence/interpretation
provenance, canonically serializes and hashes the spec, and fails closed on
unsupported canon, spoiler leaks and conflicts (D-32-02). It never touches the
database.

The provider-neutral ``build_prompt_revision_from_spec`` derivation (D-32-01/03)
and the owner-scoped ``SceneSpecService`` DB seam (preview/create/list/load/
diff/persistence/views/stale) live in ``prompt_builder.py`` and ``service.py``
respectively; the Visual-Bible contract reconstruction used by the service
lives in ``visual_bible_loader.py``. This module re-exports their public
symbols so ``from app.services.scene_spec.compiler import SceneSpecService``
keeps working unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.schemas.key_scene import (
    SceneCandidateContract,
    SceneEvidenceRange,
    candidate_content_hash,
)
from app.schemas.scene_spec import (
    SCENE_SPEC_SCHEMA_VERSION,
    ConstraintScope,
    NegativeConstraint,
    SceneDetail,
    SceneSpecContract,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewState,
    SpecSource,
    UncertaintyReason,
    VisualBibleRef,
    canonical_scene_spec_hash,
    recompute_scene_spec_hash,
    validate_scene_spec_contract,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleVersionContract,
    VisualEntityType,
    validate_version_contract,
)

from .errors import (
    SceneSpecCompileError,
    SceneSpecConflict,
    SceneSpecNotFound,
    SceneSpecServiceError,
)
from .prompt_builder import (
    MOCK_PROMPT_ADAPTER_ID,
    MOCK_PROMPT_ADAPTER_VERSION,
    MOCK_PROMPT_CONFIG_HASH,
    PROMPT_SCHEMA_HASH,
    build_prompt_revision_from_spec,
)

__all__ = [
    # compiler identity / lineage
    "COMPILER_ID",
    "COMPILER_VERSION",
    "SCENE_SPEC_SCHEMA_HASH",
    "PROMPT_SCHEMA_HASH",
    "SCENE_SPEC_DEFAULT_POLICY_HASH",
    # prompt derivation re-exports
    "MOCK_PROMPT_ADAPTER_ID",
    "MOCK_PROMPT_ADAPTER_VERSION",
    "MOCK_PROMPT_CONFIG_HASH",
    "build_prompt_revision_from_spec",
    # error surface
    "SceneSpecCompileError",
    "SceneSpecServiceError",
    "SceneSpecNotFound",
    "SceneSpecConflict",
    # pure compile contracts
    "CompileUnresolved",
    "SceneSpecCompileInput",
    "CompiledSceneSpec",
    "compile_scene_spec",
    # NOTE: the service seam re-exports (SceneSpecService, SceneSpecPreviewRequest,
    # SceneSpecPreviewResult, PersistedSceneSpec, SceneSpecDiffSection,
    # SceneSpecDiffResult) are intentionally NOT listed in __all__: they are
    # provided lazily via the module __getattr__ below and ruff would otherwise
    # report F822 for names it cannot see statically.
]

# Deterministic compiler lineage (D-32-03).
COMPILER_ID = "scene-spec.v1"
COMPILER_VERSION = "1.0.0"

SCENE_SPEC_SCHEMA_HASH = canonical_scene_spec_hash(
    {
        "kind": "scene_spec.schema",
        "schema_version": SCENE_SPEC_SCHEMA_VERSION,
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
    _require(
        candidate.chapter_number <= input_.cutoff_chapter,
        "candidate chapter_number exceeds the spoiler cutoff",
    )
    _require(
        candidate.spoiler_cutoff == input_.cutoff_chapter,
        "candidate spoiler_cutoff does not match the compile cutoff",
    )
    _require(
        candidate_content_hash(candidate) == input_.scene_candidate_hash,
        "candidate content hash does not replay",
    )
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
    _require(
        vb.manifest_hash == input_.visual_bible_revision_hash,
        "visual bible manifest hash does not match the revision hash",
    )
    _require(
        vb.cutoff_chapter >= input_.cutoff_chapter,
        "visual bible cutoff_chapter is below the compile cutoff",
    )
    try:
        validate_version_contract(vb)
    except ValueError as exc:
        raise SceneSpecCompileError(
            f"visual bible revision failed revalidation: {exc}"
        ) from exc


def _find_entity(vb: VisualBibleVersionContract, name: str) -> Any | None:
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
        rendered = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
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
    _require(
        isinstance(constraint_key, str) and constraint_key,
        f"constraint[{index}] has no constraint_key",
    )
    scope_raw = entry.get("scope")
    _require(
        isinstance(scope_raw, str) and scope_raw in ConstraintScope._value2member_map_,
        f"constraint {constraint_key!r} has unsupported scope",
    )
    text = entry.get("text")
    _require(
        isinstance(text, str) and text, f"constraint {constraint_key!r} has no text"
    )
    source_raw = entry.get("source") or SpecSource.VISUAL_BIBLE.value
    _require(
        source_raw in SpecSource._value2member_map_,
        f"constraint {constraint_key!r} has unsupported source",
    )
    source = SpecSource(source_raw)

    author = entry.get("author") if isinstance(entry.get("author"), str) else None
    rationale = (
        entry.get("rationale") if isinstance(entry.get("rationale"), str) else None
    )
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
                raise SceneSpecCompileError(
                    f"constraint {constraint_key!r} evidence[{i}] is not an object"
                )
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
        evidence = [
            _evidence_ref_from_range(
                primary, input_, namespace=f"constraint:{constraint_key}"
            )
        ]

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
                (
                    c
                    for c in claims
                    if c.authority is VisualAuthority.USER_INTERPRETATION
                ),
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
            primary = (
                input_.candidate.evidence_ranges[0]
                if input_.candidate.evidence_ranges
                else None
            )
            if primary is not None:
                details.append(
                    SceneDetail(
                        detail_key=f"setting:place:{place}",
                        kind=SpecDetailKind.SETTING,
                        source=SpecSource.EVIDENCE,
                        text=f"地点：{place}",
                        evidence_refs=[
                            _evidence_ref_from_range(
                                primary, input_, namespace=f"setting:place:{place}"
                            )
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
        primary = (
            input_.candidate.evidence_ranges[0]
            if input_.candidate.evidence_ranges
            else None
        )
        if primary is not None:
            details.append(
                SceneDetail(
                    detail_key=f"setting:time:{time}",
                    kind=SpecDetailKind.SETTING,
                    source=SpecSource.EVIDENCE,
                    text=f"时间：{time}",
                    evidence_refs=[
                        _evidence_ref_from_range(
                            primary, input_, namespace=f"setting:time:{time}"
                        )
                    ],
                    spoiler_cutoff=cutoff,
                )
            )

    # ---- action: the candidate's primary evidence excerpt (evidence authority)
    primary = (
        input_.candidate.evidence_ranges[0]
        if input_.candidate.evidence_ranges
        else None
    )
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
        raise SceneSpecCompileError(
            f"compiled spec failed its own contract gate: {exc}"
        ) from exc

    return CompiledSceneSpec(spec=spec, unresolved=tuple(unresolved))


# ---------------------------------------------------------------------------
# Lazy re-export of the DB service seam (avoids compiler ↔ service cycle)
# ---------------------------------------------------------------------------

_SERVICE_EXPORTS = {
    "SceneSpecPreviewRequest",
    "SceneSpecPreviewResult",
    "PersistedSceneSpec",
    "SceneSpecDiffSection",
    "SceneSpecDiffResult",
    "SceneSpecService",
}


def __getattr__(name: str) -> Any:
    if name in _SERVICE_EXPORTS:
        from .service import (  # noqa: PLC0415
            PersistedSceneSpec,
            SceneSpecDiffResult,
            SceneSpecDiffSection,
            SceneSpecPreviewRequest,
            SceneSpecPreviewResult,
            SceneSpecService,
        )

        return {
            "SceneSpecPreviewRequest": SceneSpecPreviewRequest,
            "SceneSpecPreviewResult": SceneSpecPreviewResult,
            "PersistedSceneSpec": PersistedSceneSpec,
            "SceneSpecDiffSection": SceneSpecDiffSection,
            "SceneSpecDiffResult": SceneSpecDiffResult,
            "SceneSpecService": SceneSpecService,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
