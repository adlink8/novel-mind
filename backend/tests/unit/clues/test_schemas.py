"""Strict clue schema contracts (fiction-only, extra=forbid, typed links)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.clue import (
    ClueEvidenceRef,
    ClueHumanActionRequest,
    ClueLifecycleEventContract,
    ClueLifecycleState,
    ClueLinkContract,
    ClueLinkTargetKind,
    ClueLinkValidationStatus,
    ClueOverrideAction,
    ClueOverrideContract,
    ClueSemanticJudgment,
    ClueVersionLineage,
    ClueVisibleEnvelope,
    MachineClueContract,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64


def _cue(**overrides):
    base = {
        "evidence_id": "ev-cue-1",
        "role": "cue",
        "chapter_id": 1,
        "narrative_chapter_number": 1,
        "source_start": 0,
        "source_end": 40,
        "content_hash": HEX64,
    }
    base.update(overrides)
    return base


def _payoff(**overrides):
    base = {
        "evidence_id": "ev-pay-1",
        "role": "payoff",
        "chapter_id": 5,
        "narrative_chapter_number": 5,
        "source_start": 10,
        "source_end": 50,
        "content_hash": HEX64_B,
    }
    base.update(overrides)
    return base


def test_evidence_ref_rejects_bad_offsets_and_hash():
    ClueEvidenceRef.model_validate(_cue())
    with pytest.raises(ValidationError):
        ClueEvidenceRef.model_validate(_cue(source_end=0, source_start=10))
    with pytest.raises(ValidationError):
        ClueEvidenceRef.model_validate(_cue(content_hash="not-hex"))


def test_machine_clue_and_version_lineage_strict():
    lineage = ClueVersionLineage.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "version_key": "v1",
            "source_snapshot_hash": HEX64,
            "hierarchy_build_id": "build-1",
            "hierarchy_checksum": HEX64,
            "prompt_hash": HEX64,
            "schema_hash": HEX64,
            "decoding_hash": HEX64,
            "config_hash": HEX64,
            "policy_hash": HEX64,
        }
    )
    assert lineage.status.value == "candidate"

    clue = MachineClueContract.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "version_id": 3,
            "logical_clue_id": "clue-1",
            "title": "Broken seal",
            "package_hash": HEX64,
            "confidence": 0.8,
            "evidence": [_cue()],
        }
    )
    assert clue.publication_status == "provisional"

    with pytest.raises(ValidationError):
        MachineClueContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "version_id": 3,
                "logical_clue_id": "clue-1",
                "title": "Broken seal",
                "package_hash": HEX64,
                "confidence": 0.8,
                "current_status": "active",  # mutable authority forbidden
            }
        )


def test_lifecycle_event_contract_requires_legal_evidence():
    ok = ClueLifecycleEventContract.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "version_id": 3,
            "logical_clue_id": "clue-1",
            "from_status": "candidate",
            "to_status": "active",
            "actor_source": "machine",
            "reason": "cue accepted",
            "evidence": [_cue()],
            "event_key": "cand-active-1",
        }
    )
    assert ok.to_status == ClueLifecycleState.ACTIVE

    with pytest.raises(ValidationError):
        ClueLifecycleEventContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "version_id": 3,
                "logical_clue_id": "clue-1",
                "from_status": "candidate",
                "to_status": "active",
                "actor_source": "machine",
                "reason": "no cue",
                "evidence": [],
                "event_key": "bad",
            }
        )


def test_link_requires_exactly_one_target_and_rejects_chat_fields():
    char_link = ClueLinkContract.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "version_id": 3,
            "logical_clue_id": "clue-1",
            "target_kind": "character",
            "character_id": 9,
            "supporting_evidence": [_cue()],
            "validation_status": "valid",
        }
    )
    assert char_link.target_kind == ClueLinkTargetKind.CHARACTER

    with pytest.raises(ValidationError):
        ClueLinkContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "version_id": 3,
                "logical_clue_id": "clue-1",
                "target_kind": "character",
                "character_id": 9,
                "timeline_event_id": 4,  # two targets
            }
        )

    with pytest.raises(ValidationError):
        ClueLinkContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "version_id": 3,
                "logical_clue_id": "clue-1",
                "target_kind": "character",
                "character_id": 9,
                "chat_text": "the butler did it",
            }
        )

    with pytest.raises(ValidationError):
        ClueLinkContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "version_id": 3,
                "logical_clue_id": "clue-1",
                "target_kind": "character",
                "character_id": 9,
                "similarity_score": 0.99,
            }
        )


def test_relationship_observation_link_can_be_source_unavailable():
    link = ClueLinkContract.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "version_id": 3,
            "logical_clue_id": "clue-1",
            "target_kind": "relationship_observation",
            "relationship_observation_ref": "obs:1:ally",
            "validation_status": "source_unavailable",
        }
    )
    assert link.validation_status == ClueLinkValidationStatus.SOURCE_UNAVAILABLE


def test_semantic_judgment_rejects_extra_authority_fields():
    ok = ClueSemanticJudgment.model_validate(
        {
            "candidate_id": "c1",
            "classification": "cue_only",
            "cue_evidence_ids": ["ev-1"],
            "confidence": 0.9,
            "rationale": "early broken seal motif",
        }
    )
    assert ok.schema_version == "clue-semantic-judgment.v1"

    for extra in (
        {"status": "active"},
        {"lifecycle_state": "paid_off"},
        {"version_id": 1},
        {"write_db": True},
        {"domain": "history"},
    ):
        payload = {
            "candidate_id": "c1",
            "classification": "cue_only",
            "cue_evidence_ids": ["ev-1"],
            "confidence": 0.9,
            "rationale": "x",
            **extra,
        }
        with pytest.raises(ValidationError):
            ClueSemanticJudgment.model_validate(payload)


def test_override_and_human_action_contracts():
    ov = ClueOverrideContract.model_validate(
        {
            "owner_id": 1,
            "novel_id": 2,
            "logical_clue_id": "clue-1",
            "action": "confirm",
            "field_name": "disposition",
            "value": {"confirmed": True},
            "author": "owner@example.com",
            "reason": "matches chapter 1 seal",
        }
    )
    assert ov.action == ClueOverrideAction.CONFIRM

    with pytest.raises(ValidationError):
        ClueOverrideContract.model_validate(
            {
                "owner_id": 1,
                "novel_id": 2,
                "logical_clue_id": "clue-1",
                "action": "annotate",
                "field_name": "note",
                "value": {},
                "author": "owner",
                "reason": "missing note body",
            }
        )

    action = ClueHumanActionRequest.model_validate(
        {"action": "reject", "reason": "motif only"}
    )
    assert action.action == ClueOverrideAction.REJECT
    with pytest.raises(ValidationError):
        ClueHumanActionRequest.model_validate(
            {"action": "annotate", "reason": "no note"}
        )


def test_visible_envelope_is_derived_read_model():
    env = ClueVisibleEnvelope.model_validate(
        {
            "novel_id": 1,
            "version_id": 2,
            "source": "active",
            "through_chapter": 3,
            "cutoff_chapter": 3,
            "clues": [
                {
                    "logical_clue_id": "clue-1",
                    "title": "Seal",
                    "derived_state": "active",
                    "narrative_chapter_number": 1,
                    "source_start": 0,
                    "confidence": 0.7,
                    "evidence_count": 1,
                    "link_count": 0,
                }
            ],
            "counts": {"total": 1},
            "available_states": ["active"],
        }
    )
    assert env.clues[0].derived_state == ClueLifecycleState.ACTIVE
    with pytest.raises(ValidationError):
        ClueVisibleEnvelope.model_validate(
            {
                "novel_id": 1,
                "version_id": 2,
                "source": "active",
                "through_chapter": 3,
                "cutoff_chapter": 3,
                "history_mode": True,
            }
        )
