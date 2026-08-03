"""Phase 32-03 Prompt golden + replayable serialization tests (REQ-VIS-03).

Covers D-32-03/D-32-04 (VALIDATION prompt-golden / prompt-edit fixtures):

- prompt golden: the same SceneSpec compiled through two mock adapters is
  deterministic across runs; canonical sections and ``input_hash`` are
  adapter-independent while the rendered ``prompt_hash`` differs;
- replayable serialization: a PromptArtifact round-trips through its canonical
  JSON payload and re-derives identically; tampering fails closed;
- prompt edit: a human change to a ``user_interpretation`` field produces an
  explicit new candidate revision (new prompt_key, revision_number + 1, parent
  link) and the diff against the parent is retained and auditable;
- unsupported edits (evidence-sourced details, no-op edits) fail closed;
- canonical payload hash replay from the stored spec payload.
"""

from __future__ import annotations

import json

import pytest

from app.schemas.scene_spec import (
    SceneSpecGateError,
    SpecDetailKind,
    prompt_input_payload,
    validate_prompt_revision_contract,
)
from app.services.prompt_compiler.adapters import (
    BlockPromptAdapter,
    MockPromptAdapter,
    PromptArtifact,
    PromptEditInput,
    compile_prompt,
)
from app.services.prompt_compiler.serialization import (
    PromptDiffSection,
    diff_prompt_revisions,
    edited_spec_with_interpretation,
    prompt_artifact_hash,
    recompute_hash_from_canonical_payload,
    redacted_preview,
    replay_prompt_artifact,
    replay_prompt_revision,
    serialize_prompt_artifact,
    serialize_prompt_revision,
)
from app.schemas.scene_spec import scene_spec_content_payload
from tests.unit.prompt_compiler.test_adapters import _spec

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# prompt-golden: deterministic across runs and mock adapters
# ---------------------------------------------------------------------------


def test_golden_is_deterministic_across_runs():
    spec = _spec()
    a = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden")
    b = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden")
    assert a == b
    assert a.input_hash == b.input_hash
    assert a.prompt_hash == b.prompt_hash
    assert serialize_prompt_revision(a) == serialize_prompt_revision(b)
    # The golden prompt fully replays from its SceneSpec (D-32-03).
    validate_prompt_revision_contract(a, spec)
    validate_prompt_revision_contract(b, spec)


def test_golden_input_lineage_is_adapter_independent_output_is_not():
    spec = _spec()
    mock = compile_prompt(spec, adapter=MockPromptAdapter(), prompt_key="pk-main")
    block = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")

    # Canonical sections are provider-neutral: identical across adapters.
    assert mock.sections == block.sections
    assert mock.negative_constraints == block.negative_constraints
    assert mock.uncertainties == block.uncertainties
    # The adapter-independent input lineage differs only by the pinned adapter
    # config hash; canonical meaning never changes between adapters.
    mock_input = prompt_input_payload(mock, spec)
    block_input = prompt_input_payload(block, spec)
    assert set(mock_input) == set(block_input)
    for key in mock_input:
        if key == "config_hash":
            continue
        assert mock_input[key] == block_input[key], key
    # The rendered output differs per adapter, and both hashes are recorded and
    # always differ (D-32-03).
    assert mock.prompt_hash != block.prompt_hash
    assert mock.input_hash != mock.prompt_hash
    assert block.input_hash != block.prompt_hash


def test_golden_negative_constraints_are_preserved():
    spec = _spec()
    for adapter in (MockPromptAdapter(), BlockPromptAdapter()):
        revision = compile_prompt(spec, adapter=adapter, prompt_key="pk-neg")
        assert "costume: no modern clothing" in revision.negative_constraints
        assert "no modern clothing" in revision.sections["negative_constraints"]
        assert "conflicting_claim" in revision.uncertainties[0]


# ---------------------------------------------------------------------------
# Replayable serialization (deterministic lineage)
# ---------------------------------------------------------------------------


