"""Phase 32-01 Scene Spec / Prompt Revision contract tests (REQ-VIS-03).

Covers D-32-01..D-32-04:
- strict typed contracts reject provider-specific fields, unbacked or
  future-spoiler details and any canon promotion path; SceneSpec is the only
  canonical candidate Artifact and PromptRevision is a derived candidate;
- canonical detail source rule: every detail/constraint is evidence-linked,
  Visual Bible-linked or explicitly user interpretation; unsupported material
  must be rejected or live in uncertainties and can never be disguised as canon;
- deterministic lineage: source snapshot / evidence hash / spoiler cutoff /
  Visual Bible revision / scene candidate hash / compiler / adapter / config
  hashes hold at every applicable boundary; a compiled PromptRevision replays
  exactly from its SceneSpec and becomes stale when the spec or Visual Bible
  revision changes;
- prompt golden/negative/edit fixtures: identical canonical sections across
  adapters (provider-neutral), negative constraints preserved, interpretation
  edits become new candidate revisions with the diff retained;
- candidate-only approval gate: default state is candidate, review actions are
  append-only and idempotent, frozen envelopes reject unapproved candidates;
- ORM + migration chain (20260801_scene_spec_prompt on top of
  20260801_key_scene) and append-only content rows are verified.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.models.user import User
from app.models.scene_spec import (
    SceneSpecDetail,
    SceneSpecEvidenceRef,
    SceneSpecNegativeConstraint,
    SceneSpecUncertainty,
    SceneSpecVersion,
)
from app.models.prompt_revision import PromptRevision
from app.schemas.scene_spec import (
    LEGAL_SPEC_REVIEW_TRANSITIONS,
    SPEC_CONSTRAINT_SCOPES,
    SPEC_DETAIL_KINDS,
    SPEC_REVIEW_ACTIONS,
    SPEC_REVIEW_STATES,
    SPEC_SECTION_ORDER,
    SPEC_SOURCES,
    SPEC_UNCERTAINTY_REASONS,
    SPEC_REVIEW_ACTION_TO_STATE,
    ConstraintScope,
    FrozenPromptRevisionView,
    FrozenSceneSpecView,
    NegativeConstraint,
    PromptArtifactLineage,
    PromptRevisionContract,
    PromptRevisionView,
    SceneDetail,
    SceneSpecContract,
    SceneSpecGateError,
    SceneSpecView,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewAction,
    SpecReviewEventInput,
    SpecReviewState,
    SpecSource,
    UncertaintyReason,
    VisualBibleRef,
    build_prompt_lineage,
    build_prompt_sections,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    recompute_prompt_input_hash,
    recompute_scene_spec_hash,
    review_state_after,
    scene_spec_content_payload,
    spec_negative_constraint_texts,
    spec_uncertainty_texts,
    validate_prompt_revision_contract,
    validate_review_event,
    validate_scene_spec_contract,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64

# Shared lineage hashes used by the fixture builders below.
VB_HASH = HEX64  # approved Visual Bible revision the spec is frozen against
CANDIDATE_HASH = HEX64_B  # source SceneCandidate content hash
SNAPSHOT_HASH = HEX64_C  # source snapshot hash
SCHEMA_HASH = HEX64_D  # scene-spec schema hash
POLICY_HASH = HEX64  # compiler policy hash
CONFIG_HASH = HEX64_B  # compiler config hash

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"

SCENE_SPEC_TABLES = {
    "scene_spec_versions",
    "scene_spec_details",
    "scene_spec_negative_constraints",
    "scene_spec_evidence_refs",
    "scene_spec_uncertainties",
}
PROMPT_TABLES = {"prompt_revisions"}

# Pinned canonical hashes of the closed vocabularies so a future rename cannot
# pass silently (stable hash pins the closed contract, D-32-02/03).
DETAIL_KINDS_HASH = canonical_scene_spec_hash({"detail_kinds": list(SPEC_DETAIL_KINDS)})
SOURCES_HASH = canonical_scene_spec_hash({"sources": list(SPEC_SOURCES)})
SCOPES_HASH = canonical_scene_spec_hash(
    {"constraint_scopes": list(SPEC_CONSTRAINT_SCOPES)}
)
REASONS_HASH = canonical_scene_spec_hash(
    {"uncertainty_reasons": list(SPEC_UNCERTAINTY_REASONS)}
)
SECTIONS_HASH = canonical_scene_spec_hash({"section_order": list(SPEC_SECTION_ORDER)})


# Mock provider adapters: deterministic, provider-neutral section renderers.
# Adapter B deliberately reorders sections to prove the canonical sections and
# the input lineage stay adapter-independent.
def _mock_adapter_a(sections: dict[str, str]) -> str:
    lines: list[str] = []
    for key in SPEC_SECTION_ORDER:
        if key in sections:
            lines.append(f"== {key} ==")
            lines.append(sections[key])
    return "\n".join(lines)


def _mock_adapter_b(sections: dict[str, str]) -> str:
    lines: list[str] = []
    for key in reversed(SPEC_SECTION_ORDER):
        if key in sections:
            lines.append(f"### {key}")
            lines.append(sections[key])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _evidence(**overrides):
    payload = {
        "evidence_key": "ev-1",
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "chapter_id": 3,
        "chapter_number": 3,
        "source_start": 0,
        "source_end": 120,
        "content_hash": HEX64_D,
        "excerpt": "Arin drew his sword as the rain fell across the courtyard walls.",
        "cutoff_chapter": 8,
    }
    payload.update(overrides)
    return SpecEvidenceRef.model_validate(payload)


def _vbref(stable_id: str = "char-arin", **overrides):
    payload = {
        "stable_id": stable_id,
        "claim_key": None,
        "revision_id": None,
        "revision_hash": VB_HASH,
    }
    payload.update(overrides)
    return VisualBibleRef.model_validate(payload)


def _detail(**overrides):
    payload = {
        "detail_key": "sub-arin",
        "kind": "subject",
        "source": "evidence",
        "text": "Arin draws his sword in the rain",
        "author": None,
        "rationale": None,
        "evidence_refs": [_evidence().model_dump()],
        "visual_bible_refs": [],
        "spoiler_cutoff": 8,
    }
    payload.update(overrides)
    return SceneDetail.model_validate(payload)


def _constraint(**overrides):
    payload = {
        "constraint_key": "neg-costume",
        "scope": "costume",
        "source": "evidence",
        "text": "no modern clothing",
        "author": None,
        "rationale": None,
        "evidence_refs": [_evidence()],
        "visual_bible_refs": [],
        "spoiler_cutoff": 8,
    }
    payload.update(overrides)
    return NegativeConstraint.model_validate(payload)


def _uncertainty(**overrides):
    payload = {
        "uncertainty_key": "unc-red-cloak",
        "reason": "conflicting_claim",
        "detail": "the cloak color is disputed in chapters 3 and 4",
    }
    payload.update(overrides)
    return SceneUncertainty.model_validate(payload)


def _spec(**overrides):
    payload = {
        "schema_version": "scene-spec.v1",
        "artifact_kind": "scene_spec",
        "owner_id": 11,
        "novel_id": 22,
        "spec_key": "spec-arin",
        "revision_number": 1,
        "scene_candidate_hash": CANDIDATE_HASH,
        "scene_candidate_id": None,
        "visual_bible_revision_hash": VB_HASH,
        "visual_bible_revision_id": None,
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": SNAPSHOT_HASH,
        "cutoff_chapter": 8,
        "schema_hash": SCHEMA_HASH,
        "compiler_id": "scene-spec-compiler.v1",
        "compiler_version": "1.0.0",
        "policy_hash": POLICY_HASH,
        "config_hash": CONFIG_HASH,
        "content_hash": "0" * 64,
        "details": [_detail().model_dump()],
        "negative_constraints": [],
        "uncertainties": [],
        "review_state": "candidate",
    }
    payload.update(overrides)
    spec = SceneSpecContract.model_validate(payload)
    if "content_hash" not in overrides:
        spec = spec.model_copy(update={"content_hash": recompute_scene_spec_hash(spec)})
    return spec


def _prompt(
    spec: SceneSpecContract,
    *,
    adapter_id: str = "mock-a",
    adapter_version: str = "1.0.0",
    render=_mock_adapter_a,
    **overrides,
):
    sections = build_prompt_sections(spec)
    payload = {
        "schema_version": "prompt-revision.v1",
        "artifact_kind": "prompt_revision",
        "owner_id": spec.owner_id,
        "novel_id": spec.novel_id,
        "prompt_key": "prompt-arin",
        "revision_number": 1,
        "parent_prompt_revision_id": None,
        "scene_spec_hash": spec.content_hash,
        "visual_bible_revision_hash": spec.visual_bible_revision_hash,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "schema_hash": spec.schema_hash,
        "prompt_schema_hash": SCHEMA_HASH,
        "compiler_version": spec.compiler_version,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "config_hash": CONFIG_HASH,
        "input_hash": "0" * 64,
        "prompt_hash": "0" * 64,
        "sections": sections,
        "negative_constraints": spec_negative_constraint_texts(spec),
        "uncertainties": spec_uncertainty_texts(spec),
        "prompt_text": render(sections),
        "redacted_preview": render(sections)[:80],
        "review_state": "candidate",
    }
    payload.update(overrides)
    revision = PromptRevisionContract.model_validate(payload)
    if "input_hash" not in overrides:
        revision = revision.model_copy(
            update={"input_hash": recompute_prompt_input_hash(revision, spec)}
        )
    if "prompt_hash" not in overrides:
        revision = revision.model_copy(
            update={"prompt_hash": recompute_prompt_hash(revision)}
        )
    return revision


def _review_event(**overrides):
    payload = {
        "owner_id": 11,
        "novel_id": 22,
        "revision_id": 1,
        "event_key": "ev-approve-1",
        "action": "approve",
        "actor_source": "human",
        "actor": "reader",
        "reason": "spec matches the text",
        "from_review_state": "candidate",
    }
    payload.update(overrides)
    return SpecReviewEventInput.model_validate(payload)


def _scene_spec_view(**overrides):
    spec = _spec()
    payload = {
        "id": 1,
        "owner_id": spec.owner_id,
        "novel_id": spec.novel_id,
        "spec_key": spec.spec_key,
        "revision_number": spec.revision_number,
        "scene_candidate_hash": spec.scene_candidate_hash,
        "scene_candidate_id": None,
        "visual_bible_revision_hash": spec.visual_bible_revision_hash,
        "visual_bible_revision_id": None,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "schema_version": spec.schema_version,
        "schema_hash": spec.schema_hash,
        "compiler_id": spec.compiler_id,
        "compiler_version": spec.compiler_version,
        "policy_hash": spec.policy_hash,
        "content_hash": spec.content_hash,
        "review_state": "candidate",
        "details": [
            {
                "detail_key": d.detail_key,
                "kind": d.kind.value,
                "source": d.source.value,
                "text": d.text,
                "author": d.author,
                "rationale": d.rationale,
                "spoiler_cutoff": d.spoiler_cutoff,
                "evidence_keys": [r.evidence_key for r in d.evidence_refs],
                "visual_bible_stable_ids": [r.stable_id for r in d.visual_bible_refs],
            }
            for d in spec.details
        ],
        "negative_constraints": [],
        "uncertainties": [],
    }
    payload.update(overrides)
    return SceneSpecView.model_validate(payload)


def _frozen_spec_view(**overrides):
    spec = _spec()
    payload = {
        "id": 1,
        "owner_id": spec.owner_id,
        "novel_id": spec.novel_id,
        "spec_key": spec.spec_key,
        "revision_number": spec.revision_number,
        "scene_candidate_hash": spec.scene_candidate_hash,
        "visual_bible_revision_hash": spec.visual_bible_revision_hash,
        "source_snapshot_id": spec.source_snapshot_id,
        "source_snapshot_hash": spec.source_snapshot_hash,
        "cutoff_chapter": spec.cutoff_chapter,
        "schema_version": spec.schema_version,
        "content_hash": spec.content_hash,
        "review_state": "candidate",
    }
    payload.update(overrides)
    return FrozenSceneSpecView.model_validate(payload)


def _frozen_prompt_view(**overrides):
    prompt = _prompt(_spec(), adapter_id="mock-a")
    payload = {
        "id": 1,
        "owner_id": prompt.owner_id,
        "novel_id": prompt.novel_id,
        "prompt_key": prompt.prompt_key,
        "revision_number": prompt.revision_number,
        "scene_spec_hash": prompt.scene_spec_hash,
        "visual_bible_revision_hash": prompt.visual_bible_revision_hash,
        "source_snapshot_id": prompt.source_snapshot_id,
        "source_snapshot_hash": prompt.source_snapshot_hash,
        "cutoff_chapter": prompt.cutoff_chapter,
        "schema_version": prompt.schema_version,
        "prompt_schema_hash": prompt.prompt_schema_hash,
        "adapter_id": prompt.adapter_id,
        "adapter_version": prompt.adapter_version,
        "config_hash": prompt.config_hash,
        "input_hash": prompt.input_hash,
        "prompt_hash": prompt.prompt_hash,
        "sections": prompt.sections,
        "negative_constraints": prompt.negative_constraints,
        "uncertainties": prompt.uncertainties,
        "prompt_text": prompt.prompt_text,
        "review_state": "candidate",
    }
    payload.update(overrides)
    return FrozenPromptRevisionView.model_validate(payload)


# ---------------------------------------------------------------------------
# Vocabulary (closed and pinned)
# ---------------------------------------------------------------------------


def test_spec_vocabulary_is_closed_and_pinned():
    assert [k.value for k in SpecDetailKind] == list(SPEC_DETAIL_KINDS)
    assert [s.value for s in SpecSource] == list(SPEC_SOURCES)
    assert [c.value for c in ConstraintScope] == list(SPEC_CONSTRAINT_SCOPES)
    assert [r.value for r in UncertaintyReason] == list(SPEC_UNCERTAINTY_REASONS)
    assert list(SPEC_SECTION_ORDER) == [
        "subject",
        "action",
        "setting",
        "composition",
        "style",
        "continuity",
        "negative_constraints",
        "uncertainties",
    ]
    assert (
        DETAIL_KINDS_HASH
        == "a329e210e3634013906814192689a085d6b5ed3bc4461e952899438d397a170d"
    )
    assert (
        SOURCES_HASH
        == "6e3979c9f9a6950a2b3a4e5129c4a2b40299c148667fb1324742b6c5eef8ae6d"
    )
    assert (
        SCOPES_HASH
        == "33adb6d6f5e1593ba29796f3894d242187086b8fa968489e7cf48320a5dfc30b"
    )
    assert (
        REASONS_HASH
        == "268df3749d3655bf63e193939c024dcdf2a7537229dc00334c5beccd11ab1106"
    )
    assert (
        SECTIONS_HASH
        == "c2adc0a1dca328bdadf50587782dc6525f164ba1706442fc29a46c295a82f451"
    )


def test_review_vocabulary_is_closed():
    assert [a.value for a in SpecReviewAction] == list(SPEC_REVIEW_ACTIONS)
    assert [s.value for s in SpecReviewState] == list(SPEC_REVIEW_STATES)


# ---------------------------------------------------------------------------
# Strict schema: provider fields rejected, no canon promotion
# ---------------------------------------------------------------------------


def test_strict_schema_rejects_provider_specific_fields():
    # Provider-specific rendering fields must never enter the canonical SceneSpec.
    with pytest.raises(ValidationError):
        _detail(cinematic=True)
    with pytest.raises(ValidationError):
        _detail(lighting="golden hour")
    with pytest.raises(ValidationError):
        _detail(camera_angle="low angle")
    with pytest.raises(ValidationError):
        _constraint(render_style="anime")
    with pytest.raises(ValidationError):
        _spec(prompt="A cinematic wide shot of Arin")
    with pytest.raises(ValidationError):
        _spec(negative_prompt="no text")

    # Provider-specific prompt sections are rejected in the derived revision.
    bad = _prompt(
        _spec(),
        sections={**build_prompt_sections(_spec()), "cinematic_shot": "low angle"},
    )
    with pytest.raises(SceneSpecGateError):
        validate_prompt_revision_contract(bad, _spec())

    # Provider secrets/keys never appear in the lineage envelope.
    prompt = _prompt(_spec(), adapter_id="mock-a")
    lineage = build_prompt_lineage(prompt, _spec())
    with pytest.raises(ValidationError):
        PromptArtifactLineage.model_validate(
            lineage.model_dump() | {"provider_secret": "sk-live"}
        )


def test_scene_spec_is_not_canon():
    with pytest.raises(ValidationError):
        _spec(promote_to_canon=True)
    with pytest.raises(ValidationError):
        _spec(cover_url="http://example.com/cover.jpg")
    fields = set(SceneSpecContract.model_fields)
    assert "canon" not in fields
    assert "active_pointer" not in fields
    assert "current_revision" not in fields
    assert "cover_url" not in fields
    assert "canon_url" not in fields
    # Default state is candidate; approval is an explicit append-only action.
    assert _spec().review_state is SpecReviewState.CANDIDATE


def test_prompt_revision_has_no_promotion_path():
    with pytest.raises(ValidationError):
        _prompt(_spec(), approved=True)
    fields = set(PromptRevisionContract.model_fields)
    assert "canon" not in fields
    assert "active_pointer" not in fields
    assert "cover_url" not in fields
    assert _prompt(_spec()).review_state is SpecReviewState.CANDIDATE


# ---------------------------------------------------------------------------
# Canonical source rule (D-32-02): evidence / Visual Bible / user interpretation
# ---------------------------------------------------------------------------


def test_detail_source_shape_is_enforced():
    # Evidence-sourced details must carry evidence.
    with pytest.raises(ValidationError):
        _detail(source="evidence", evidence_refs=[])
    # Visual Bible-sourced details must carry a VB ref with revision hash.
    with pytest.raises(ValidationError):
        _detail(source="visual_bible", visual_bible_refs=[])
    # user_interpretation requires author + rationale and never carries refs.
    with pytest.raises(ValidationError):
        _detail(source="user_interpretation", author=None, rationale=None)
    with pytest.raises(ValidationError):
        _detail(
            source="user_interpretation",
            author="reader",
            rationale="my reading",
            evidence_refs=[_evidence()],
        )
    ok = _detail(
        source="user_interpretation",
        author="reader",
        rationale="my reading",
        evidence_refs=[],
        visual_bible_refs=[],
    )
    assert ok.source is SpecSource.USER_INTERPRETATION


def test_unsupported_future_spoiler_evidence_rejected():
    # Evidence chapter beyond the spoiler cutoff fails closed at schema level.
    with pytest.raises(ValidationError):
        _evidence(chapter_number=9, cutoff_chapter=8)
    with pytest.raises(ValidationError):
        _detail(
            evidence_refs=[_evidence(chapter_number=9, cutoff_chapter=8).model_dump()]
        )
    ok = _detail(
        evidence_refs=[_evidence(chapter_number=8, cutoff_chapter=8).model_dump()]
    )
    assert ok.spoiler_cutoff == 8


def test_unresolved_material_must_be_labeled_or_uncertainty():
    # A conflicting/unbacked claim cannot be canon: source=evidence without
    # evidence is rejected by the schema, so the only honest homes are
    # user_interpretation (labeled) or uncertainties (unresolved).
    with pytest.raises(ValidationError):
        _detail(
            detail_key="con-cloak",
            kind="continuity",
            source="evidence",
            evidence_refs=[],
            text="she wears a red cloak",
        )

    spec = _spec(
        details=[],
        uncertainties=[
            _uncertainty(
                uncertainty_key="unc-cloak",
                reason="conflicting_claim",
                detail="the cloak color is disputed in chapters 3 and 4",
            ).model_dump()
        ],
    )
    validate_scene_spec_contract(spec)
    sections = build_prompt_sections(spec)
    # Uncertainties are surfaced in their own section, never as canon.
    assert "uncertainties" in sections
    for key, value in sections.items():
        if key != "uncertainties":
            assert "cloak color is disputed" not in value
    assert "conflicting_claim" in sections["uncertainties"]


# ---------------------------------------------------------------------------
# Contract gates: replayable hashes, snapshot/cutoff/VB lineage
# ---------------------------------------------------------------------------


def test_spec_content_hash_is_replayable_and_detects_change():
    a = _spec()
    b = _spec()
    assert recompute_scene_spec_hash(a) == recompute_scene_spec_hash(b)
    assert a.content_hash == recompute_scene_spec_hash(a)
    changed = _spec(
        details=[_detail(detail_key="sub-2", text="different subject").model_dump()]
    )
    assert changed.content_hash != a.content_hash


def test_duplicate_detail_keys_are_rejected():
    d = _detail()
    with pytest.raises(SceneSpecGateError):
        validate_scene_spec_contract(_spec(details=[d.model_dump(), d.model_dump()]))


def test_evidence_lineage_must_match_spec_snapshot_and_cutoff():
    spec = _spec()
    validate_scene_spec_contract(spec)

    bad_snapshot_id = _spec(
        details=[
            _detail(
                evidence_refs=[_evidence(source_snapshot_id="other-ss").model_dump()]
            ).model_dump()
        ]
    )
    with pytest.raises(SceneSpecGateError):
        validate_scene_spec_contract(bad_snapshot_id)

    bad_snapshot_hash = _spec(
        details=[
            _detail(
                evidence_refs=[_evidence(source_snapshot_hash=HEX64_D).model_dump()]
            ).model_dump()
        ]
    )
    with pytest.raises(SceneSpecGateError):
        validate_scene_spec_contract(bad_snapshot_hash)

    bad_cutoff = _spec(
        details=[
            _detail(
                evidence_refs=[_evidence(cutoff_chapter=3).model_dump()]
            ).model_dump()
        ]
    )
    with pytest.raises(SceneSpecGateError):
        validate_scene_spec_contract(bad_cutoff)


def test_visual_bible_revision_lineage_must_match():
    spec = _spec()
    validate_scene_spec_contract(spec)

    wrong_revision = _spec(
        details=[
            _detail(
                detail_key="sub-mara",
                source="visual_bible",
                text="Mara the harbor-runner",
                visual_bible_refs=[
                    _vbref(stable_id="char-mara", revision_hash=HEX64_D)
                ],
            ).model_dump()
        ]
    )
    with pytest.raises(SceneSpecGateError):
        validate_scene_spec_contract(wrong_revision)


def test_valid_spec_passes_full_contract():
    validate_scene_spec_contract(_spec())


# ---------------------------------------------------------------------------
# spec-continuity fixture: two characters + one place with stable VB IDs
# ---------------------------------------------------------------------------


def test_spec_continuity_preserves_stable_visual_bible_ids():
    details = [
        _detail(
            detail_key="sub-arin",
            kind="subject",
            source="visual_bible",
            text="Arin, the sword-bearer",
            visual_bible_refs=[_vbref(stable_id="char-arin")],
        ),
        _detail(
            detail_key="sub-mara",
            kind="subject",
            source="visual_bible",
            text="Mara, the harbor-runner",
            visual_bible_refs=[_vbref(stable_id="char-mara")],
        ),
        _detail(
            detail_key="set-courtyard",
            kind="setting",
            source="visual_bible",
            text="the rain-soaked courtyard",
            visual_bible_refs=[_vbref(stable_id="place-courtyard")],
        ),
        _detail(
            detail_key="con-arin-mara",
            kind="continuity",
            source="visual_bible",
            text="Arin and Mara stand together",
            visual_bible_refs=[
                _vbref(stable_id="char-arin"),
                _vbref(stable_id="char-mara"),
            ],
        ),
    ]
    spec = _spec(details=[d.model_dump() for d in details])
    validate_scene_spec_contract(spec)

    stable_ids = {ref.stable_id for d in spec.details for ref in d.visual_bible_refs}
    assert {"char-arin", "char-mara", "place-courtyard"} <= stable_ids
    for detail in spec.details:
        for ref in detail.visual_bible_refs:
            assert ref.revision_hash == spec.visual_bible_revision_hash

    # Continuity renders into the canonical prompt sections.
    sections = build_prompt_sections(spec)
    assert "Arin and Mara stand together" in sections["continuity"]


# ---------------------------------------------------------------------------
# spec-negative fixture: forbidden costume/era/identity + style exclusions
# ---------------------------------------------------------------------------


def test_negative_constraints_are_preserved_into_prompt():
    constraints = [
        _constraint(
            constraint_key="neg-costume",
            scope="costume",
            source="visual_bible",
            text="no modern clothing",
            visual_bible_refs=[_vbref(stable_id="style-prose")],
        ),
        _constraint(
            constraint_key="neg-era",
            scope="era",
            source="evidence",
            text="no anachronistic technology",
            evidence_refs=[_evidence()],
        ),
        _constraint(
            constraint_key="neg-identity",
            scope="identity",
            source="user_interpretation",
            text="do not depict Arin as a king",
            author="reader",
            rationale="unresolved in the text",
            evidence_refs=[],
            visual_bible_refs=[],
        ),
        _constraint(
            constraint_key="neg-style",
            scope="style",
            source="user_interpretation",
            text="avoid painterly lighting",
            author="editor",
            rationale="house style",
            evidence_refs=[],
            visual_bible_refs=[],
        ),
    ]
    spec = _spec(negative_constraints=[c.model_dump() for c in constraints])
    validate_scene_spec_contract(spec)

    prompt = _prompt(spec, adapter_id="mock-a")
    validate_prompt_revision_contract(prompt, spec)
    assert prompt.negative_constraints == spec_negative_constraint_texts(spec)
    assert "costume: no modern clothing" in prompt.negative_constraints
    assert "no modern clothing" in prompt.sections["negative_constraints"]
    assert "do not depict Arin as a king" in prompt.sections["negative_constraints"]


def test_negative_constraint_scope_is_closed():
    with pytest.raises(ValidationError):
        _constraint(scope="lighting")
    with pytest.raises(ValidationError):
        _constraint(scope="dynamic_range")


# ---------------------------------------------------------------------------
# prompt-golden fixture: deterministic across runs and mock adapters
# ---------------------------------------------------------------------------


def test_prompt_golden_is_deterministic_across_runs():
    spec = _spec()
    a = _prompt(
        spec, adapter_id="mock-a", adapter_version="1.0.0", render=_mock_adapter_a
    )
    b = _prompt(
        spec, adapter_id="mock-a", adapter_version="1.0.0", render=_mock_adapter_a
    )
    assert a == b
    assert a.input_hash == b.input_hash
    assert a.prompt_hash == b.prompt_hash
    validate_prompt_revision_contract(a, spec)
    validate_prompt_revision_contract(b, spec)


def test_prompt_input_hash_is_adapter_independent_output_differs():
    spec = _spec()
    a = _prompt(
        spec, adapter_id="mock-a", adapter_version="1.0.0", render=_mock_adapter_a
    )
    b = _prompt(
        spec, adapter_id="mock-b", adapter_version="2.0.0", render=_mock_adapter_b
    )
    # Canonical sections are provider-neutral: identical across adapters.
    assert a.sections == b.sections
    # The input lineage (spec + canonical sections) is adapter-independent.
    assert a.input_hash == b.input_hash
    # The rendered output is adapter-dependent and differs from the input hash.
    assert a.prompt_hash != b.prompt_hash
    assert a.input_hash != a.prompt_hash
    assert a.prompt_text != b.prompt_text


def test_prompt_lineage_envelope_records_deterministic_lineage():
    spec = _spec()
    prompt = _prompt(spec, adapter_id="mock-a")
    lineage = build_prompt_lineage(prompt, spec)
    assert lineage.scene_spec_hash == spec.content_hash
    assert lineage.visual_bible_revision_hash == spec.visual_bible_revision_hash
    assert lineage.source_snapshot_id == spec.source_snapshot_id
    assert lineage.cutoff_chapter == spec.cutoff_chapter
    assert lineage.input_hash == prompt.input_hash
    assert lineage.prompt_hash == prompt.prompt_hash
    assert lineage.adapter_id == "mock-a"
    with pytest.raises(ValidationError):
        PromptArtifactLineage.model_validate(
            lineage.model_dump() | {"input_hash": lineage.prompt_hash}
        )


# ---------------------------------------------------------------------------
# prompt stale gates: SceneSpec revision change and Visual Bible revision change
# ---------------------------------------------------------------------------


def test_prompt_is_stale_when_scene_spec_revision_changes():
    spec_v1 = _spec(spec_key="spec-arin", revision_number=1)
    prompt_v1 = _prompt(spec_v1, adapter_id="mock-a")
    validate_prompt_revision_contract(prompt_v1, spec_v1)

    spec_v2 = _spec(
        spec_key="spec-arin",
        revision_number=2,
        details=[_detail(detail_key="sub-2", text="different subject").model_dump()],
    )
    assert spec_v2.content_hash != spec_v1.content_hash
    with pytest.raises(SceneSpecGateError):
        validate_prompt_revision_contract(prompt_v1, spec_v2)


def test_visual_bible_revision_change_stales_old_prompt():
    spec_v1 = _spec(visual_bible_revision_hash=VB_HASH, revision_number=1)
    prompt_v1 = _prompt(spec_v1, adapter_id="mock-a")
    validate_prompt_revision_contract(prompt_v1, spec_v1)

    spec_v2 = _spec(visual_bible_revision_hash=HEX64_D, revision_number=2)
    with pytest.raises(SceneSpecGateError):
        validate_prompt_revision_contract(prompt_v1, spec_v2)


# ---------------------------------------------------------------------------
# prompt-edit fixture: user edits become new candidate revisions, diff retained
# ---------------------------------------------------------------------------


def test_prompt_edit_is_new_candidate_revision_with_diff_retained():
    base = _spec(
        details=[
            _detail(
                detail_key="interp-mood",
                kind="style",
                source="user_interpretation",
                text="quietly melancholic",
                author="reader",
                rationale="silences in chapter 3",
                evidence_refs=[],
                visual_bible_refs=[],
            ).model_dump()
        ]
    )
    validate_scene_spec_contract(base)

    edited = _spec(
        spec_key=base.spec_key,
        revision_number=2,
        details=[
            _detail(
                detail_key="interp-mood",
                kind="style",
                source="user_interpretation",
                text="quietly melancholic, candle-lit",
                author="reader",
                rationale="silences in chapter 3",
                evidence_refs=[],
                visual_bible_refs=[],
            ).model_dump()
        ],
    )
    validate_scene_spec_contract(edited)
    assert edited.content_hash != base.content_hash
    # The edited interpretation field is retained in the canonical payload (the
    # diff is a first-class part of the new revision, not a separate prompt text).
    edited_payload = scene_spec_content_payload(edited)
    assert edited_payload["details"][0]["text"] == "quietly melancholic, candle-lit"

    # Old prompt is stale; a new prompt revision references the edited spec.
    old_prompt = _prompt(base, adapter_id="mock-a")
    with pytest.raises(SceneSpecGateError):
        validate_prompt_revision_contract(old_prompt, edited)
    new_prompt = _prompt(
        edited,
        adapter_id="mock-a",
        revision_number=2,
        parent_prompt_revision_id=1,
    )
    validate_prompt_revision_contract(new_prompt, edited)
    assert new_prompt.revision_number == 2
    assert new_prompt.parent_prompt_revision_id == 1


# ---------------------------------------------------------------------------
# Review actions (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------


def test_review_transition_map_is_closed():
    assert set(LEGAL_SPEC_REVIEW_TRANSITIONS) == set(SpecReviewState)
    for state, actions in LEGAL_SPEC_REVIEW_TRANSITIONS.items():
        for action in actions:
            assert action in SPEC_REVIEW_ACTION_TO_STATE
            assert (
                review_state_after(state, action) == SPEC_REVIEW_ACTION_TO_STATE[action]
            )


def test_review_chain_and_idempotency():
    assert review_state_after("candidate", "approve") is SpecReviewState.APPROVED
    assert review_state_after("candidate", "reject") is SpecReviewState.REJECTED
    assert (
        review_state_after("candidate", "needs_relink") is SpecReviewState.NEEDS_RELINK
    )
    assert review_state_after("approved", "supersede") is SpecReviewState.SUPERSEDED
    with pytest.raises(SceneSpecGateError):
        review_state_after("approved", "approve")  # double approval impossible
    with pytest.raises(SceneSpecGateError):
        review_state_after("superseded", "reject")  # terminal

    result = validate_review_event(_review_event())
    assert result is SpecReviewState.APPROVED
    with pytest.raises(SceneSpecGateError):
        validate_review_event(_review_event(), seen_event_keys={"ev-approve-1"})


# ---------------------------------------------------------------------------
# Candidate-only approval gate: frozen envelopes reject unapproved candidates
# ---------------------------------------------------------------------------


def test_frozen_scene_spec_requires_approved_state():
    with pytest.raises(ValidationError):
        _frozen_spec_view(review_state="candidate")
    with pytest.raises(ValidationError):
        _frozen_spec_view(review_state="rejected")
    frozen = _frozen_spec_view(review_state="approved")
    assert frozen.review_state is SpecReviewState.APPROVED


def test_frozen_prompt_revision_requires_approved_state():
    with pytest.raises(ValidationError):
        _frozen_prompt_view(review_state="candidate")
    with pytest.raises(ValidationError):
        _frozen_prompt_view(review_state="needs_relink")
    frozen = _frozen_prompt_view(review_state="approved")
    assert frozen.review_state is SpecReviewState.APPROVED
    assert frozen.prompt_text  # the only generation input (Phase 33)


def test_read_envelopes_load():
    view = _scene_spec_view()
    assert view.review_state is SpecReviewState.CANDIDATE
    assert view.details[0].evidence_keys == ["ev-1"]

    prompt = _prompt(_spec(), adapter_id="mock-a")
    pv = PromptRevisionView.model_validate(
        {
            "id": 1,
            "owner_id": prompt.owner_id,
            "novel_id": prompt.novel_id,
            "prompt_key": prompt.prompt_key,
            "revision_number": prompt.revision_number,
            "scene_spec_hash": prompt.scene_spec_hash,
            "visual_bible_revision_hash": prompt.visual_bible_revision_hash,
            "source_snapshot_id": prompt.source_snapshot_id,
            "source_snapshot_hash": prompt.source_snapshot_hash,
            "cutoff_chapter": prompt.cutoff_chapter,
            "schema_version": prompt.schema_version,
            "schema_hash": prompt.schema_hash,
            "prompt_schema_hash": prompt.prompt_schema_hash,
            "compiler_version": prompt.compiler_version,
            "adapter_id": prompt.adapter_id,
            "adapter_version": prompt.adapter_version,
            "config_hash": prompt.config_hash,
            "input_hash": prompt.input_hash,
            "prompt_hash": prompt.prompt_hash,
            "sections": prompt.sections,
            "negative_constraints": prompt.negative_constraints,
            "uncertainties": prompt.uncertainties,
            "redacted_preview": prompt.redacted_preview,
            "review_state": prompt.review_state.value,
        }
    )
    assert pv.input_hash != pv.prompt_hash
    assert pv.adapter_id == "mock-a"


# ---------------------------------------------------------------------------
# ORM metadata, append-only content rows and migration chain
# ---------------------------------------------------------------------------


def test_scene_spec_tables_are_registered_on_metadata():
    tables = set(SceneSpecVersion.metadata.tables)
    assert SCENE_SPEC_TABLES <= tables
    assert PROMPT_TABLES <= tables


def test_orm_exports_all_scene_spec_entities():
    from app.models import (
        PromptRevision as ExportedPrompt,
        SceneSpecDetail as ExportedDetail,
        SceneSpecEvidenceRef as ExportedEvidence,
        SceneSpecNegativeConstraint as ExportedConstraint,
        SceneSpecUncertainty as ExportedUncertainty,
        SceneSpecVersion as ExportedVersion,
    )

    assert ExportedVersion.__tablename__ == "scene_spec_versions"
    assert ExportedDetail.__tablename__ == "scene_spec_details"
    assert ExportedConstraint.__tablename__ == "scene_spec_negative_constraints"
    assert ExportedEvidence.__tablename__ == "scene_spec_evidence_refs"
    assert ExportedUncertainty.__tablename__ == "scene_spec_uncertainties"
    assert ExportedPrompt.__tablename__ == "prompt_revisions"


def test_spec_orm_carries_owner_novel_version_snapshot_lineage():
    cols = set(inspect(SceneSpecVersion).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "spec_key",
        "revision_number",
        "scene_candidate_id",
        "scene_candidate_hash",
        "visual_bible_revision_id",
        "visual_bible_revision_hash",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "review_state",
        "schema_version",
        "schema_hash",
        "compiler_id",
        "compiler_version",
        "policy_hash",
        "config_hash",
        "content_hash",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneSpecVersion.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "spec_key") in unique
    assert ("owner_id", "novel_id", "id") in unique

    check_names = {
        c.name for c in SceneSpecVersion.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_scene_spec_versions_review_state" in check_names
    assert "ck_scene_spec_versions_candidate_hash" in check_names
    assert "ck_scene_spec_versions_vb_hash" in check_names


def test_detail_orm_enforces_closed_kind_source_and_unique_key():
    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneSpecDetail.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "spec_id", "detail_key") in unique
    check_names = {
        c.name for c in SceneSpecDetail.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_scene_spec_details_kind" in check_names
    assert "ck_scene_spec_details_source" in check_names


def test_evidence_orm_enforces_spoiler_gate_and_owner_shape():
    check_names = {
        c.name for c in SceneSpecEvidenceRef.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_scene_spec_evidence_spoiler_cutoff" in check_names
    assert "ck_scene_spec_evidence_owner" in check_names
    assert "ck_scene_spec_evidence_offsets" in check_names
    assert SceneSpecEvidenceRef.__table__.c.content_hash.type.length == 64
    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneSpecEvidenceRef.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "spec_id", "evidence_key") in unique


def test_prompt_orm_carries_lineage_and_hash_separation():
    cols = set(inspect(PromptRevision).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "prompt_key",
        "revision_number",
        "parent_prompt_revision_id",
        "scene_spec_id",
        "scene_spec_hash",
        "visual_bible_revision_hash",
        "source_snapshot_id",
        "source_snapshot_hash",
        "cutoff_chapter",
        "review_state",
        "schema_version",
        "schema_hash",
        "prompt_schema_hash",
        "compiler_version",
        "adapter_id",
        "adapter_version",
        "config_hash",
        "input_hash",
        "prompt_hash",
        "prompt_text",
        "redacted_preview",
    } <= cols

    unique = {
        tuple(c.name for c in u.columns)
        for u in PromptRevision.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "prompt_key") in unique

    check_names = {
        c.name for c in PromptRevision.__table__.constraints if hasattr(c, "name")
    }
    assert "ck_prompt_revisions_hash_separation" in check_names
    assert "ck_prompt_revisions_review_state" in check_names
    assert "ck_prompt_revisions_spec_hash" in check_names


def test_negative_constraint_orm_enforces_closed_scope():
    check_names = {
        c.name
        for c in SceneSpecNegativeConstraint.__table__.constraints
        if hasattr(c, "name")
    }
    assert "ck_scene_spec_constraints_scope" in check_names
    unique = {
        tuple(c.name for c in u.columns)
        for u in SceneSpecNegativeConstraint.__table__.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }
    assert ("owner_id", "novel_id", "spec_id", "constraint_key") in unique


async def _user_and_novel(db_session: AsyncSession, username: str):
    user = User(
        username=username,
        email=f"{username}@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()
    novel = Novel(title=f"Scene Spec Novel {username}", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()
    return user, novel


async def _persist_spec(
    db_session: AsyncSession, *, username: str
) -> tuple[SceneSpecVersion, User, Novel]:
    owner, novel = await _user_and_novel(db_session, username)
    row = SceneSpecVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        spec_key="spec-append",
        revision_number=1,
        scene_candidate_id=None,
        scene_candidate_hash=CANDIDATE_HASH,
        visual_bible_revision_id=None,
        visual_bible_revision_hash=VB_HASH,
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        cutoff_chapter=8,
        review_state="candidate",
        schema_version="scene-spec.v1",
        schema_hash=SCHEMA_HASH,
        compiler_id="scene-spec-compiler.v1",
        compiler_version="1.0.0",
        policy_hash=POLICY_HASH,
        config_hash=CONFIG_HASH,
        content_hash=HEX64,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64,
        projection_hash=HEX64,
    )
    db_session.add(row)
    await db_session.flush()
    return row, owner, novel


async def test_detail_row_is_append_only(db_session: AsyncSession):
    spec_row, owner, novel = await _persist_spec(
        db_session, username="ss_append_detail"
    )
    detail_row = SceneSpecDetail(
        owner_id=owner.id,
        novel_id=novel.id,
        spec_id=spec_row.id,
        detail_key="sub-1",
        kind="subject",
        source="evidence",
        text="Arin draws his sword",
        author=None,
        rationale=None,
        spoiler_cutoff=8,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="scene-spec.v1",
    )
    db_session.add(detail_row)
    await db_session.flush()
    detail_row.text = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_evidence_ref_row_is_append_only(db_session: AsyncSession):
    spec_row, owner, novel = await _persist_spec(
        db_session, username="ss_append_evidence"
    )
    detail_row = SceneSpecDetail(
        owner_id=owner.id,
        novel_id=novel.id,
        spec_id=spec_row.id,
        detail_key="sub-1",
        kind="subject",
        source="evidence",
        text="Arin draws his sword",
        author=None,
        rationale=None,
        spoiler_cutoff=8,
        canonical_payload={},
        canonical_payload_hash=HEX64,
        idempotency_key=HEX64_B,
        projection_hash=HEX64,
        schema_version="scene-spec.v1",
    )
    db_session.add(detail_row)
    await db_session.flush()

    evidence_row = SceneSpecEvidenceRef(
        owner_id=owner.id,
        novel_id=novel.id,
        spec_id=spec_row.id,
        detail_id=detail_row.id,
        constraint_id=None,
        evidence_key="ev-1",
        source_snapshot_id="ss-1",
        source_snapshot_hash=SNAPSHOT_HASH,
        chapter_id=None,
        chapter_number=3,
        source_start=0,
        source_end=120,
        content_hash=HEX64_D,
        excerpt=None,
        cutoff_chapter=8,
        idempotency_key=HEX64_C,
    )
    db_session.add(evidence_row)
    await db_session.flush()
    evidence_row.excerpt = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


async def test_uncertainty_row_is_append_only(db_session: AsyncSession):
    spec_row, owner, novel = await _persist_spec(
        db_session, username="ss_append_uncertainty"
    )
    uncertainty_row = SceneSpecUncertainty(
        owner_id=owner.id,
        novel_id=novel.id,
        spec_id=spec_row.id,
        uncertainty_key="unc-1",
        reason="conflicting_claim",
        detail="the cloak color is disputed",
        idempotency_key=HEX64_B,
    )
    db_session.add(uncertainty_row)
    await db_session.flush()
    uncertainty_row.detail = "mutated"
    with pytest.raises(ValueError):
        await db_session.flush()


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chain_is_serial_on_top_of_key_scene_head():
    migration = _load_migration("20260801_scene_spec_prompt.py")
    assert migration.revision == "20260801_scene_spec_prompt"
    assert migration.down_revision == "20260801_key_scene"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
    assert "scene_spec_versions" in migration.__doc__
    assert "'needs_relink'" in migration._REVIEW_STATES
    assert "'user_interpretation'" in migration._SOURCES


def test_migration_matches_orm_table_set():
    migration = _load_migration("20260801_scene_spec_prompt.py")
    # The migration docstring declares every ORM table (upgrade creates the same
    # set the ORM registers on metadata).
    for table in SCENE_SPEC_TABLES | PROMPT_TABLES:
        assert table in migration.__doc__


def test_no_cover_or_active_pointer_crossing_in_contracts():
    """SceneSpec/PromptRevision never reuse cover_url and have no active pointer
    (D-32-01); provider prompts are derived, never source truth."""
    for contract in (SceneSpecContract, PromptRevisionContract):
        fields = set(contract.model_fields)
        assert "cover_url" not in fields
        assert "active_pointer" not in fields
        assert "current_revision" not in fields
        assert "canon_url" not in fields
        assert "promote_to_canon" not in fields
