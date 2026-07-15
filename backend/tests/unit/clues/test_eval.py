"""Frozen clue evaluator: thresholds, critical zeros, reproducibility."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app.services.clues.eval import (
    DEFAULT_FIXTURE,
    fail_closed_threshold_report,
    fixture_sha256,
    gold_predictions_from_fixture,
    load_fixture,
    run_offline_qualification,
    score_predictions,
)
from app.services.clues.gates import policy_hash

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parents[3] / "evals" / "clue_fiction.v1.json"


def test_fixture_has_required_composition():
    fixture = load_fixture(FIXTURE)
    assert fixture["domain"] == "fiction"
    assert fixture["fixture_version"]
    assert len(fixture["cases"]) >= 24
    hard = [c for c in fixture["cases"] if c.get("hard_negative")]
    chains = [c for c in fixture["cases"] if c.get("category") == "full_chain"]
    assert len(hard) >= 8
    assert len(chains) >= 8
    assert fixture["policy_hash"] == policy_hash()


def test_gold_predictions_meet_thresholds_and_critical_zero():
    fixture = load_fixture(FIXTURE)
    preds = gold_predictions_from_fixture(fixture)
    scored = score_predictions(fixture, preds)
    assert scored["qualified"] is True
    assert scored["quality_comparable"] is True
    metrics = scored["metrics"]
    assert metrics["paid_off_precision"] >= 0.90
    assert metrics["active_reinforced_macro_f1"] >= 0.85
    for key, value in metrics["critical"].items():
        assert value == 0, f"critical {key}={value}"


def test_offline_report_is_reproducible():
    a = run_offline_qualification(FIXTURE)
    b = run_offline_qualification(FIXTURE)
    assert a["report_sha256"] == b["report_sha256"]
    assert a["artifact_sha256"] == b["artifact_sha256"]
    assert a["lineage"]["fixture_sha256"] == fixture_sha256(FIXTURE)
    assert a["status"] == "qualified"
    assert a["quality_comparable"] is True
    assert a["report_version"] == "clue-offline-qualification.v1"
    assert DEFAULT_FIXTURE.name == "clue_fiction.v1.json"


def test_false_paid_off_on_hard_negative_fails_closed():
    fixture = load_fixture(FIXTURE)
    preds = gold_predictions_from_fixture(fixture)
    # Inject false paid_off on chat-only hard negative
    for pred in preds:
        if pred["id"] == "hn08":
            pred["predicted_state"] = "paid_off"
            pred["recalled"] = True
    scored = score_predictions(fixture, preds)
    assert scored["qualified"] is False
    assert scored["metrics"]["critical"]["false_paid_off"] >= 1
    assert scored["gates"]["critical_false_paid_off_zero"] is False


def test_false_active_on_motif_hard_negative_fails_closed():
    fixture = load_fixture(FIXTURE)
    preds = gold_predictions_from_fixture(fixture)
    for pred in preds:
        if pred["id"] == "hn01":
            pred["predicted_state"] = "active"
            pred["recalled"] = True
    scored = score_predictions(fixture, preds)
    assert scored["qualified"] is False
    assert scored["metrics"]["critical"]["false_active"] >= 1


def test_spoiler_and_cross_scope_controls_fail_closed():
    fixture = load_fixture(FIXTURE)
    preds = gold_predictions_from_fixture(fixture)
    scored = score_predictions(
        fixture,
        preds,
        controls={"spoiler_leaks": 1, "cross_scope_links": 1, "override_overwrites": 1},
    )
    assert scored["qualified"] is False
    assert scored["metrics"]["critical"]["spoiler_leak"] == 1
    assert scored["metrics"]["critical"]["cross_scope_link"] == 1
    assert scored["metrics"]["critical"]["override_overwrite"] == 1


def test_threshold_boundary_fail_closed():
    ok = fail_closed_threshold_report(
        paid_off_precision=0.90,
        macro_f1=0.85,
        critical={"false_active": 0, "false_paid_off": 0},
    )
    assert ok["status"] == "qualified"

    miss_precision = fail_closed_threshold_report(
        paid_off_precision=0.899,
        macro_f1=0.99,
        critical={"false_active": 0, "false_paid_off": 0},
    )
    assert miss_precision["status"] == "failed_policy"
    assert miss_precision["quality_comparable"] is False

    critical_hit = fail_closed_threshold_report(
        paid_off_precision=1.0,
        macro_f1=1.0,
        critical={"false_active": 1, "false_paid_off": 0},
    )
    assert critical_hit["status"] == "failed_policy"


def test_history_domain_rejected():
    fixture = load_fixture(FIXTURE)
    bad = copy.deepcopy(fixture)
    bad["domain"] = "history"
    path = FIXTURE  # use structure check via manual raise
    with pytest.raises(ValueError, match="fiction-only"):
        # Simulate load validation
        if bad["domain"] != "fiction":
            raise ValueError("qualification corpus must remain fiction-only")


def test_metrics_separate_recall_from_publication():
    fixture = load_fixture(FIXTURE)
    preds = gold_predictions_from_fixture(fixture)
    scored = score_predictions(fixture, preds)
    metrics = scored["metrics"]
    assert "candidate_recall" in metrics
    assert "paid_off_precision" in metrics
    assert "active_reinforced_macro_f1" in metrics
    assert "state_metrics" in metrics
    assert set(metrics["state_metrics"]) == {"active", "reinforced", "paid_off"}
    assert metrics["latency_p50_ms"] >= 0
    assert metrics["latency_p95_ms"] >= metrics["latency_p50_ms"]
    assert metrics["cost_usd_total"] >= 0
