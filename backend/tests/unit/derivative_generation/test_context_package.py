"""Unit tests for the derivative context package compiler (Phase 37-01).

Covers the deterministic, DB-free contract layer of
``services/derivative_generation/context_package.py``: byte-replayable hashes,
canonical assembly with fixed structure, the closed intent vocabulary, honest
dimension status, the budget gate and the fail-closed cutoff checks
(REQ-CRE-05 / REQ-FORK-03 / D-37-01).
"""

from __future__ import annotations

import pytest

from app.services.derivative_generation.context_package import (
    CONTEXT_PACKAGE_SCHEMA_VERSION,
    ContextBudgetPolicy,
    ContextPackageError,
    ContextPackageIntent,
    DimensionStatus,
    assemble_package_payload,
    assert_cutoff_within_fork,
    budget_verdict,
    canonical_json_bytes,
    compute_dimension_status,
    dimension_view,
    estimate_input_characters,
    estimate_input_tokens,
    package_hash,
    validate_lineage,
    verify_package_hash,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64


def _lineage(**overrides):
    data = {
        "source_version_key": "original:abc",
        "source_snapshot_hash": HEX64,
        "through_chapter": 2,
        "full_book_authorized": False,
        "cutoff_snapshot_hash": HEX64,
        "scope_hash": HEX64,
        "manifest_hash": HEX64,
    }
    data.update(overrides)
    return data


def _dimensions(**overrides):
    data = {
        "world_state": dimension_view(
            status=DimensionStatus.AVAILABLE, items=[{"entity_key": "hero"}]
        ),
        "timeline": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "unresolved_clues": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "world_rules": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "evidence": dimension_view(
            status=DimensionStatus.AVAILABLE, items=[{"leaf_key": "chapter:1"}]
        ),
        "user_intent": {
            "status": "available",
            "kind": "continuation",
            "hash": "b" * 64,
        },
    }
    data.update(overrides)
    return data


def _payload(**kwargs):
    return assemble_package_payload(
        owner_id=kwargs.get("owner_id", 1),
        novel_id=kwargs.get("novel_id", 1),
        fork_id=kwargs.get("fork_id", 7),
        fork_key=kwargs.get("fork_key", "ff-test"),
        intent=kwargs.get("intent", "continuation"),
        lineage=kwargs.get("lineage", _lineage()),
        dimensions=kwargs.get("dimensions", _dimensions()),
        budget_estimate=kwargs.get("budget_estimate", {"blocked": False}),
    )


# ---------------------------------------------------------------------------
# Canonical serialization / hashing (T-37-01-02)
# ---------------------------------------------------------------------------


def test_canonical_json_bytes_is_byte_replayable():
    a = canonical_json_bytes({"b": 2, "a": [1, {"x": "你"}]})
    b = canonical_json_bytes({"a": [1, {"x": "你"}], "b": 2})
    assert a == b


def test_package_hash_is_deterministic():
    payload = _payload()
    assert package_hash(payload) == package_hash(payload)
    assert len(package_hash(payload)) == 64


def test_package_hash_is_sensitive_to_content():
    a = package_hash(_payload())
    b = package_hash(_payload(intent="rewrite"))
    c = package_hash(
        _payload(through_chapter=3) if False else _payload(intent="continuation")
    )
    assert a != b
    assert a == c


def test_package_hash_excludes_the_hash_field_itself():
    payload = _payload()
    with_hash = dict(payload)
    with_hash["package_hash"] = "0" * 64
    assert package_hash(with_hash) == package_hash(payload)


def test_verify_package_hash_fails_closed_on_mismatch():
    payload = _payload()
    good = package_hash(payload)
    verify_package_hash(payload, good)  # no raise
    with pytest.raises(ContextPackageError) as exc:
        verify_package_hash(payload, "1" * 64)
    assert exc.value.code == "package_hash_mismatch"


# ---------------------------------------------------------------------------
# Canonical assembly (D-37-01)
# ---------------------------------------------------------------------------


def test_assemble_package_payload_has_complete_deterministic_structure():
    payload = _payload()
    assert payload["schema_version"] == CONTEXT_PACKAGE_SCHEMA_VERSION
    assert payload["owner_id"] == 1
    assert payload["novel_id"] == 1
    assert payload["fork_id"] == 7
    assert payload["fork_key"] == "ff-test"
    assert payload["space"] == "fanfiction_canon"
    assert payload["intent"] == "continuation"
    assert set(payload["version"]) == {
        "source_version_key",
        "source_snapshot_hash",
        "through_chapter",
        "full_book_authorized",
        "cutoff_snapshot_hash",
        "scope_hash",
        "manifest_hash",
    }
    assert set(payload["dimensions"]) == {
        "world_state",
        "timeline",
        "unresolved_clues",
        "world_rules",
        "evidence",
        "user_intent",
    }
    # Re-assembly with the same inputs is byte-identical.
    assert _payload() == payload


def test_assemble_rejects_unknown_intent():
    with pytest.raises(ContextPackageError) as exc:
        _payload(intent="hallucinate")
    assert exc.value.code == "invalid_intent"


def test_assemble_rejects_missing_dimension():
    dims = _dimensions()
    del dims["world_rules"]
    with pytest.raises(ContextPackageError) as exc:
        _payload(dimensions=dims)
    assert exc.value.code == "incomplete_dimensions"


def test_assemble_rejects_incomplete_lineage():
    lineage = _lineage()
    del lineage["scope_hash"]
    with pytest.raises(ContextPackageError) as exc:
        _payload(lineage=lineage)
    assert exc.value.code == "incomplete_lineage"


def test_assemble_rejects_non_positive_scope_ids():
    with pytest.raises(ContextPackageError) as exc:
        _payload(owner_id=0)
    assert exc.value.code == "invalid_scope"


def test_assemble_rejects_non_positive_cutoff():
    with pytest.raises(ContextPackageError) as exc:
        _payload(lineage=_lineage(through_chapter=0))
    assert exc.value.code == "invalid_cutoff"


# ---------------------------------------------------------------------------
# Cutoff gate (D-37-01, T-37-01-01)
# ---------------------------------------------------------------------------


def test_cutoff_within_fork_passes_and_future_cutoff_fails_closed():
    assert_cutoff_within_fork(2, 2)  # equal is allowed (same scope)
    assert_cutoff_within_fork(1, 2)  # shrink is allowed
    with pytest.raises(ContextPackageError) as exc:
        assert_cutoff_within_fork(3, 2)
    assert exc.value.code == "cutoff_exceeds_scope"


# ---------------------------------------------------------------------------
# Dimension honesty (REQ-CRE-01 pitfall #4)
# ---------------------------------------------------------------------------


def test_empty_dimension_is_unavailable_not_fake_success():
    assert compute_dimension_status([]) is DimensionStatus.UNAVAILABLE
    assert compute_dimension_status(None) is DimensionStatus.UNAVAILABLE
    assert compute_dimension_status([1]) is DimensionStatus.AVAILABLE


def test_dimension_view_shape():
    view = dimension_view(
        status=DimensionStatus.BLOCKED,
        items=[],
        block_reason="beyond_cutoff",
        trace={"status": "blocked"},
    )
    assert view == {
        "status": "blocked",
        "block_reason": "beyond_cutoff",
        "trace": {"status": "blocked"},
        "items": [],
    }


def test_missing_dimensions_are_reported_not_fabricated():
    """A package with absent world/timeline/clue rows still seals honestly."""
    payload = _payload()
    dims = payload["dimensions"]
    assert dims["world_state"]["status"] == "available"
    assert dims["timeline"]["status"] == "unavailable"
    assert dims["unresolved_clues"]["status"] == "unavailable"


def test_user_intent_is_sealed_with_hash():
    payload = _payload(
        intent="rewrite",
        dimensions=_dimensions(
            user_intent={
                "status": "available",
                "kind": "rewrite",
                "hash": "c" * 64,
            }
        ),
    )
    intent = payload["dimensions"]["user_intent"]
    assert intent["status"] == "available"
    assert intent["kind"] == "rewrite"
    assert len(intent["hash"]) == 64


# ---------------------------------------------------------------------------
# Budget gate (blocked before any provider call)
# ---------------------------------------------------------------------------


def test_budget_verdict_allows_within_policy():
    verdict = budget_verdict(_payload(), ContextBudgetPolicy(max_input_tokens=10_000))
    assert verdict["blocked"] is False
    assert verdict["block_reason"] is None
    assert verdict["estimated_input_tokens"] >= 1


def test_budget_verdict_blocks_overrun():
    huge = _payload(
        dimensions=_dimensions(
            world_state=dimension_view(
                status=DimensionStatus.AVAILABLE,
                items=[{"canonical_payload": {"blob": "x" * 50_000}}],
            )
        )
    )
    verdict = budget_verdict(huge, ContextBudgetPolicy(max_input_tokens=100))
    assert verdict["blocked"] is True
    assert verdict["block_reason"] == "budget_exhausted"


def test_estimate_input_tokens_rounds_up():
    assert estimate_input_tokens({"a": "x"}, chars_per_token=4) >= 1
    # ~8 characters at 4 chars/token -> at least 2 tokens.
    assert estimate_input_tokens({"text": "abcdefgh"}, chars_per_token=4) >= 2


def test_estimate_input_characters_tracks_payload_size():
    small = _payload()
    large = _payload(
        dimensions=_dimensions(
            world_state=dimension_view(
                status=DimensionStatus.AVAILABLE,
                items=[{"entity_key": "x" * 200}],
            )
        )
    )
    assert estimate_input_characters(large) > estimate_input_characters(small)


# ---------------------------------------------------------------------------
# Lineage validation
# ---------------------------------------------------------------------------


def test_validate_lineage_returns_same_dict():
    lineage = _lineage()
    assert validate_lineage(lineage) is lineage
    with pytest.raises(ContextPackageError):
        validate_lineage({"source_version_key": "x"})


def test_intent_enum_closed_vocabulary():
    assert ContextPackageIntent.CONTINUATION.value == "continuation"
    assert ContextPackageIntent.REWRITE.value == "rewrite"
    with pytest.raises(ValueError):
        ContextPackageIntent("autofork")
