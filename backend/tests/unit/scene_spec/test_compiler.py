"""Phase 32-02 SceneSpec compiler unit tests (REQ-VIS-03).

Covers D-32-02..D-32-04 for the evidence-to-spec compiler:
- deterministic compile: same input produces the same content hash and the
  same canonical prompt sections (golden replay);
- continuity: cast members and places keep their stable Visual Bible IDs and
  the continuity clause carries every matched entity ref;
- negative constraints: forbidden costume/era/identity/style details from the
  Visual Bible constraints list are preserved with provenance and never leak
  into positive sections;
- unsupported detail gate: a cast member absent from the Visual Bible, a
  conflicting canon claim and a future-spoiler entity all become reason-coded
  uncertainties and are never emitted as canon details;
- fail-closed lineage: candidate hash drift, Visual Bible manifest drift,
  evidence chapter beyond the spoiler cutoff and candidate chapter beyond the
  cutoff all raise and produce no spec;
- PromptArtifact derivation: the same spec deterministically rebuilds a
  provider-neutral PromptRevision whose input_hash/prompt_hash replay, differ,
  and whose canonical sections match the spec.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.key_scene import (
    SceneCandidateContract,
    SceneCoordinates,
    SceneEvidenceRange,
    candidate_content_hash,
)
from app.schemas.scene_spec import (
    ConstraintScope,
    SpecDetailKind,
    SpecSource,
    UncertaintyReason,
    build_prompt_sections,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
    validate_prompt_revision_contract,
    validate_scene_spec_contract,
)
from app.schemas.visual_bible import (
    VisualAuthority,
    VisualBibleVersionContract,
    VisualClaimContract,
    VisualEntityType,
    VisualReviewState,
    claim_content_hash,
    recompute_manifest_hash,
)
from app.services.scene_spec.compiler import (
    MOCK_PROMPT_ADAPTER_ID,
    SCENE_SPEC_DEFAULT_POLICY_HASH,
    SceneSpecCompileError,
    SceneSpecCompileInput,
    build_prompt_revision_from_spec,
    compile_scene_spec,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

OWNER_ID = 1
NOVEL_ID = 1
SPEC_KEY = "spec-continuity"
SNAPSHOT_ID = "ss-1"
SNAPSHOT_HASH = HEX64_C
CUTOFF = 3

CH_ACTION = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    '"We attack at dawn!" he said. Mara drew her sword and charged. '
    "The enemy banners would rise with the sun and there would be no going back! "
    "Torches guttered low across the courtyard as the attack exploded."
)


def _candidate(
    *,
    cast: tuple[str, ...] = ("arin", "mara"),
    place: str = "courtyard",
    time: str = "night",
    chapter_number: int = 1,
    cutoff: int = CUTOFF,
    evidence_key: str = "ev-scene-1",
) -> SceneCandidateContract:
    ref = SceneEvidenceRange(
        evidence_key=evidence_key,
        source_snapshot_id=SNAPSHOT_ID,
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_id=10,
        chapter_number=chapter_number,
        source_start=0,
        source_end=min(len(CH_ACTION), 120),
        content_hash=HEX64,
        excerpt=CH_ACTION[:120],
        cutoff_chapter=cutoff,
    )
    return SceneCandidateContract(
        candidate_key="ks-main-0",
        candidate_order=0,
        scene_id="scene-main-0001",
        chapter_id=10,
        chapter_number=chapter_number,
        source_start=0,
        source_end=min(len(CH_ACTION), 120),
        source_hash=HEX64,
        coordinates=SceneCoordinates(cast=list(cast), place=place, time=time, pov="arin"),
        spoiler_cutoff=cutoff,
        salience_reasons=[],
        score_total=1.0,
        score_breakdown={},
        diversity_key="d1",
        detector_id="key-scene.v1",
        detector_version="1.0.0",
        policy_hash=HEX64_B,
        evidence_ranges=[ref],
        review_state="candidate",
    )


def _entity(
    *,
    stable_id: str,
    entity_type: str,
    description: str,
    authority: str = VisualAuthority.CANON_FACT.value,
    disclosure_cutoff: int = CUTOFF,
) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "entity_key": stable_id,
        "entity_type": entity_type,
        "description": description,
        "authority": authority,
        "disclosure_cutoff": disclosure_cutoff,
    }


def _claim(
    *,
    claim_key: str,
    entity_stable_id: str,
    authority: str,
    description: str,
    author: str | None = None,
    rationale: str | None = None,
    evidence: list[dict[str, Any]] | None = None,
    cutoff: int = CUTOFF,
) -> dict[str, Any]:
    payload = {
        "claim_key": claim_key,
        "entity_stable_id": entity_stable_id,
        "authority": authority,
        "description": description,
        "author": author,
        "rationale": rationale,
        "cutoff_chapter": cutoff,
        "claim_hash": "0" * 64,
        "evidence_refs": evidence or [],
    }
    claim = VisualClaimContract.model_validate(payload)
    claim = claim.model_copy(update={"claim_hash": claim_content_hash(claim)})
    return claim.model_dump(mode="json")


def _claim_evidence(evidence_key: str) -> dict[str, Any]:
    return {
        "evidence_key": evidence_key,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_hash": SNAPSHOT_HASH,
        "chapter_id": 10,
        "chapter_number": 1,
        "source_start": 0,
        "source_end": 20,
        "content_hash": HEX64,
        "excerpt": "Arin drew his sword",
        "cutoff_chapter": CUTOFF,
    }


def _build_visual_bible(
    *,
    entities: list[dict[str, Any]],
    claims: list[dict[str, Any]],
    constraints: list[dict[str, Any]] | None = None,
    style_profile: dict[str, Any] | None = None,
) -> VisualBibleVersionContract:
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": OWNER_ID,
        "novel_id": NOVEL_ID,
        "version_key": "vb-main",
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_hash": SNAPSHOT_HASH,
        "cutoff_chapter": CUTOFF,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "prompt_hash": HEX64,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": style_profile,
        "constraints": constraints,
        "entities": entities,
        "claims": claims,
        "reference_assets": [],
        "review_state": VisualReviewState.APPROVED.value,
    }
    version = VisualBibleVersionContract.model_validate(payload)
    return version.model_copy(update={"manifest_hash": recompute_manifest_hash(version)})


def _continuity_bible() -> VisualBibleVersionContract:
    """Two characters + one place with stable IDs (VALIDATION spec-continuity)."""
    entities = [
        _entity(
            stable_id="arin",
            entity_type=VisualEntityType.CHARACTER.value,
            description="Arin, a tall swordsman in a grey cloak",
        ),
        _entity(
            stable_id="mara",
            entity_type=VisualEntityType.CHARACTER.value,
            description="Mara, a quick archer with braided black hair",
        ),
        _entity(
            stable_id="courtyard",
            entity_type=VisualEntityType.PLACE.value,
            description="The rain-soaked courtyard with stone walls",
        ),
    ]
    claims = [
        _claim(
            claim_key="c-arin-appearance",
            entity_stable_id="arin",
            authority=VisualAuthority.CANON_FACT.value,
            description="Arin wears a grey cloak",
            evidence=[_claim_evidence("ev-arin")],
        ),
        _claim(
            claim_key="c-mara-appearance",
            entity_stable_id="mara",
            authority=VisualAuthority.CANON_FACT.value,
            description="Mara is an archer",
            evidence=[_claim_evidence("ev-mara")],
        ),
    ]
    constraints = [
        {
            "constraint_key": "nc-no-cinematic-armor",
            "scope": ConstraintScope.COSTUME.value,
            "source": SpecSource.VISUAL_BIBLE.value,
            "text": "do not add ornate armor to Arin",
        },
        {
            "constraint_key": "nc-modern-era",
            "scope": ConstraintScope.ERA.value,
            "source": SpecSource.VISUAL_BIBLE.value,
            "text": "the scene must stay medieval; no modern objects",
        },
    ]
    return _build_visual_bible(
        entities=entities,
        claims=claims,
        constraints=constraints,
        style_profile={"palette": "muted cold tones", "lighting": "overcast daylight"},
    )


def _compile_input(
    *,
    candidate: SceneCandidateContract | None = None,
    visual_bible: VisualBibleVersionContract | None = None,
    spec_key: str = SPEC_KEY,
    **overrides: Any,
) -> SceneSpecCompileInput:
    candidate = candidate or _candidate()
    visual_bible = visual_bible or _continuity_bible()
    return SceneSpecCompileInput(
        owner_id=OWNER_ID,
        novel_id=NOVEL_ID,
        spec_key=spec_key,
        revision_number=1,
        candidate=candidate,
        scene_candidate_hash=candidate_content_hash(candidate),
        scene_candidate_id=None,
        visual_bible=visual_bible,
        visual_bible_revision_hash=visual_bible.manifest_hash,
        visual_bible_revision_id=None,
        source_snapshot_id=SNAPSHOT_ID,
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=CUTOFF,
        policy_hash=SCENE_SPEC_DEFAULT_POLICY_HASH,
        config_hash=None,
        **overrides,
    )


# ---------------------------------------------------------------------------
# Deterministic golden replay
# ---------------------------------------------------------------------------


def test_same_input_compiles_to_same_hash():
    compiled_a = compile_scene_spec(_compile_input())
    compiled_b = compile_scene_spec(_compile_input())
    assert compiled_a.spec.content_hash == compiled_b.spec.content_hash
    assert build_prompt_sections(compiled_a.spec) == build_prompt_sections(
        compiled_b.spec
    )
    # Deterministic lineage fields are pinned, not random.
    assert len(compiled_a.spec.content_hash) == 64
    assert compiled_a.spec.compiler_id == "scene-spec.v1"
    assert compiled_a.spec.schema_hash == compiled_b.spec.schema_hash


def test_compiled_spec_passes_its_own_contract_gate():
    compiled = compile_scene_spec(_compile_input())
    validate_scene_spec_contract(compiled.spec)  # must not raise


# ---------------------------------------------------------------------------
# Continuity: stable Visual Bible IDs preserved
# ---------------------------------------------------------------------------


def test_continuity_keeps_stable_visual_bible_ids():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec

    subjects = {
        d.detail_key: d for d in spec.details if d.kind is SpecDetailKind.SUBJECT
    }
    assert "subject:arin" in subjects
    assert "subject:mara" in subjects
    arin = subjects["subject:arin"]
    assert arin.source is SpecSource.VISUAL_BIBLE
    assert arin.text == "Arin, a tall swordsman in a grey cloak"
    assert arin.visual_bible_refs[0].stable_id == "arin"
    assert arin.visual_bible_refs[0].revision_hash == compiled.spec.visual_bible_revision_hash
    # VB-sourced clauses cite the immutable Visual Bible revision; the spec's
    # own evidence lineage is carried by evidence-sourced clauses instead.
    assert arin.evidence_refs == []

    # Place matched to the Visual Bible PLACE entity keeps its stable id.
    settings = {d.detail_key: d for d in spec.details if d.kind is SpecDetailKind.SETTING}
    place_detail = settings["setting:place:courtyard"]
    assert place_detail.source is SpecSource.VISUAL_BIBLE
    assert place_detail.visual_bible_refs[0].stable_id == "courtyard"

    # Continuity clause lists every matched entity and carries every ref.
    continuity = next(
        d for d in spec.details if d.kind is SpecDetailKind.CONTINUITY
    )
    assert "arin" in continuity.text
    assert "mara" in continuity.text
    assert "courtyard" in continuity.text
    stable_ids = {ref.stable_id for ref in continuity.visual_bible_refs}
    assert stable_ids == {"arin", "mara", "courtyard"}


def test_action_and_time_are_evidence_bounded():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec
    action = next(
        d for d in spec.details if d.kind is SpecDetailKind.ACTION
    )
    assert action.source is SpecSource.EVIDENCE
    assert action.evidence_refs, "action clause must be evidence-linked"
    assert action.text.startswith("Arin drew his sword")

    time_detail = next(
        d for d in spec.details if d.kind is SpecDetailKind.SETTING and "time" in d.detail_key
    )
    assert time_detail.source is SpecSource.EVIDENCE
    assert time_detail.evidence_refs


def test_style_profile_renders_deterministically():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec
    style = next(
        d for d in spec.details if d.kind is SpecDetailKind.STYLE
    )
    assert style.source is SpecSource.VISUAL_BIBLE
    assert "lighting: overcast daylight" in style.text
    assert "palette: muted cold tones" in style.text
    assert style.visual_bible_refs[0].revision_hash == spec.visual_bible_revision_hash


# ---------------------------------------------------------------------------
# Negative constraints preserved with provenance
# ---------------------------------------------------------------------------


def test_negative_constraints_preserved_and_never_in_positive_sections():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec
    by_key = {c.constraint_key: c for c in spec.negative_constraints}
    assert "nc-no-cinematic-armor" in by_key
    assert "nc-modern-era" in by_key

    armor = by_key["nc-no-cinematic-armor"]
    assert armor.scope is ConstraintScope.COSTUME
    assert armor.source is SpecSource.VISUAL_BIBLE
    assert armor.visual_bible_refs
    assert armor.spoiler_cutoff == CUTOFF

    era = by_key["nc-modern-era"]
    assert era.scope is ConstraintScope.ERA

    # Negative constraints never appear in a positive prompt section.
    sections = build_prompt_sections(spec)
    assert "negative_constraints" in sections
    for clause in spec_negative_constraint_texts(spec):
        assert clause in sections["negative_constraints"]
    for detail in spec.details:
        assert not any(
            c.text in detail.text for c in spec.negative_constraints
        )


def test_unsupported_constraint_shape_fails_closed():
    vb = _continuity_bible().model_copy(
        update={
            "constraints": [
                {"constraint_key": "nc-bad", "text": "no scope key present"}
            ]
        }
    )
    vb = vb.model_copy(update={"manifest_hash": recompute_manifest_hash(vb)})
    with pytest.raises(SceneSpecCompileError):
        compile_scene_spec(_compile_input(visual_bible=vb))


# ---------------------------------------------------------------------------
# Unsupported detail gate (never canon)
# ---------------------------------------------------------------------------


def test_absent_cast_member_becomes_missing_evidence_uncertainty():
    candidate = _candidate(cast=("arin", "mara", "zephyr"))
    compiled = compile_scene_spec(_compile_input(candidate=candidate))
    spec = compiled.spec
    subjects = {
        d.detail_key: d for d in spec.details if d.kind is SpecDetailKind.SUBJECT
    }
    assert "subject:arin" in subjects
    assert "subject:zephyr" not in subjects, "unsupported detail must not be canon"
    reasons = {u.reason for u in spec.uncertainties}
    assert UncertaintyReason.MISSING_EVIDENCE in reasons
    assert any(
        u.uncertainty_key == "subject-missing:zephyr" for u in spec.uncertainties
    )
    # Uncertainties are surfaced separately, never in a positive section.
    assert "uncertainties" in build_prompt_sections(spec)


def test_conflicting_canon_claims_withhold_the_detail():
    entities = [
        _entity(
            stable_id="arin",
            entity_type=VisualEntityType.CHARACTER.value,
            description="Arin, a tall swordsman",
        )
    ]
    claims = [
        _claim(
            claim_key="c1",
            entity_stable_id="arin",
            authority=VisualAuthority.CANON_FACT.value,
            description="Arin wears a red cloak",
            evidence=[_claim_evidence("ev-arin-a")],
        ),
        _claim(
            claim_key="c2",
            entity_stable_id="arin",
            authority=VisualAuthority.CANON_FACT.value,
            description="Arin wears a blue cloak",
            evidence=[_claim_evidence("ev-arin-b")],
        ),
    ]
    vb = _build_visual_bible(entities=entities, claims=claims)
    compiled = compile_scene_spec(_compile_input(visual_bible=vb))
    spec = compiled.spec
    subjects = [
        d for d in spec.details if d.kind is SpecDetailKind.SUBJECT
    ]
    assert not subjects, "conflicting claim must not be emitted as canon"
    assert any(
        u.reason is UncertaintyReason.CONFLICTING_CLAIM
        and u.uncertainty_key == "subject-conflict:arin"
        for u in spec.uncertainties
    )


def test_future_spoiler_entity_is_withheld():
    entities = [
        _entity(
            stable_id="zephyr",
            entity_type=VisualEntityType.CHARACTER.value,
            description="Zephyr appears only in a later chapter",
            disclosure_cutoff=CUTOFF + 2,
        )
    ]
    vb = _build_visual_bible(entities=entities, claims=[])
    candidate = _candidate(cast=("zephyr",))
    compiled = compile_scene_spec(
        _compile_input(candidate=candidate, visual_bible=vb)
    )
    spec = compiled.spec
    assert all(d.kind is not SpecDetailKind.SUBJECT for d in spec.details)
    assert any(
        u.reason is UncertaintyReason.FUTURE_SPOILER
        and u.uncertainty_key == "subject-spoiler:zephyr"
        for u in spec.uncertainties
    )


def test_interpretation_entity_needs_author_and_rationale():
    entities = [
        _entity(
            stable_id="mara",
            entity_type=VisualEntityType.CHARACTER.value,
            description="mara base",
            authority=VisualAuthority.USER_INTERPRETATION.value,
        )
    ]
    claims = [
        _claim(
            claim_key="c-mara-vibe",
            entity_stable_id="mara",
            authority=VisualAuthority.USER_INTERPRETATION.value,
            description="Mara should look fierce and wind-bitten",
            author="author-1",
            rationale="user visual preference",
        )
    ]
    vb = _build_visual_bible(entities=entities, claims=claims)
    compiled = compile_scene_spec(_compile_input(visual_bible=vb))
    spec = compiled.spec
    mara = next(
        d for d in spec.details if d.detail_key == "subject:mara"
    )
    assert mara.source is SpecSource.USER_INTERPRETATION
    assert mara.author == "author-1"
    assert mara.rationale == "user visual preference"
    assert not mara.evidence_refs
    assert not mara.visual_bible_refs


# ---------------------------------------------------------------------------
# Fail-closed lineage gates
# ---------------------------------------------------------------------------


def test_candidate_hash_drift_fails_closed():
    input_ = _compile_input()
    input_ = SceneSpecCompileInput(
        **{
            **{k: v for k, v in input_.__dict__.items()},
            "scene_candidate_hash": HEX64_B,
        }
    )
    with pytest.raises(SceneSpecCompileError):
        compile_scene_spec(input_)


def test_visual_bible_manifest_drift_fails_closed():
    base = _compile_input()
    with pytest.raises(SceneSpecCompileError):
        compile_scene_spec(
            SceneSpecCompileInput(
                owner_id=base.owner_id,
                novel_id=base.novel_id,
                spec_key=base.spec_key,
                revision_number=base.revision_number,
                candidate=base.candidate,
                scene_candidate_hash=base.scene_candidate_hash,
                scene_candidate_id=base.scene_candidate_id,
                visual_bible=base.visual_bible,
                visual_bible_revision_hash=HEX64_B,
                visual_bible_revision_id=base.visual_bible_revision_id,
                source_snapshot_id=base.source_snapshot_id,
                source_snapshot_hash=base.source_snapshot_hash,
                cutoff_chapter=base.cutoff_chapter,
                policy_hash=base.policy_hash,
                config_hash=base.config_hash,
            )
        )


def test_candidate_evidence_beyond_cutoff_fails_closed():
    # Candidate chapter is inside its own spoiler_cutoff but beyond the spec
    # cutoff; the compile-level gate must fail closed.
    candidate = _candidate(chapter_number=CUTOFF + 1, cutoff=CUTOFF + 1)
    with pytest.raises(SceneSpecCompileError):
        compile_scene_spec(
            _compile_input(candidate=candidate)
        )


def test_candidate_chapter_beyond_cutoff_fails_closed():
    candidate = _candidate(chapter_number=CUTOFF + 1, cutoff=CUTOFF + 1)
    with pytest.raises(SceneSpecCompileError):
        compile_scene_spec(_compile_input(candidate=candidate))


# ---------------------------------------------------------------------------
# PromptArtifact deterministic rebuild
# ---------------------------------------------------------------------------


def test_prompt_golden_replays_identically_and_separates_uncertainties():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec

    rev_a = build_prompt_revision_from_spec(spec, prompt_key="pk-main")
    rev_b = build_prompt_revision_from_spec(spec, prompt_key="pk-main")
    assert rev_a.input_hash == rev_b.input_hash
    assert rev_a.prompt_hash == rev_b.prompt_hash
    assert rev_a.prompt_text == rev_b.prompt_text
    assert rev_a.adapter_id == MOCK_PROMPT_ADAPTER_ID
    # Hash both inputs and output (identical text can come from different
    # evidence/Visual Bible revisions, so input_hash and prompt_hash must differ).
    assert rev_a.input_hash != rev_a.prompt_hash
    # The derived prompt is exactly reproducible from its SceneSpec.
    validate_prompt_revision_contract(rev_a, spec)
    assert recompute_prompt_input_hash(rev_a, spec) == rev_a.input_hash
    assert recompute_prompt_hash(rev_a) == rev_a.prompt_hash


def test_prompt_surfaces_all_uncertainties_without_canon_leakage():
    candidate = _candidate(cast=("arin", "mara", "zephyr"))
    compiled = compile_scene_spec(_compile_input(candidate=candidate))
    spec = compiled.spec
    assert spec.uncertainties

    revision = build_prompt_revision_from_spec(spec, prompt_key="pk-unc")
    assert revision.uncertainties == spec_uncertainty_texts(spec)
    # Uncertainties never appear inside a positive canon section.
    for section_key, section_text in revision.sections.items():
        if section_key == "uncertainties":
            continue
        for uncertainty in spec_uncertainty_texts(spec):
            assert uncertainty not in section_text


def test_prompt_negative_constraints_are_preserved():
    compiled = compile_scene_spec(_compile_input())
    spec = compiled.spec
    revision = build_prompt_revision_from_spec(spec, prompt_key="pk-neg")
    assert revision.negative_constraints == spec_negative_constraint_texts(spec)
    assert "costume: do not add ornate armor to Arin" in revision.negative_constraints
