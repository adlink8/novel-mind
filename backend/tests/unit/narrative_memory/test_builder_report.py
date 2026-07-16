"""Unit tests for deterministic build report arithmetic."""

from __future__ import annotations

from app.services.narrative_memory.builder_report import report_checksum


pytestmark = __import__("pytest").mark.unit


def test_report_checksum_stable_and_sensitive() -> None:
    body = {
        "outcome": "completed_candidate",
        "call_totals": {"transport_calls": 2, "cache_hits": 1, "cost_usd": "0.01"},
        "stage_counts": {"completed": 3},
    }
    a = report_checksum(body)
    b = report_checksum(dict(body))
    assert a == b
    assert a != report_checksum({**body, "outcome": "partial"})
