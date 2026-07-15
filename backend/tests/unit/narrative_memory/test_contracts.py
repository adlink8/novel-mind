"""Strict narrative-memory contract and canonicalization tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ExactSourceLink,
    MemoryClaim,
    MemoryEdge,
    ModelLineage,
    NodeKind,
    canonical_json,
    claim_checksum,
    edge_checksum,
    model_lineage_checksum,
    node_checksum,
    parse_memory_node,
    source_link_checksum,
    version_spec_checksum,
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


def _package_dict() -> dict[str, object]:
    claim = _claim(_claim_payloads()[0])
    claim["node_key"] = "chapter:1"
    return {
        "nodes": [
            {
                "node_kind": "global_story",
                "node_key": "global",
                "chapter_start": 1,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            },
            {
                "node_kind": "story_arc",
                "node_key": "arc:1",
                "chapter_start": 1,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            },
            {
                "node_kind": "chapter_state",
                "node_key": "chapter:1",
                "chapter_start": 1,
                "chapter_end": 1,
                "schema_version": "memory-node.v1",
            },
        ],
        "claims": [claim],
        "edges": [
            {
                "edge_type": "contains",
                "source_node_key": "global",
                "target_node_key": "arc:1",
            },
            {
                "edge_type": "contains",
                "source_node_key": "arc:1",
                "target_node_key": "chapter:1",
            },
        ],
        "source_links": [
            {
                "source_key": "source:1",
                "claim_key": "claim:entity_state",
                "source_kind": "hierarchy",
                "hierarchy_build_id": "build-1",
                "evidence_node_id": "leaf-1",
                "chapter_id": 10,
                "chapter_number": 1,
                "source_start": 0,
                "source_end": 3,
                "content_hash": "a" * 64,
                "source_snapshot_hash": "b" * 64,
            }
        ],
    }


def test_candidate_package_requires_local_node_claim_and_source_keys() -> None:
    package = CandidatePackage.model_validate_json(json.dumps(_package_dict()))
    assert package.claims[0].source_keys == ("source:1",)

    invalid_packages: list[dict[str, object]] = []
    for mutation in ("foreign_node", "foreign_claim", "foreign_source"):
        raw = json.loads(json.dumps(_package_dict()))
        if mutation == "foreign_node":
            raw["claims"][0]["node_key"] = "chapter:outside"
        elif mutation == "foreign_claim":
            raw["source_links"][0]["claim_key"] = "claim:outside"
        else:
            raw["claims"][0]["source_keys"] = ["source:outside"]
        invalid_packages.append(raw)

    for raw in invalid_packages:
        with pytest.raises(ValidationError):
            CandidatePackage.model_validate_json(json.dumps(raw))


def test_package_rejects_visibility_or_evidence_outside_node_range() -> None:
    visibility = json.loads(json.dumps(_package_dict()))
    visibility["claims"][0]["visible_from_chapter"] = 2
    with pytest.raises(ValidationError):
        CandidatePackage.model_validate_json(json.dumps(visibility))

    evidence = json.loads(json.dumps(_package_dict()))
    evidence["source_links"][0]["chapter_number"] = 2
    with pytest.raises(ValidationError):
        CandidatePackage.model_validate_json(json.dumps(evidence))


def test_canonical_hashes_are_order_independent_and_authority_sensitive() -> None:
    package = CandidatePackage.model_validate_json(json.dumps(_package_dict()))
    node = package.nodes[2]
    claim = package.claims[0]
    edge = package.edges[0]
    link = package.source_links[0]
    lineage = ModelLineage(
        provider="openai", model="gpt", deployment="fixed", revision="1"
    )
    spec = CandidateVersionSpec(
        version_key="memory-v1",
        prompt_hash="a" * 64,
        schema_hash="b" * 64,
        model_lineage=lineage,
        decoding_hash="c" * 64,
        config_hash="d" * 64,
        policy_hash="e" * 64,
    )

    reordered = MemoryClaim.model_validate_json(
        json.dumps(dict(reversed(list(_claim(_claim_payloads()[0]).items()))))
    )
    assert canonical_json(reordered) == canonical_json(claim)
    assert claim_checksum(reordered) == claim_checksum(claim)

    assert len(
        {
            node_checksum(node),
            claim_checksum(claim),
            edge_checksum(edge),
            source_link_checksum(link),
            model_lineage_checksum(lineage),
            version_spec_checksum(spec),
        }
    ) == 6

    changed_claim = claim.model_copy(update={"visible_from_chapter": 2})
    changed_lineage = lineage.model_copy(update={"revision": "2"})
    assert claim_checksum(changed_claim) != claim_checksum(claim)
    assert model_lineage_checksum(changed_lineage) != model_lineage_checksum(lineage)

    with pytest.raises(TypeError):
        canonical_json({"arbitrary": "unvalidated"})  # type: ignore[arg-type]


def test_every_frozen_version_lineage_field_changes_server_checksum() -> None:
    lineage = ModelLineage(
        provider="openai", model="gpt", deployment="fixed", revision="1"
    )
    spec = CandidateVersionSpec(
        version_key="memory-v1",
        prompt_hash="a" * 64,
        schema_hash="b" * 64,
        model_lineage=lineage,
        decoding_hash="c" * 64,
        config_hash="d" * 64,
        policy_hash="e" * 64,
    )
    baseline = version_spec_checksum(spec)
    changes = (
        {"version_key": "memory-v2"},
        {"parent_version_id": 1},
        {"prompt_hash": "f" * 64},
        {"schema_hash": "f" * 64},
        {"model_lineage": lineage.model_copy(update={"revision": "2"})},
        {"decoding_hash": "f" * 64},
        {"config_hash": "f" * 64},
        {"policy_hash": "f" * 64},
    )

    assert all(
        version_spec_checksum(spec.model_copy(update=change)) != baseline
        for change in changes
    )
