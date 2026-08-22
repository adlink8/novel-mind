"""Unit tests for the narrative-memory CLI model-output normalization.

Gemini occasionally emits values outside the closed chapter-state contract
enums (entity_kind="person", dimension="appearance", value_kind="string",
change="shift"). ``_normalize_model_output`` must clamp them back into the
closed sets so strict ``MemoryClaim`` validation in ``rebind_chapter_state_package``
succeeds instead of failing as schema/business-invalid after repairs.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.services.narrative_memory.builder_contracts import (
    ChapterStateInputPackage,
    EvidenceLeafRef,
)
from app.services.narrative_memory.builder_packages import rebind_chapter_state_package
from app.services.narrative_memory.contracts import (
    EntityKind,
    EntityStateDimension,
    EventKind,
    ModelLineage,
    StateChange,
)


pytestmark = pytest.mark.unit

HEX = "a" * 64

_CLI_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "run_narrative_memory_build.py"
)


@pytest.fixture(scope="module")
def cli_module():
    spec = importlib.util.spec_from_file_location(
        "run_narrative_memory_build_cli", _CLI_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _raw_chapter_output() -> dict:
    """Raw model output with out-of-contract enums (the N1 reproduction)."""
    return {
        "display_label": "第三章",
        "claims": [
            {
                "claim_key": "chapter_state:3:claim:1",
                "payload": {
                    "claim_kind": "entity_state",
                    "entity_kind": "person",
                    "entity_key": "character:someone",
                    "dimension": "appearance",
                    "prior": {"value_kind": "unknown"},
                    "current": {"value_kind": "string", "value": "蓝眼睛"},
                    "change": "shift",
                },
                "uncertainty": "likely",
                "confidence": 0.8,
                "visible_from_chapter": 3,
            },
            {
                "claim_key": "chapter_state:3:claim:2",
                "payload": {
                    "claim_kind": "event_fact",
                    "event_kind": "happened",
                    "actor_keys": ["character:someone"],
                    "outcome": {"value_kind": "string", "value": "事件"},
                },
                "uncertainty": "likely",
                "confidence": 0.8,
                "visible_from_chapter": 3,
            },
        ],
        "source_bindings": [],
    }


def _input_package() -> ChapterStateInputPackage:
    leaf = EvidenceLeafRef(
        hierarchy_build_id="build-1",
        evidence_node_id="leaf-1",
        chapter_id=3,
        chapter_number=3,
        source_start=0,
        source_end=4,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )
    return ChapterStateInputPackage(
        stage_key="chapter_state:3",
        owner_id=1,
        novel_id=2,
        version_id=3,
        chapter_id=3,
        chapter_number=3,
        hierarchy_build_id="build-1",
        source_snapshot_hash=HEX,
        hierarchy_checksum=HEX,
        eligibility_report_checksum=HEX,
        evidence_leaves=(leaf,),
        prompt_hash=HEX,
        schema_hash=HEX,
        model_lineage=ModelLineage(
            provider="p", model="m", deployment="d", revision="1"
        ),
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
    )


def test_normalize_clamps_out_of_contract_enums(cli_module) -> None:
    payload = {
        "chapter_number": 3,
        "evidence_leaves": [{"evidence_node_id": "leaf-1"}],
    }
    normalized = cli_module._normalize_model_output(
        _raw_chapter_output(), payload=payload, stage_key="chapter_state:3"
    )
    claims = normalized["claims"]
    assert len(claims) == 2

    entity_payload = claims[0]["payload"]
    entity_kinds = {member.value for member in EntityKind}
    dimensions = {member.value for member in EntityStateDimension}
    changes = {member.value for member in StateChange}
    assert entity_payload["entity_kind"] in entity_kinds
    assert entity_payload["entity_kind"] == "character"
    assert entity_payload["dimension"] in dimensions
    assert entity_payload["dimension"] == "condition"
    assert entity_payload["change"] in changes
    assert entity_payload["change"] == "establish"
    assert entity_payload["current"]["value_kind"] == "text"
    assert entity_payload["current"]["value"] == "蓝眼睛"

    event_payload = claims[1]["payload"]
    event_kinds = {member.value for member in EventKind}
    assert event_payload["event_kind"] in event_kinds
    assert event_payload["event_kind"] == "action"
    assert event_payload["outcome"]["value_kind"] == "text"


def test_normalize_keeps_valid_enums_untouched(cli_module) -> None:
    payload = {
        "chapter_number": 5,
        "evidence_leaves": [{"evidence_node_id": "leaf-1"}],
    }
    raw = {
        "display_label": "第五章",
        "claims": [
            {
                "claim_key": "chapter_state:5:claim:1",
                "payload": {
                    "claim_kind": "entity_state",
                    "entity_kind": "faction",
                    "entity_key": "faction:guild",
                    "dimension": "goal",
                    "prior": {"value_kind": "text", "value": "旧目标"},
                    "current": {"value_kind": "text", "value": "新目标"},
                    "change": "change",
                },
                "uncertainty": "certain",
                "confidence": 0.9,
                "visible_from_chapter": 5,
            }
        ],
        "source_bindings": [],
    }
    normalized = cli_module._normalize_model_output(
        raw, payload=payload, stage_key="chapter_state:5"
    )
    entity_payload = normalized["claims"][0]["payload"]
    assert entity_payload["entity_kind"] == "faction"
    assert entity_payload["dimension"] == "goal"
    assert entity_payload["change"] == "change"
    assert entity_payload["prior"]["value_kind"] == "text"
    assert entity_payload["current"]["value_kind"] == "text"


def test_clamped_claims_rebind_into_strict_package(cli_module) -> None:
    """End-to-end: clamped normalization passes strict MemoryClaim validation."""
    payload = {
        "chapter_number": 3,
        "evidence_leaves": [{"evidence_node_id": "leaf-1"}],
    }
    normalized = cli_module._normalize_model_output(
        _raw_chapter_output(), payload=payload, stage_key="chapter_state:3"
    )
    package = rebind_chapter_state_package(
        input_package=_input_package(),
        model_output=normalized,
    )
    assert len(package.claims) == 2
    assert package.claims[0].payload.claim_kind == "entity_state"
    assert package.claims[0].payload.entity_kind == EntityKind.CHARACTER
    assert package.claims[0].payload.dimension == EntityStateDimension.CONDITION
    assert package.claims[0].payload.change == StateChange.ESTABLISH