def test_artifact_serialization_round_trips_and_replays():
    spec = _spec()
    artifact = PromptArtifact.build(
        compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden"),
        spec,
        provider_calls=0,
    )
    payload = json.loads(serialize_prompt_artifact(artifact))
    replayed = replay_prompt_artifact(payload, spec)
    assert replayed.revision == artifact.revision
    assert replayed.lineage == artifact.lineage
    assert replayed.provider_calls == 0
    assert prompt_artifact_hash(replayed) == prompt_artifact_hash(artifact)


def test_revision_serialization_round_trips():
    spec = _spec()
    revision = compile_prompt(spec, adapter=MockPromptAdapter(), prompt_key="pk-golden")
    payload = json.loads(serialize_prompt_revision(revision))
    replayed = replay_prompt_revision(payload)
    assert replayed == revision
    assert replayed.prompt_hash == revision.prompt_hash


def test_serialization_fails_closed_on_tampered_prompt_text():
    spec = _spec()
    artifact = PromptArtifact.build(
        compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden"),
        spec,
    )
    payload = json.loads(serialize_prompt_artifact(artifact))
    # Tamper with the rendered output: the prompt_hash no longer replays.
    payload["revision"]["prompt_text"] = payload["revision"]["prompt_text"] + "\nMAYBE MORE"
    with pytest.raises(SceneSpecGateError):
        replay_prompt_artifact(payload, spec)


def test_serialization_fails_closed_on_provider_section_injection():
    spec = _spec()
    artifact = PromptArtifact.build(
        compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden"),
        spec,
    )
    payload = json.loads(serialize_prompt_artifact(artifact))
    # Inject a provider-specific section: canonical meaning must not change.
    payload["revision"]["sections"]["cinematic_shot"] = "low angle"
    with pytest.raises(SceneSpecGateError):
        replay_prompt_artifact(payload, spec)


def test_serialization_fails_closed_on_lineage_drift():
    spec = _spec()
    artifact = PromptArtifact.build(
        compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-golden"),
        spec,
    )
    payload = json.loads(serialize_prompt_artifact(artifact))
    payload["lineage"]["visual_bible_revision_hash"] = "e" * 64
    with pytest.raises(SceneSpecGateError):
        replay_prompt_artifact(payload, spec)


def test_canonical_payload_hash_replays_from_stored_payload():
    spec = _spec()
    # scene_spec_content_payload is itself canonical: its hash replays the
    # stored spec content hash (used to verify edited spec content stored with
    # an edited prompt revision).
    stored = scene_spec_content_payload(spec)
    assert recompute_hash_from_canonical_payload(stored) == spec.content_hash


def test_redacted_preview_truncates_safely():
    assert redacted_preview("short prompt") == "short prompt"
    long_text = "x" * 300
    assert redacted_preview(long_text, max_chars=100) == "x" * 100
    assert redacted_preview(long_text, max_chars=None) == long_text


# ---------------------------------------------------------------------------
# prompt-edit: explicit candidate revision with retained diff (D-32-04)
# ---------------------------------------------------------------------------


def test_prompt_edit_creates_explicit_candidate_with_retained_diff():
    spec = _spec()
    base = compile_prompt(
        spec, adapter=MockPromptAdapter(), prompt_key="pk-base", revision_number=1
    )
    validate_prompt_revision_contract(base, spec)

    edited_spec = edited_spec_with_interpretation(
        spec,
        edit=PromptEditInput(
            detail_key="interp-mood",
            kind=SpecDetailKind.STYLE,
            text="quietly melancholic, candle-lit",
            author="reader",
            rationale="silences in chapter 3",
        ),
    )
    assert edited_spec.revision_number == spec.revision_number + 1
    assert edited_spec.content_hash != spec.content_hash

    edited_prompt = compile_prompt(
        edited_spec,
        adapter=MockPromptAdapter(),
        prompt_key="pk-edited",
        revision_number=base.revision_number + 1,
        parent_prompt_revision_id=7,
    )
    # The edited prompt replays from the edited spec.
    validate_prompt_revision_contract(edited_prompt, edited_spec)
    # The old prompt is stale against the edited spec (cannot be silently reused).
    with pytest.raises(SceneSpecGateError):
        validate_prompt_revision_contract(base, edited_spec)

    diff = diff_prompt_revisions(base, edited_prompt)
    assert diff.parent_prompt_revision_id == 7
    assert diff.revision_number == 2
    assert not diff.same
    assert diff.prompt_text_changed
    # The interpretation change lands in the style section diff.
    assert any(s.section_key == "style" for s in diff.changed_sections)
    assert any(
        isinstance(s, PromptDiffSection) and "candle-lit" in (s.current or "")
        for s in diff.changed_sections
    )


