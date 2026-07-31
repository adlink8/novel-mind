"""Unit tests for chapter package rebinding and cache identity."""

from __future__ import annotations

import json

import pytest

from app.services.narrative_memory.builder_contracts import (
    ChapterStateInputPackage,
    ChapterStateModelOutput,
    EvidenceLeafRef,
)
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    chapter_cache_identity,
    rebind_cached_chapter_state_package,
    rebind_chapter_state_package,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    ModelLineage,
    NodeKind,
)


pytestmark = pytest.mark.unit

HEX = "a" * 64


def _input() -> ChapterStateInputPackage:
    leaf = EvidenceLeafRef(
        hierarchy_build_id="build-1",
        evidence_node_id="leaf-1",
        chapter_id=10,
        chapter_number=1,
        source_start=0,
        source_end=4,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )
    return ChapterStateInputPackage(
        stage_key="chapter_state:10",
        owner_id=1,
        novel_id=2,
        version_id=3,
        chapter_id=10,
        chapter_number=1,
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


def test_rebind_chapter_state_produces_strict_candidate_package() -> None:
    package = rebind_chapter_state_package(
        input_package=_input(),
        model_output=ChapterStateModelOutput(
            node_key="ignored",
            display_label="Chapter One",
            claims=(
                {
                    # Simulate a model copying the previous chapter's key.
                    "claim_key": "chapter_state:99:claim:1",
                    "payload": {
                        "claim_kind": "entity_state",
                        "entity_kind": "character",
                        "entity_key": "character:lin",
                        "dimension": "location",
                        "prior": {"value_kind": "unknown"},
                        "current": {"value_kind": "text", "value": "gate"},
                        "change": "establish",
                    },
                    "uncertainty": "certain",
                    "confidence": 0.91,
                    "visible_from_chapter": 1,
                },
            ),
            source_bindings=(
                {
                    "claim_key": "chapter_state:99:claim:1",
                    "evidence_node_id": "leaf-1",
                    "source_key": "src:1",
                },
            ),
        ),
    )
    assert len(package.nodes) == 1
    assert package.nodes[0].node_kind == NodeKind.CHAPTER_STATE
    assert package.nodes[0].chapter_start == 1
    assert len(package.claims) == 1
    assert package.claims[0].claim_key == "chapter_state:1:claim:1"
    assert package.source_links[0].source_key == "chapter_state:1:claim:1:src:1"
    assert package.source_links[0].evidence_node_id == "leaf-1"
    assert package.source_links[0].hierarchy_build_id == "build-1"


def test_rebind_rejects_unknown_evidence_leaf() -> None:
    with pytest.raises(PackageBuildError, match="unknown evidence leaf"):
        rebind_chapter_state_package(
            input_package=_input(),
            model_output={
                "node_key": "x",
                "claims": [
                    {
                        "claim_key": "c1",
                        "payload": {
                            "claim_kind": "entity_state",
                            "entity_kind": "character",
                            "entity_key": "character:lin",
                            "dimension": "location",
                            "prior": {"value_kind": "unknown"},
                            "current": {"value_kind": "text", "value": "gate"},
                            "change": "establish",
                        },
                        "uncertainty": "certain",
                        "confidence": 0.9,
                        "visible_from_chapter": 1,
                    }
                ],
                "source_bindings": [
                    {
                        "claim_key": "c1",
                        "evidence_node_id": "missing-leaf",
                    }
                ],
            },
        )


def test_rebind_cached_package_discards_stale_model_keys() -> None:
    package = rebind_chapter_state_package(
        input_package=_input(),
        model_output={
            "node_key": "chapter_state:1",
            "claims": [
                {
                    "claim_key": "chapter_state:99:claim:1",
                    "payload": {
                        "claim_kind": "entity_state",
                        "entity_kind": "character",
                        "entity_key": "character:lin",
                        "dimension": "location",
                        "prior": {"value_kind": "unknown"},
                        "current": {"value_kind": "text", "value": "gate"},
                        "change": "establish",
                    },
                }
            ],
            "source_bindings": [
                {"evidence_node_id": "leaf-1", "source_key": "old-source"}
            ],
        },
    )
    cached_data = package.model_dump(mode="json")
    cached_data["claims"][0]["claim_key"] = "chapter_state:99:claim:1"
    cached_data["claims"][0]["source_keys"] = ["old-source"]
    cached_data["source_links"][0]["claim_key"] = "chapter_state:99:claim:1"
    cached_data["source_links"][0]["source_key"] = "old-source"
    cached = CandidatePackage.model_validate_json(json.dumps(cached_data))

    rebound = rebind_cached_chapter_state_package(
        input_package=_input(), cached_package=cached
    )

    assert rebound.claims[0].claim_key == "chapter_state:1:claim:1"
    assert rebound.source_links[0].source_key == "chapter_state:1:claim:1:src:1"


def test_chapter_cache_identity_changes_with_policy_hash() -> None:
    base = _input()
    cs1, key1 = chapter_cache_identity(base)
    other = ChapterStateInputPackage.model_validate(
        {**base.model_dump(mode="json"), "policy_hash": "b" * 64}
    )
    cs2, key2 = chapter_cache_identity(other)
    assert cs1 != cs2
    assert key1 != key2
