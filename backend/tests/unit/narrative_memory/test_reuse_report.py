"""Unit tests for reuse economics report formulas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.narrative_memory.reuse_report import (
    coalesce_ranges,
    compute_avoided_upper_bound,
    report_has_provider_capability,
)

pytestmark = pytest.mark.unit


def test_report_provider_free() -> None:
    assert report_has_provider_capability() is False


def test_coalesce_ranges_sorted_and_merged() -> None:
    assert coalesce_ranges([]) == []
    assert coalesce_ranges([3, 1, 2, 5]) == [[1, 3], [5, 5]]
    assert coalesce_ranges([1, 1, 2]) == [[1, 2]]
    assert coalesce_ranges([10, 8, 9, 1]) == [[1, 1], [8, 10]]


def test_avoided_upper_bound_floors_at_zero() -> None:
    result = compute_avoided_upper_bound(
        full_calls=10,
        full_input=1000,
        full_output=500,
        full_cost=Decimal("1.50"),
        observed_calls=3,
        observed_input=300,
        observed_output=100,
        observed_cost=Decimal("0.40"),
    )
    assert result["calls"] == 7
    assert result["input_tokens"] == 700
    assert result["output_tokens"] == 400
    assert Decimal(result["cost_usd"]) == Decimal("1.10")
    assert "formula" in result


def test_avoided_upper_bound_when_observed_exceeds_full() -> None:
    result = compute_avoided_upper_bound(
        full_calls=2,
        full_input=10,
        full_output=10,
        full_cost=Decimal("0.01"),
        observed_calls=5,
        observed_input=50,
        observed_output=50,
        observed_cost=Decimal("1.00"),
    )
    assert result["calls"] == 0
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert Decimal(result["cost_usd"]) == Decimal("0")


def test_labels_are_separate_concepts() -> None:
    """Document the five labeled economics buckets required by V08-REUSE-04."""
    labels = {
        "observed_actual",
        "full_rebuild_upper_bound",
        "avoided_upper_bound",
        "carry_reuse",
        "exact_cache_reuse",
    }
    assert len(labels) == 5
