"""Controlled dual-model timeline qualification; blocked is never success."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_timeline_qualification import run_live_qualification

pytestmark = pytest.mark.live

CORPUS = Path(__file__).resolve().parents[2] / "evals" / "timeline_fiction.v1.json"


def _result(tier: str, **overrides):
    result = {
        "status": "completed",
        "tier": tier,
        "schema_valid": True,
        "evidence_valid": True,
        "spoiler_leaks": 0,
        "budget_status": "completed",
        "tokens": 100,
        "cost_usd": 0.001,
        "latency_ms": 10.0,
        "provider": "controlled",
        "model": f"{tier}-fixture",
        "revision": "v1",
    }
    result.update(overrides)
    return result


def test_controlled_balanced_and_quality_models_qualify_together():
    report = run_live_qualification(
        CORPUS,
        chapter_runner=lambda: _result("balanced"),
        reconcile_runner=lambda: _result("quality"),
    )
    assert report["status"] == "qualified"
    assert report["quality_comparable"] is True
    assert report["metrics"]["calls"] == 2
    assert [item["tier"] for item in report["deployments"]] == ["balanced", "quality"]


@pytest.mark.parametrize(
    "bad_result",
    [
        {"status": "outage"},
        {"schema_valid": False},
        {"evidence_valid": False},
        {"budget_status": "paused_budget"},
        {"spoiler_leaks": 1},
    ],
)
def test_live_failure_modes_are_non_comparable(bad_result):
    report = run_live_qualification(
        CORPUS,
        chapter_runner=lambda: _result("balanced", **bad_result),
        reconcile_runner=lambda: _result("quality"),
    )
    assert report["status"] in {"blocked_dependency", "failed_policy"}
    assert report["status"] != "qualified"
    assert report["quality_comparable"] is False
    assert report["metrics"] is None


def test_missing_live_dependency_is_blocked_not_a_zero_score_pass():
    report = run_live_qualification(CORPUS)
    assert report["status"] == "blocked_dependency"
    assert report["quality_comparable"] is False
    assert report["metrics"] is None
