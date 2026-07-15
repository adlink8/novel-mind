"""Strict narrative-memory contract and canonicalization tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.contracts import (
    CandidateVersionSpec,
    ExactSourceLink,
    MemoryClaim,
    MemoryEdge,
    ModelLineage,
    NodeKind,
    parse_memory_node,
)


pytestmark = pytest.mark.unit


def _claim_payloads() -> tuple[dict[str, object], ...]:
    text_before = {"value_kind": "text", "value": "unknown"}
    text_after = {"value_kind": "text", "value": "north gate"}
    return (
        {
            "claim_kind": "entity_state",
            "entity_kind": "character",
            "entity_key": "character:lin",
            "dimension": "location",
            "prior": text_before,
            "current": text_after,
            "change": "change",
        },
        {
            "claim_kind": "event_fact",
            "event_kind": "discovery",
            "actor_keys": ["character:lin"],
            "object_keys": ["clue:map"],
            "chapter_start": 1,
            "chapter_end": 1,
            "outcome": text_after,
        },
        {
            "claim_kind": "relationship_delta",
            "source_entity_key": "character:lin",
            "target_entity_key": "character:mei",
            "relationship_kind": "ally",
            "prior": "unknown",
            "current": "ally",
            "change": "establish",
        },
        {
            "claim_kind": "clue_delta",
            "clue_key": "clue:map",
            "prior": "candidate",
            "current": "active",
            "change": "activate",
        },
        {
            "claim_kind": "world_state_delta",
            "subject_key": "world:capital",
            "dimension": "political_order",
            "prior": text_before,
            "current": text_after,
            "change": "change",
        },
        {
            "claim_kind": "open_loop_delta",
            "loop_key": "loop:missing-heir",
            "prior": "open",
            "current": "resolved",
            "change": "resolve",
        },
    )


def _claim(payload: dict[str, object]) -> dict[str, object]:
    return {
        "claim_key": f"claim:{payload['claim_kind']}",
        "node_key": "chapter:1",
        "payload": payload,
        "uncertainty": "certain",
        "confidence": 0.9,
        "visible_from_chapter": 1,
        "source_keys": ["source:1"],
        "non_authoritative_statement": "non-authoritative display only",
    }


@pytest.mark.parametrize("payload", _claim_payloads())
def test_six_claim_variants_round_trip_strict_json(payload: dict[str, object]) -> None:
    claim = MemoryClaim.model_validate_json(json.dumps(_claim(payload)))

    restored = MemoryClaim.model_validate_json(claim.model_dump_json())

    assert restored == claim
    assert restored.payload.claim_kind == payload["claim_kind"]
    assert restored.model_config["frozen"] is True


def test_contracts_reject_extra_coercion_unknown_enum_and_summary_only_authority() -> None:
    valid = _claim(_claim_payloads()[0])
    invalid_cases = (
        {**valid, "authoritative_summary": "invented"},
        {**valid, "confidence": "0.9"},
        {**valid, "visible_from_chapter": "1"},
        {**valid, "uncertainty": "maybe"},
        {key: value for key, value in valid.items() if key != "payload"},
        {**valid, "payload": {"claim_kind": "entity_state", "facts": {"x": 1}}},
    )

    for raw in invalid_cases:
        with pytest.raises(ValidationError):
            MemoryClaim.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    ("kind", "start", "end"),
    (
        ("chapter_state", 1, 1),
        ("story_arc", 1, 3),
        ("volume", 1, 3),
        ("global_story", 1, 9),
    ),
)
def test_closed_node_contracts_use_explicit_inclusive_ranges(
    kind: str, start: int, end: int
) -> None:
    node = parse_memory_node(
        {
            "node_kind": kind,
            "node_key": f"node:{kind}",
            "chapter_start": start,
            "chapter_end": end,
            "schema_version": "memory-node.v1",
            "display_label": "non-authoritative",
        }
    )

    assert node.node_kind == NodeKind(kind)
    assert (node.chapter_start, node.chapter_end) == (start, end)


def test_node_edge_source_and_version_contracts_fail_closed() -> None:
    with pytest.raises(ValidationError):
        parse_memory_node(
            {
                "node_kind": "chapter_state",
                "node_key": "chapter:1",
                "chapter_start": 1,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            }
        )

    with pytest.raises(ValidationError):
        MemoryEdge.model_validate(
            {
                "edge_type": "contains",
                "source_node_key": "global",
                "target_node_key": "global",
            }
        )

    with pytest.raises(ValidationError):
        ExactSourceLink.model_validate(
            {
                "source_key": "source:1",
                "claim_key": "claim:1",
                "source_kind": "hierarchy",
                "hierarchy_build_id": "build-1",
                "evidence_node_id": "leaf-1",
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": 3,
                "source_end": 3,
                "content_hash": "a" * 64,
                "source_snapshot_hash": "b" * 64,
            }
        )

    with pytest.raises(ValidationError):
        CandidateVersionSpec.model_validate(
            {
                "version_key": "memory-v1",
                "prompt_hash": "a" * 64,
                "schema_hash": "a" * 64,
                "model_lineage": {
                    "provider": "openai",
                    "model": "gpt",
                    "deployment": "prod",
                    "revision": "1",
                    "temperature": 0,
                },
                "decoding_hash": "a" * 64,
                "config_hash": "a" * 64,
                "policy_hash": "a" * 64,
            }
        )

    lineage = ModelLineage.model_validate(
        {
            "provider": "openai",
            "model": "gpt",
            "deployment": "fixed",
            "revision": "1",
        }
    )
    with pytest.raises(ValidationError):
        lineage.provider = "other"  # type: ignore[misc]
