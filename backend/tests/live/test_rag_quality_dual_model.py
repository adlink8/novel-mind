"""Live dual-model G/J/SUT path (06-04).

When Ollama/models are unavailable the result must be blocked_dependency with
metrics=null — never a fabricated pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.eval import CalibrationReport, EvalCase, SourceSnapshot
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, load_json
from app.services.rag_quality import (
    default_healthy,
    probe_ollama_health,
    run_quality_evaluation,
)

pytestmark = pytest.mark.live

SECRET = DEFAULT_SIGNING_SECRET
EVALS = Path(__file__).resolve().parents[2] / "evals"


def _load_benchmark() -> tuple[SourceSnapshot, list[EvalCase]]:
    data = load_json(EVALS / "fixtures" / "rag-quality-benchmark.v1.json")
    snap = SourceSnapshot.model_validate(data["snapshot"])
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return snap, cases


def test_live_dual_model_or_blocked_dependency():
    """Comparable live scores only when health+calibrated lineage are complete.

    Offline CI without Ollama must observe blocked_dependency / metrics=null.
    """
    snap, cases = _load_benchmark()
    g = cases[0].generator_lineage
    j = cases[0].judge_lineage
    assert g is not None and j is not None

    health = probe_ollama_health()
    cal = CalibrationReport(
        suite_hash="a" * 64,
        suite_signature="b" * 64,
        prompt_hash=j.prompt_hash,
        schema_hash=j.schema_hash,
        judge_lineage=j,
        domain="calibration-synthetic",
        repeats=3,
        confusion_matrix={},
        critical_false_accept=0,
        consistency=1.0,
        status="passed",
        metrics={"consistency": 1.0, "critical_false_accept": 0},
        quality_comparable=False,
    )

    if not health.get("ok"):
        # Explicit outage path — must not fake pass
        report = run_quality_evaluation(
            snapshot=snap,
            cases=cases[:1],
            generator_lineage=g,
            judge_lineage=j,
            calibration_report=cal,
            baseline={
                "context_recall_at_5_mean": 1.0,
                "answer_relevance_mean": 1.0,
                "cost_usd_total": 1.0,
            },
            health=health,
            secret=SECRET,
            repeats=1,
        )
        assert report["status"] == "blocked_dependency"
        assert report["metrics"] is None
        assert report["quality_comparable"] is False
        return

    # Live dependencies available: still use stub SUT for this slice unless
    # real retrieve/answer adapters are wired. Health ok + stubs can pass.
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 0.0,
            "answer_relevance_mean": 0.0,
            "cost_usd_total": 999.0,
        },
        health=default_healthy(),
        secret=SECRET,
    )
    if report["status"] in {"passed", "qualified"}:
        assert report["metrics"] is not None
        assert report["quality_comparable"] is True
    else:
        # Any fail path must null metrics
        assert report["metrics"] is None
        assert report["quality_comparable"] is False
        assert report["status"] in {
            "failed_policy",
            "quality_regression",
            "blocked_dependency",
            "invalid_fixture",
            "invalid_lineage",
            "quarantined",
        }


def test_missing_health_is_blocked_not_comparable():
    snap, cases = _load_benchmark()
    g = cases[0].generator_lineage
    j = cases[0].judge_lineage
    cal = CalibrationReport(
        suite_hash="a" * 64,
        suite_signature="b" * 64,
        prompt_hash=j.prompt_hash,
        schema_hash=j.schema_hash,
        judge_lineage=j,
        domain="calibration-synthetic",
        repeats=3,
        confusion_matrix={},
        critical_false_accept=0,
        consistency=1.0,
        status="passed",
        metrics={"consistency": 1.0},
        quality_comparable=False,
    )
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 1.0,
            "cost_usd_total": 1.0,
        },
        health=None,
        secret=SECRET,
        repeats=1,
    )
    assert report["status"] == "blocked_dependency"
    assert report["metrics"] is None
    assert report["quality_comparable"] is False
