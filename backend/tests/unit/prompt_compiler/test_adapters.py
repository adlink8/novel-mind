"""Phase 32-03 Provider Prompt Adapter contract tests (REQ-VIS-03).

Covers D-32-01..D-32-03 for the provider-neutral → provider-specific adapter
layer:

- the ``PromptAdapter`` protocol registry: mock + configured adapter are both
  registered and unknown adapters fail closed;
- both adapters pass the prompt contract gate; canonical meaning is unchanged
  (identical sections and adapter-independent ``input_hash``) while the
  rendered output (``prompt_hash``/``prompt_text``) is adapter-specific;
- provider tokens never enter the SceneSpec (adapter branching is not written
  back, D-32-01) and provider fields never appear in canonical sections;
- the mock adapter is byte-identical to the Phase 32-02 derivation so the whole
  chain replays;
- unsupported detail / invalid spec fail closed and produce no prompt.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.scene_spec import (
    NegativeConstraint,
    PromptArtifactLineage,
    SceneDetail,
    SceneSpecContract,
    SceneSpecGateError,
    SceneUncertainty,
    SpecEvidenceRef,
    SpecSource,
    VisualBibleRef,
    build_prompt_lineage,
    build_prompt_sections,
    prompt_input_payload,
    recompute_scene_spec_hash,
    validate_prompt_revision_contract,
)
from app.services.prompt_compiler.adapters import (
    ADAPTER_REGISTRY,
    MOCK_PROMPT_ADAPTER_ID,
    BlockPromptAdapter,
    MockPromptAdapter,
    PromptCompileError,
    adapter_config_hash,
    compile_prompt,
    get_adapter,
)
from app.services.scene_spec.compiler import (
    MOCK_PROMPT_CONFIG_HASH,
    build_prompt_revision_from_spec,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64
HEX64_D = "d" * 64

VB_HASH = HEX64  # approved Visual Bible revision the spec is frozen against
CANDIDATE_HASH = HEX64_B  # source SceneCandidate content hash
SNAPSHOT_HASH = HEX64_C  # source snapshot hash
SCHEMA_HASH = HEX64_D  # scene-spec schema hash
POLICY_HASH = HEX64  # compiler policy hash
CONFIG_HASH = HEX64_B  # compiler config hash
CUTOFF = 8


# ---------------------------------------------------------------------------
# Fixture builders (shared with test_golden.py via re-import)
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
        "cutoff_chapter": CUTOFF,
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
        "source": "visual_bible",
        "text": "Arin, a tall swordsman in a grey cloak",
        "author": None,
        "rationale": None,
        "evidence_refs": [],
        "visual_bible_refs": [_vbref().model_dump()],
        "spoiler_cutoff": CUTOFF,
    }
    payload.update(overrides)
    return SceneDetail.model_validate(payload)


def _action_detail(**overrides):
    payload = {
        "detail_key": "action:scene-1",
        "kind": "action",
        "source": "evidence",
        "text": "Arin drew his sword as the rain fell across the courtyard walls.",
        "author": None,
        "rationale": None,
        "evidence_refs": [_evidence().model_dump()],
        "visual_bible_refs": [],
        "spoiler_cutoff": CUTOFF,
    }
    payload.update(overrides)
    return SceneDetail.model_validate(payload)


def _interpretation_detail(**overrides):
    payload = {
        "detail_key": "interp-mood",
        "kind": "style",
        "source": "user_interpretation",
        "text": "quietly melancholic",
        "author": "reader",
        "rationale": "silences in chapter 3",
        "evidence_refs": [],
        "visual_bible_refs": [],
        "spoiler_cutoff": CUTOFF,
    }
    payload.update(overrides)
    return SceneDetail.model_validate(payload)


def _constraint(**overrides):
    payload = {
        "constraint_key": "neg-costume",
        "scope": "costume",
        "source": "visual_bible",
        "text": "no modern clothing",
        "author": None,
        "rationale": None,
        "evidence_refs": [],
        "visual_bible_refs": [_vbref(stable_id="style-prose").model_dump()],
        "spoiler_cutoff": CUTOFF,
    }
    payload.update(overrides)
    return NegativeConstraint.model_validate(payload)


def _uncertainty(**overrides):
    payload = {
        "uncertainty_key": "unc-cloak",
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
        "cutoff_chapter": CUTOFF,
        "schema_hash": SCHEMA_HASH,
        "compiler_id": "scene-spec-compiler.v1",
        "compiler_version": "1.0.0",
        "policy_hash": POLICY_HASH,
        "config_hash": CONFIG_HASH,
        "content_hash": "0" * 64,
        "details": [
            _detail().model_dump(),
            _action_detail().model_dump(),
            _interpretation_detail().model_dump(),
        ],
        "negative_constraints": [_constraint().model_dump()],
        "uncertainties": [_uncertainty().model_dump()],
        "review_state": "candidate",
    }
    payload.update(overrides)
    spec = SceneSpecContract.model_validate(payload)
    if "content_hash" not in overrides:
        spec = spec.model_copy(update={"content_hash": recompute_scene_spec_hash(spec)})
    return spec


# ---------------------------------------------------------------------------
# Adapter registry (Task 1 contract)
# ---------------------------------------------------------------------------


def test_adapter_registry_registers_mock_and_configured_adapters():
    assert MockPromptAdapter.adapter_id in ADAPTER_REGISTRY
    assert BlockPromptAdapter.adapter_id in ADAPTER_REGISTRY
    assert get_adapter(MockPromptAdapter.adapter_id).adapter_id == "mock-provider"
    assert get_adapter(BlockPromptAdapter.adapter_id).adapter_id == "prompt-blocks.v1"


def test_unknown_adapter_fails_closed():
    with pytest.raises(PromptCompileError):
        get_adapter("not-a-provider")


def test_adapter_config_hash_is_deterministic_and_pinned():
    a = adapter_config_hash("mock-provider", "1.0.0")
    b = adapter_config_hash("mock-provider", "1.0.0")
    assert a == b
    assert len(a) == 64
    # The mock adapter config hash matches the Phase 32-02 pinned hash so the
    # whole chain replays byte-identically.
    assert a == MOCK_PROMPT_CONFIG_HASH
    # Different adapter id/version must change the hash.
    assert a != adapter_config_hash("mock-provider", "1.0.1")
    assert a != adapter_config_hash("prompt-blocks.v1", "1.0.0")


# ---------------------------------------------------------------------------
# Both adapters pass the contract; canonical meaning is unchanged
# ---------------------------------------------------------------------------


def test_mock_adapter_is_byte_identical_to_phase_32_derivation():
    spec = _spec()
    via_adapter = compile_prompt(
        spec, adapter=MockPromptAdapter(), prompt_key="pk-main"
    )
    via_compiler = build_prompt_revision_from_spec(spec, prompt_key="pk-main")
    assert via_adapter == via_compiler
    assert via_adapter.input_hash == via_compiler.input_hash
    assert via_adapter.prompt_hash == via_compiler.prompt_hash


def test_both_adapters_pass_contract_and_share_canonical_meaning():
    spec = _spec()
    mock = compile_prompt(spec, adapter=MockPromptAdapter(), prompt_key="pk-main")
    block = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")

    # Both adapters pass the fail-closed contract gate.
    validate_prompt_revision_contract(mock, spec)
    validate_prompt_revision_contract(block, spec)

    # Canonical meaning is unchanged: identical canonical sections and
    # negative/uncertainty lists across adapters.
    assert mock.sections == block.sections == build_prompt_sections(spec)
    assert mock.negative_constraints == block.negative_constraints
    assert mock.uncertainties == block.uncertainties

    # The adapter-independent input lineage differs only by the adapter config
    # hash (each adapter pins its own id/version config); the canonical content
    # is identical, so no adapter branch ever leaks into the spec.
    mock_input = prompt_input_payload(mock, spec)
    block_input = prompt_input_payload(block, spec)
    assert set(mock_input) == set(block_input)
    for key in mock_input:
        if key == "config_hash":
            continue
        assert mock_input[key] == block_input[key], key

    # The rendered output is adapter-specific.
    assert mock.prompt_hash != block.prompt_hash
    assert mock.prompt_text != block.prompt_text
    assert mock.input_hash != mock.prompt_hash
    assert block.input_hash != block.prompt_hash

    # Adapter version/identity is recorded on the output revision.
    assert mock.adapter_id == MOCK_PROMPT_ADAPTER_ID
    assert block.adapter_id == "prompt-blocks.v1"
    assert block.adapter_version == "1.0.0"


def test_provider_tokens_never_enter_scene_spec_or_canonical_sections():
    spec = _spec()
    block = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")

    # Provider tokens only exist in the rendered prompt string.
    assert "== style ==" in block.prompt_text

    # They never appear in canonical sections or in the SceneSpec.
    for section_text in block.sections.values():
        assert "== " not in section_text
    for section_text in build_prompt_sections(spec).values():
        assert "== " not in section_text
    for detail in spec.details:
        assert "== " not in detail.text

    # The SceneSpec schema rejects provider-specific fields outright.
    with pytest.raises(ValidationError):
        _detail(cinematic=True)
    with pytest.raises(ValidationError):
        _detail(camera_angle="low angle")


def test_compile_prompt_records_deterministic_lineage():
    spec = _spec()
    revision = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")
    lineage = build_prompt_lineage(revision, spec)
    assert lineage.scene_spec_hash == spec.content_hash
    assert lineage.visual_bible_revision_hash == spec.visual_bible_revision_hash
    assert lineage.source_snapshot_id == spec.source_snapshot_id
    assert lineage.source_snapshot_hash == spec.source_snapshot_hash
    assert lineage.cutoff_chapter == spec.cutoff_chapter
    assert lineage.adapter_id == "prompt-blocks.v1"
    assert lineage.adapter_version == "1.0.0"
    assert lineage.input_hash == revision.input_hash
    assert lineage.prompt_hash == revision.prompt_hash
    # input_hash and prompt_hash must always differ (D-32-03).
    assert lineage.input_hash != lineage.prompt_hash
    with pytest.raises(ValidationError):
        PromptArtifactLineage.model_validate(
            lineage.model_dump() | {"input_hash": lineage.prompt_hash}
        )


def test_negative_constraints_and_uncertainties_survive_both_adapters():
    spec = _spec()
    for adapter in (MockPromptAdapter(), BlockPromptAdapter()):
        revision = compile_prompt(spec, adapter=adapter, prompt_key="pk-neg")
        assert "costume: no modern clothing" in revision.negative_constraints
        assert "conflicting_claim" in revision.uncertainties[0]
        # Uncertainties are surfaced separately, never in a positive section.
        for section_key, section_text in revision.sections.items():
            if section_key == "uncertainties":
                continue
            assert "cloak color is disputed" not in section_text


# ---------------------------------------------------------------------------
# Fail-closed gates
# ---------------------------------------------------------------------------


def test_compile_fails_closed_on_spec_hash_drift():
    spec = _spec()
    tampered = spec.model_copy(update={"content_hash": HEX64_B})
    with pytest.raises((SceneSpecGateError, PromptCompileError)):
        compile_prompt(tampered, adapter=MockPromptAdapter(), prompt_key="pk-bad")


def test_compile_fails_closed_on_vb_revision_drift():
    # A Visual Bible ref whose revision_hash no longer matches the spec.
    bad = _spec(
        details=[
            _detail(
                visual_bible_refs=[_vbref(revision_hash=HEX64_D).model_dump()]
            ).model_dump(),
            _action_detail().model_dump(),
            _interpretation_detail().model_dump(),
        ]
    )
    with pytest.raises(SceneSpecGateError):
        compile_prompt(bad, adapter=MockPromptAdapter(), prompt_key="pk-bad")


def test_compile_fails_closed_on_spoiler_cutoff_drift():
    # A spec whose details carry a different spoiler cutoff than the spec's own
    # cutoff chapter fails closed at compile time.
    bad = _spec(
        details=[
            _detail().model_dump(),
            _action_detail().model_dump(),
            _interpretation_detail().model_dump(),
        ],
        cutoff_chapter=3,  # details still carry spoiler_cutoff=8
    )
    with pytest.raises(SceneSpecGateError):
        compile_prompt(bad, adapter=MockPromptAdapter(), prompt_key="pk-bad")


def test_unsupported_detail_cannot_be_disguised_as_canon():
    # A conflicting/ambiguous detail must be labeled interpretation or live in
    # uncertainties; the strict schema rejects unbacked canon outright.
    with pytest.raises(ValidationError):
        _detail(
            detail_key="sub-mystery",
            source=SpecSource.EVIDENCE.value,
            evidence_refs=[],
        )
    # Uncertainties are rendered in their own section, never as positive canon.
    spec = _spec()
    revision = compile_prompt(spec, adapter=MockPromptAdapter(), prompt_key="pk-unc")
    assert revision.uncertainties
    for section_key, section_text in revision.sections.items():
        if section_key == "uncertainties":
            continue
        for uncertainty in revision.uncertainties:
            assert uncertainty not in section_text


def test_compile_prompt_is_deterministic_for_same_spec_and_adapter():
    spec = _spec()
    a = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")
    b = compile_prompt(spec, adapter=BlockPromptAdapter(), prompt_key="pk-main")
    assert a == b
    assert a.input_hash == b.input_hash
    assert a.prompt_hash == b.prompt_hash


def test_changed_spec_changes_prompt_hash():
    spec_a = _spec()
    spec_b = _spec(
        details=[
            _detail(text="Arin, a tall swordsman in a dark cloak").model_dump(),
            _action_detail().model_dump(),
            _interpretation_detail().model_dump(),
        ]
    )
    assert spec_a.content_hash != spec_b.content_hash
    prompt_a = compile_prompt(spec_a, adapter=MockPromptAdapter(), prompt_key="pk-main")
    prompt_b = compile_prompt(spec_b, adapter=MockPromptAdapter(), prompt_key="pk-main")
    assert prompt_a.prompt_hash != prompt_b.prompt_hash