def test_prompt_edit_adds_new_interpretation_detail():
    spec = _spec()
    edited = edited_spec_with_interpretation(
        spec,
        edit=PromptEditInput(
            detail_key="interp-light",
            kind=SpecDetailKind.STYLE,
            text="candle-lit shadows",
            author="editor",
            rationale="house visual style",
        ),
    )
    keys = [d.detail_key for d in edited.details]
    assert "interp-light" in keys
    assert edited.content_hash != spec.content_hash
    from app.schemas.scene_spec import SpecSource

    added = next(d for d in edited.details if d.detail_key == "interp-light")
    assert added.source is SpecSource.USER_INTERPRETATION
    assert added.author == "editor"
    assert added.rationale == "house visual style"


def test_prompt_edit_rejects_evidence_sourced_detail():
    spec = _spec()
    with pytest.raises(SceneSpecGateError):
        edited_spec_with_interpretation(
            spec,
            edit=PromptEditInput(
                detail_key="sub-arin",  # visual_bible-sourced
                kind=SpecDetailKind.SUBJECT,
                text="Arin, the storm-king",
                author="reader",
                rationale="my reading",
            ),
        )


def test_prompt_edit_rejects_canon_detail_and_action_detail():
    spec = _spec()
    with pytest.raises(SceneSpecGateError):
        edited_spec_with_interpretation(
            spec,
            edit=PromptEditInput(
                detail_key="action:scene-1",  # evidence-sourced
                kind=SpecDetailKind.ACTION,
                text="rewritten action",
                author="reader",
                rationale="my reading",
            ),
        )


def test_prompt_edit_noop_fails_closed():
    spec = _spec()
    interp = next(d for d in spec.details if d.detail_key == "interp-mood")
    with pytest.raises(SceneSpecGateError):
        edited_spec_with_interpretation(
            spec,
            edit=PromptEditInput(
                detail_key="interp-mood",
                kind=SpecDetailKind.STYLE,
                text=interp.text,  # identical → no change
                author=interp.author or "reader",
                rationale=interp.rationale or "",
            ),
        )


def test_diff_is_empty_for_identical_revisions():
    spec = _spec()
    revision = compile_prompt(spec, adapter=MockPromptAdapter(), prompt_key="pk-main")
    diff = diff_prompt_revisions(revision, revision)
    assert diff.same
    assert diff.changed_sections == ()
    assert diff.changed_negative_constraints == ()
    assert diff.changed_uncertainties == ()
    assert not diff.prompt_text_changed


def test_edit_keeps_negative_constraints_intact():
    spec = _spec()
    edited = edited_spec_with_interpretation(
        spec,
        edit=PromptEditInput(
            detail_key="interp-mood",
            kind=SpecDetailKind.STYLE,
            text="quietly melancholic, candle-lit",
            author="reader",
            rationale="silences in chapter 3",
        ),
    )
    edited_prompt = compile_prompt(
        edited, adapter=BlockPromptAdapter(), prompt_key="pk-edited"
    )
    assert "costume: no modern clothing" in edited_prompt.negative_constraints
    assert edited_prompt.negative_constraints == [
        "costume: no modern clothing"
    ]
