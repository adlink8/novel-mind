"""Unit tests for builder contracts, cache identity, and forbidden keys."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.builder_contracts import (
    BudgetPolicy,
    ChapterStateInputPackage,
    ChapterStateModelOutput,
    EvidenceLeafRef,
    ModelDeploymentSnapshot,
    OptionalSourceSignal,
    RunPolicy,
    SourceStatus,
    StageKind,
    assert_no_forbidden_keys,
    exact_cache_key,
    package_checksum,
)
from app.services.narrative_memory.contracts import ModelLineage


pytestmark = pytest.mark.unit

HEX = "a" * 64


def _lineage() -> ModelLineage:
    return ModelLineage(
        provider="test", model="m", deployment="d", revision="1"
    )


def _leaf() -> EvidenceLeafRef:
    return EvidenceLeafRef(
        hierarchy_build_id="build-1",
        evidence_node_id="leaf-1",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=3,
        content_hash=HEX,
        source_snapshot_hash=HEX,
    )


def _chapter_package(**overrides):
    base = {
        "stage_key": "chapter_state:1",
        "owner_id": 1,
        "novel_id": 1,
        "version_id": 1,
        "chapter_id": 1,
        "chapter_number": 1,
        "hierarchy_build_id": "build-1",
        "source_snapshot_hash": HEX,
        "hierarchy_checksum": HEX,
        "eligibility_report_checksum": HEX,
        "evidence_leaves": [_leaf().model_dump(mode="json")],
        "optional_signals": [],
        "prompt_hash": HEX,
        "schema_hash": HEX,
        "model_lineage": _lineage().model_dump(mode="json"),
        "decoding_hash": HEX,
        "config_hash": HEX,
        "policy_hash": HEX,
    }
    base.update(overrides)
    return ChapterStateInputPackage.model_validate(base)


def test_chapter_package_round_trip_and_rejects_forbidden_fields() -> None:
    package = _chapter_package()
    restored = ChapterStateInputPackage.model_validate_json(package.model_dump_json())
    assert restored == package

    with pytest.raises(ValidationError):
        _chapter_package(reader_chat={"x": 1})
    with pytest.raises(ValidationError):
        _chapter_package(conversation_id=1)
    with pytest.raises(ValidationError):
        _chapter_package(summary_text="nope")


def test_exact_cache_key_sensitive_to_lineage_and_optional_sources() -> None:
    package = _chapter_package()
    base = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=package_checksum(package),
        prompt_hash=package.prompt_hash,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage={},
    )
    changed_prompt = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=package_checksum(package),
        prompt_hash="b" * 64,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage={},
    )
    changed_optional = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=package_checksum(package),
        prompt_hash=package.prompt_hash,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage={"timeline": {"status": "non_empty"}},
    )
    assert base != changed_prompt
    assert base != changed_optional
    assert base.startswith("nmb:")


def test_cache_key_insertion_order_stable() -> None:
    package = _chapter_package(
        optional_signals=[
            OptionalSourceSignal(
                source_kind="timeline",
                status=SourceStatus.HEALTHY_EMPTY,
            ).model_dump(mode="json"),
            OptionalSourceSignal(
                source_kind="clue",
                status=SourceStatus.UNAVAILABLE,
                reason_code="missing",
            ).model_dump(mode="json"),
        ]
    )
    a = package_checksum(package)
    # rebuild with reversed signals order in lineage dict keys
    key1 = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=a,
        prompt_hash=package.prompt_hash,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage={"clue": {"status": "x"}, "timeline": {"status": "y"}},
    )
    key2 = exact_cache_key(
        stage_key=package.stage_key,
        source_snapshot_hash=package.source_snapshot_hash,
        hierarchy_checksum=package.hierarchy_checksum,
        package_checksum_value=a,
        prompt_hash=package.prompt_hash,
        schema_hash=package.schema_hash,
        model_lineage=package.model_lineage,
        decoding_hash=package.decoding_hash,
        config_hash=package.config_hash,
        policy_hash=package.policy_hash,
        optional_source_lineage={"timeline": {"status": "y"}, "clue": {"status": "x"}},
    )
    assert key1 == key2


def test_unknown_price_snapshot_and_run_policy() -> None:
    dep = ModelDeploymentSnapshot(
        provider="p",
        model="m",
        deployment="d",
        revision="1",
        supports_structured_output=True,
        input_price_per_million=None,
        output_price_per_million=None,
    )
    assert dep.prices() == (None, None)
    policy = RunPolicy(
        policy_version="v1",
        stage_order=(StageKind.CHAPTER_STATE,),
        budget=BudgetPolicy(
            max_calls=1,
            max_input_tokens=10,
            max_output_tokens=10,
            max_cost_usd="1.0",
        ),
        prompt_hash=HEX,
        schema_hash=HEX,
        model_lineage=_lineage(),
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
    )
    assert policy.max_schema_repairs == 1


def test_model_output_rejects_chat_keys() -> None:
    with pytest.raises(ValidationError):
        ChapterStateModelOutput.model_validate(
            {
                "node_key": "chapter_state:1",
                "claims": [{"claim_kind": "event_fact"}],
                "source_bindings": [{}],
                "citations": [],
            }
        )


def test_assert_no_forbidden_keys_nested() -> None:
    with pytest.raises(ValueError, match="forbidden key"):
        assert_no_forbidden_keys({"ok": 1, "nested": {"similarity_score": 0.1}})
