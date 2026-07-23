"""Independent Judge calibration suite contracts (06-03 / D-15)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.eval import CalibrationCase, CalibrationSuite, ModelLineage
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    InvalidLineageError,
    assert_calibration_benchmark_isolation,
    freeze_calibration_suite,
    load_json,
    prompt_file_hash,
    prompts_dir,
    resolve_lineage,
    run_judge_calibration,
    schema_contract_hash,
    verify_calibration_suite,
)

pytestmark = pytest.mark.contract

SECRET = DEFAULT_SIGNING_SECRET
EVALS = Path(__file__).resolve().parents[1] / "evals"
CREATED = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _judge_lineage(
    prompt_hash: str, schema_hash: str, rev: str = "gemma-cal-rev-1"
) -> ModelLineage:
    return resolve_lineage(
        provider="ollama",
        model_family="gemma",
        model_id="gemma4-local",
        weights_revision=rev,
        prompt_hash=prompt_hash,
        prompt_version="rag_fixture_judge.v1",
        schema_hash=schema_hash,
        started_at=CREATED,
    )


def _load_suite() -> CalibrationSuite:
    path = EVALS / "calibration" / "rag-judge-calibration.v1.json"
    data = load_json(path)
    return CalibrationSuite.model_validate(data)


def test_calibration_suite_signed_and_covers_categories():
    suite = _load_suite()
    assert verify_calibration_suite(suite, SECRET)
    cats = {c.category for c in suite.cases}
    required = {
        "supported",
        "partial",
        "unsupported",
        "contradictory",
        "no_answer",
        "hard_negative",
        "equivalent_evidence",
    }
    assert required <= cats
    assert suite.domain == "calibration-synthetic"
    assert suite.suite_type == "calibration"


def test_calibration_benchmark_domain_and_hash_isolation():
    suite = _load_suite()
    bench = load_json(EVALS / "fixtures" / "rag-quality-benchmark.v1.json")
    assert_calibration_benchmark_isolation(suite, bench)
    # negative: same hash
    with pytest.raises(InvalidLineageError):
        assert_calibration_benchmark_isolation(
            suite,
            {"suite_hash": suite.suite_hash, "domain": "other-domain"},
        )
    # negative: same domain
    with pytest.raises(InvalidLineageError):
        assert_calibration_benchmark_isolation(
            suite,
            {"suite_hash": "different" + suite.suite_hash[9:], "domain": suite.domain},
        )


def test_calibration_pass_with_oracle_stub_three_repeats():
    suite = _load_suite()
    lineage = _judge_lineage(suite.prompt_hash, suite.schema_hash)
    report = run_judge_calibration(suite, lineage, secret=SECRET, repeats=3)
    assert report.status == "passed"
    assert report.critical_false_accept == 0
    assert report.consistency >= 0.80
    assert report.metrics is not None
    assert report.metrics["repeats"] == 3
    assert report.signature
    assert report.confusion_matrix
    # confusion matrix has gold labels
    assert any(report.confusion_matrix.values())


def test_critical_false_accept_invalid_lineage():
    suite = _load_suite()
    lineage = _judge_lineage(suite.prompt_hash, suite.schema_hash, rev="bad-judge-rev")

    def greedy_accept(case: CalibrationCase, _lineage: ModelLineage) -> str:
        # Always accept — catastrophic false accept on critical negatives
        return "accept"

    report = run_judge_calibration(
        suite, lineage, judge_fn=greedy_accept, secret=SECRET, repeats=3
    )
    assert report.status == "invalid_lineage"
    assert report.critical_false_accept > 0
    assert report.metrics is None
    assert report.quality_comparable is False


def test_inconsistent_judge_invalid_lineage():
    suite = _load_suite()
    lineage = _judge_lineage(suite.prompt_hash, suite.schema_hash, rev="flaky-rev")
    counter = {"n": 0}

    def flaky(case: CalibrationCase, _lineage: ModelLineage) -> str:
        counter["n"] += 1
        # Alternate predictions so no case is fully consistent
        return case.gold_verdict if counter["n"] % 2 == 0 else "reject"

    report = run_judge_calibration(
        suite, lineage, judge_fn=flaky, secret=SECRET, repeats=3
    )
    assert report.status == "invalid_lineage"
    assert report.consistency < 0.80
    assert report.metrics is None


def test_prompt_schema_mismatch_invalid_lineage():
    suite = _load_suite()
    lineage = _judge_lineage("0" * 64, suite.schema_hash)
    report = run_judge_calibration(suite, lineage, secret=SECRET)
    assert report.status == "invalid_lineage"
    assert report.metrics is None


def test_tampered_suite_signature_invalid():
    suite = _load_suite()
    bad = suite.model_copy(update={"signature": "deadbeef" * 8})
    assert not verify_calibration_suite(bad, SECRET)
    lineage = _judge_lineage(suite.prompt_hash, suite.schema_hash)
    report = run_judge_calibration(bad, lineage, secret=SECRET)
    assert report.status == "invalid_lineage"
    assert report.metrics is None


def test_freeze_calibration_suite_roundtrip():
    j_prompt = prompt_file_hash(prompts_dir() / "rag_fixture_judge.v1.txt")
    sch = schema_contract_hash()
    cases = [
        CalibrationCase(
            case_id="mini-1",
            category="supported",
            question="q",
            candidate_answer="a",
            gold_verdict="accept",
            critical=False,
        ),
        CalibrationCase(
            case_id="mini-2",
            category="unsupported",
            question="q2",
            candidate_answer="a2",
            gold_verdict="reject",
            critical=True,
        ),
    ]
    suite = freeze_calibration_suite(
        suite_id="mini-cal",
        domain="calibration-mini",
        cases=cases,
        prompt_hash=j_prompt,
        schema_hash=sch,
        secret=SECRET,
    )
    assert verify_calibration_suite(suite, SECRET)
    lineage = _judge_lineage(j_prompt, sch)
    report = run_judge_calibration(suite, lineage, secret=SECRET)
    assert report.status == "passed"
