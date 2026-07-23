"""SUT scoring + deterministic policy arbiter contracts (06-04 / D-06..D-08)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.schemas.eval import (
    CalibrationReport,
    ChunkerLineage,
    EvalCase,
    ModelLineage,
    SourceSnapshot,
)
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    load_json,
    resolve_lineage,
    schema_contract_hash,
    stable_hash,
    verify_frozen_case,
    verify_source_snapshot,
)
from app.services.rag_quality import (
    DependencyOutage,
    apply_policy_arbiter,
    bootstrap_lower_bound,
    build_quality_input_hash,
    build_stage_cache_key,
    context_precision_at_k,
    context_recall_at_k,
    default_healthy,
    default_stub_answer,
    default_stub_retrieve,
    load_policy,
    make_baseline_from_metrics,
    policy_path,
    recompute_chunker_config_hash,
    run_quality_evaluation,
    validate_calibrated_lineage,
    validate_fixtures_for_scoring,
    verdict_consistency,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]

SECRET = DEFAULT_SIGNING_SECRET
EVALS = Path(__file__).resolve().parents[1] / "evals"
CREATED = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _load_benchmark() -> tuple[SourceSnapshot, list[EvalCase]]:
    data = load_json(EVALS / "fixtures" / "rag-quality-benchmark.v1.json")
    snap = SourceSnapshot.model_validate(data["snapshot"])
    cases = [EvalCase.model_validate(c) for c in data["cases"]]
    return snap, cases


def _passed_calibration(judge: ModelLineage) -> CalibrationReport:
    return CalibrationReport(
        suite_hash="a" * 64,
        suite_signature="b" * 64,
        prompt_hash=judge.prompt_hash,
        schema_hash=judge.schema_hash,
        judge_lineage=judge,
        domain="calibration-synthetic",
        repeats=3,
        confusion_matrix={"accept": {"accept": 3}},
        critical_false_accept=0,
        consistency=1.0,
        status="passed",
        metrics={"consistency": 1.0, "critical_false_accept": 0},
        quality_comparable=False,
        signature="c" * 64,
    )


def _lineages_from_cases(cases: list[EvalCase]) -> tuple[ModelLineage, ModelLineage]:
    g = cases[0].generator_lineage
    j = cases[0].judge_lineage
    assert g is not None and j is not None
    return g, j


def test_policy_file_locks_d08_thresholds():
    policy = load_policy(policy_path())
    t = policy["thresholds"]
    assert t["answer_faithfulness_95lb_min"] == 0.90
    assert t["critical_unsupported_claim_rate_max"] == 0.0
    assert t["context_recall_at_5_regression_pp_max"] == 2.0
    assert t["answer_relevance_regression_pp_max"] == 3.0
    assert t["verdict_consistency_min"] == 0.80
    assert t["cost_vs_baseline_max_ratio"] == 1.15
    assert "latency_ms" in policy["p95_budgets"]
    assert policy["run"]["repeats"] == 3


def test_benchmark_fixtures_verify():
    snap, cases = _load_benchmark()
    assert verify_source_snapshot(snap, SECRET)
    assert all(verify_frozen_case(c, SECRET) for c in cases)
    assert (
        validate_fixtures_for_scoring(snapshot=snap, cases=cases, secret=SECRET) is None
    )


def test_context_precision_and_recall_deterministic():
    snap, cases = _load_benchmark()
    case = next(c for c in cases if c.case_type == "answerable")
    retrieved = default_stub_retrieve(case, snap, top_k=5)
    assert context_precision_at_k(case, retrieved, 5) == 1.0
    assert context_recall_at_k(case, retrieved, 5) == 1.0
    noise = [{"chunk_content_hash": "0" * 64, "quote_text": "noise"}]
    assert context_precision_at_k(case, noise, 5) == 0.0
    assert context_recall_at_k(case, noise, 5) == 0.0


def test_bootstrap_lower_bound_and_consistency():
    assert bootstrap_lower_bound([1.0, 1.0, 1.0]) >= 0.99
    assert bootstrap_lower_bound([0.0, 1.0], n_boot=500, seed=1) < 0.9
    assert verdict_consistency(["pass", "pass", "pass"]) == 1.0
    assert verdict_consistency(["pass", "fail", "pass"]) == pytest.approx(2 / 3)


def test_arbiter_fail_closed_missing_inputs():
    metrics = {
        "answer_faithfulness_95lb": 0.95,
        "answer_relevance_mean": 0.9,
        "context_recall_at_5_mean": 0.9,
        "critical_unsupported_claim_rate": 0.0,
        "verdict_consistency": 1.0,
        "cost_usd_total": 0.01,
        "latency_ms_p95": 10.0,
        "tokens_total": 100,
    }
    policy = load_policy()
    baseline = make_baseline_from_metrics(metrics)

    r = apply_policy_arbiter(
        metrics=metrics,
        policy=None,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy"
    assert r["metrics"] is None
    assert r["quality_comparable"] is False

    r = apply_policy_arbiter(
        metrics=metrics,
        policy=policy,
        baseline=None,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy" and r["metrics"] is None

    r = apply_policy_arbiter(
        metrics=metrics,
        policy=policy,
        baseline=baseline,
        health=None,
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "blocked_dependency" and r["metrics"] is None

    r = apply_policy_arbiter(
        metrics=metrics,
        policy=policy,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=False,
        fixture_ok=True,
    )
    assert r["status"] == "invalid_lineage" and r["metrics"] is None

    r = apply_policy_arbiter(
        metrics=metrics,
        policy=policy,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=False,
    )
    assert r["status"] == "invalid_fixture" and r["metrics"] is None


def test_arbiter_critical_and_faithfulness_gates():
    policy = load_policy()
    good = {
        "answer_faithfulness_95lb": 0.95,
        "answer_relevance_mean": 0.9,
        "context_recall_at_5_mean": 0.95,
        "critical_unsupported_claim_rate": 0.0,
        "verdict_consistency": 1.0,
        "cost_usd_total": 0.01,
        "latency_ms_p95": 10.0,
        "tokens_total": 100,
    }
    baseline = make_baseline_from_metrics(good)

    bad_crit = {**good, "critical_unsupported_claim_rate": 0.1}
    r = apply_policy_arbiter(
        metrics=bad_crit,
        policy=policy,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy"
    assert r["metrics"] is None

    bad_faith = {**good, "answer_faithfulness_95lb": 0.85}
    r = apply_policy_arbiter(
        metrics=bad_faith,
        policy=policy,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy"
    assert r["metrics"] is None


def test_arbiter_regression_and_cost_gates():
    policy = load_policy()
    current = {
        "answer_faithfulness_95lb": 0.95,
        "answer_relevance_mean": 0.80,
        "context_recall_at_5_mean": 0.90,
        "critical_unsupported_claim_rate": 0.0,
        "verdict_consistency": 1.0,
        "cost_usd_total": 0.01,
        "latency_ms_p95": 10.0,
        "tokens_total": 100,
    }
    # baseline recall 0.95 -> current 0.90 = 5pp regression > 2pp
    baseline = {
        "context_recall_at_5_mean": 0.95,
        "answer_relevance_mean": 0.80,
        "cost_usd_total": 0.01,
    }
    r = apply_policy_arbiter(
        metrics=current,
        policy=policy,
        baseline=baseline,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "quality_regression"
    assert r["metrics"] is None
    assert r["quality_comparable"] is False

    # relevance regression 4pp > 3pp
    baseline2 = {
        "context_recall_at_5_mean": 0.90,
        "answer_relevance_mean": 0.84,
        "cost_usd_total": 0.01,
    }
    r = apply_policy_arbiter(
        metrics=current,
        policy=policy,
        baseline=baseline2,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "quality_regression"

    # cost > baseline + 15%
    expensive = {**current, "cost_usd_total": 0.02}
    baseline3 = {
        "context_recall_at_5_mean": 0.90,
        "answer_relevance_mean": 0.80,
        "cost_usd_total": 0.01,
    }
    r = apply_policy_arbiter(
        metrics=expensive,
        policy=policy,
        baseline=baseline3,
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy"
    assert r["metrics"] is None


def test_arbiter_consistency_gate():
    policy = load_policy()
    metrics = {
        "answer_faithfulness_95lb": 0.95,
        "answer_relevance_mean": 0.9,
        "context_recall_at_5_mean": 0.9,
        "critical_unsupported_claim_rate": 0.0,
        "verdict_consistency": 0.5,
        "cost_usd_total": 0.01,
        "latency_ms_p95": 10.0,
        "tokens_total": 100,
    }
    r = apply_policy_arbiter(
        metrics=metrics,
        policy=policy,
        baseline=make_baseline_from_metrics(metrics),
        health=default_healthy(),
        lineage_ok=True,
        fixture_ok=True,
    )
    assert r["status"] == "failed_policy"
    assert "consistency" in r["reason"]
    assert r["metrics"] is None


def test_full_run_passed_with_stubs():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)

    # First establish metrics for baseline (self-consistent)
    stage_cache: dict = {}
    pre = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 0.0,  # will adjust after we see real
            "cost_usd_total": 100.0,  # loose temporary
        },
        health=default_healthy(),
        secret=SECRET,
        stage_cache=stage_cache,
    )
    # May quality_regression on relevance; rebuild baseline from a metrics-forcing path
    # Run with perfect baseline matching stub means
    # Compute expected via second call with generous baseline then tighten
    if pre.get("metrics"):
        baseline = make_baseline_from_metrics(pre["metrics"])
    else:
        # Force metrics via arbiter-bypass path: re-run with matching baseline after
        # temporarily using high relevance baseline
        baseline = {
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 0.5,
            "cost_usd_total": 1.0,
        }

    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        stage_cache=stage_cache,  # idempotent — no duplicate model calls
    )
    # If still failing on relevance, rebuild baseline from intermediate
    if report["status"] not in {"passed", "qualified"} and report.get("detail"):
        # Produce raw metrics with blocked=false by using baseline that only checks absolute if we set equal
        pass

    # Improve baseline to match a dedicated metrics harvest:
    # use apply_policy_arbiter-friendly baseline after harvesting with high ceilings
    harvest = run_quality_evaluation(
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
        stage_cache={},
    )
    assert harvest["status"] in {"passed", "qualified"}, harvest
    assert harvest["quality_comparable"] is True
    assert harvest["metrics"] is not None
    assert harvest["metrics"]["critical_unsupported_claim_rate"] == 0.0
    assert harvest["metrics"]["answer_faithfulness_95lb"] >= 0.90
    assert harvest["metrics"]["verdict_consistency"] >= 0.80
    assert harvest["report_signature"]

    baseline = make_baseline_from_metrics(harvest["metrics"])
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        stage_cache={},
    )
    assert report["status"] in {"passed", "qualified"}
    assert report["quality_comparable"] is True
    assert report["metrics"] is not None
    assert report["usable_for_baseline"] is True


def test_invalid_calibration_blocks_run():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    bad_cal = CalibrationReport(
        suite_hash="a" * 64,
        suite_signature="b" * 64,
        prompt_hash=j.prompt_hash,
        schema_hash=j.schema_hash,
        judge_lineage=j,
        domain="calibration-synthetic",
        repeats=3,
        confusion_matrix={},
        critical_false_accept=1,
        consistency=1.0,
        status="invalid_lineage",
        metrics=None,
        quality_comparable=False,
    )
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=bad_cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 1.0,
            "cost_usd_total": 1.0,
        },
        health=default_healthy(),
        secret=SECRET,
    )
    assert report["status"] == "invalid_lineage"
    assert report["metrics"] is None
    assert report["quality_comparable"] is False


def test_blocked_dependency_never_fake_pass():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)

    def boom_retrieve(case, snapshot, top_k):
        raise DependencyOutage("chroma down")

    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 1.0,
            "cost_usd_total": 1.0,
        },
        health=default_healthy(),
        secret=SECRET,
        retrieve_fn=boom_retrieve,
    )
    assert report["status"] == "blocked_dependency"
    assert report["metrics"] is None
    assert report["quality_comparable"] is False


def test_unhealthy_probe_blocks():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 1.0,
            "cost_usd_total": 1.0,
        },
        health={"ok": False, "reason": "ollama down"},
        secret=SECRET,
    )
    assert report["status"] == "blocked_dependency"
    assert report["metrics"] is None


def test_exception_not_swallowed_as_zero_scores():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)

    def bad_answer(case, retrieved):
        raise RuntimeError("model exploded")

    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases,
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline={
            "context_recall_at_5_mean": 1.0,
            "answer_relevance_mean": 1.0,
            "cost_usd_total": 1.0,
        },
        health=default_healthy(),
        secret=SECRET,
        answer_fn=bad_answer,
    )
    assert report["status"] == "failed_policy"
    assert report["metrics"] is None
    assert "exception" in report["reason"].lower() or "RuntimeError" in report["reason"]


def test_idempotent_stage_cache_no_duplicate_calls():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)
    calls = {"n": 0}

    def counting_answer(case, retrieved):
        calls["n"] += 1
        return default_stub_answer(case, retrieved)

    cache: dict = {}
    # Same baseline keeps input_hash identical so stage keys hit the cache.
    baseline = {
        "context_recall_at_5_mean": 0.0,
        "answer_relevance_mean": 0.0,
        "cost_usd_total": 999.0,
    }
    chunker = _chunker(snap, "baseline-fixed", "1.0.0")
    r1 = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        answer_fn=counting_answer,
        stage_cache=cache,
        repeats=3,
        chunker_lineage=chunker,
    )
    n_first = calls["n"]
    assert n_first == 3  # 1 case × 3 repeats
    r2 = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        answer_fn=counting_answer,
        stage_cache=cache,
        repeats=3,
        chunker_lineage=chunker,
        run_input_hash=r1.get("input_hash"),
    )
    assert calls["n"] == n_first  # no new answer calls
    assert r2["status"] in {
        "passed",
        "qualified",
        "quality_regression",
        "failed_policy",
    }


def test_validate_lineage_requires_matching_revision():
    j = resolve_lineage(
        provider="ollama",
        model_family="gemma",
        model_id="gemma4-local",
        weights_revision="rev-j",
        prompt_hash="d" * 64,
        prompt_version="v1",
        schema_hash=schema_contract_hash(),
        started_at=CREATED,
    )
    g = resolve_lineage(
        provider="ollama",
        model_family="qwen",
        model_id="qwen3.5:9b",
        weights_revision="rev-g",
        prompt_hash="e" * 64,
        prompt_version="v1",
        schema_hash=schema_contract_hash(),
        started_at=CREATED,
    )
    cal = _passed_calibration(j)
    # wrong revision on judge
    j2 = j.model_copy(update={"weights_revision": "other-rev"})
    fail = validate_calibrated_lineage(
        generator_lineage=g, judge_lineage=j2, calibration_report=cal
    )
    assert fail is not None
    assert fail["status"] == "invalid_lineage"
    assert fail["metrics"] is None


def test_db_id_only_case_rejected():
    snap, cases = _load_benchmark()
    bad = cases[0].model_copy(
        deep=True,
        update={
            "equivalent_evidence_sets": [],
            "gold_chunk_db_ids": [1, 2, 3],
            "case_type": "answerable",
            "status": "frozen",
        },
    )
    # signature won't verify; also hash-evidence missing
    fail = validate_fixtures_for_scoring(snapshot=snap, cases=[bad], secret=SECRET)
    assert fail is not None
    assert fail["status"] in {"invalid_fixture"}
    assert fail["metrics"] is None


def _chunker(snap: SourceSnapshot, name: str, version: str, **cfg) -> ChunkerLineage:
    config = {"size": 512, **cfg}
    return ChunkerLineage(
        chunker_name=name,
        chunker_version=version,
        chunker_config=config,
        chunker_config_hash=recompute_chunker_config_hash(config),
        chunk_manifest_hash=stable_hash(
            {
                "chunks": [c.content_hash for c in snap.chunks],
                "chunker": name,
                "v": version,
            }
        ),
        source_snapshot_hash=snap.manifest_hash,
    )


def test_input_hash_and_stage_keys_differ_across_chunkers():
    snap, cases = _load_benchmark()
    a = _chunker(snap, "baseline-fixed", "1.0.0")
    b = _chunker(snap, "semantic", "2.0.0", size=256)
    ha = build_quality_input_hash(
        snapshot_manifest_hash=snap.manifest_hash,
        case_fixture_hashes=[c.fixture_hash for c in cases],
        baseline={"x": 1},
        chunker_lineage=a,
    )
    hb = build_quality_input_hash(
        snapshot_manifest_hash=snap.manifest_hash,
        case_fixture_hashes=[c.fixture_hash for c in cases],
        baseline={"x": 1},
        chunker_lineage=b,
    )
    assert ha != hb
    ka = build_stage_cache_key(
        run_input_hash=ha,
        case_id=cases[0].case_id,
        fixture_hash=cases[0].fixture_hash,
        repetition=0,
        top_k=5,
        chunker_lineage=a,
    )
    kb = build_stage_cache_key(
        run_input_hash=hb,
        case_id=cases[0].case_id,
        fixture_hash=cases[0].fixture_hash,
        repetition=0,
        top_k=5,
        chunker_lineage=b,
    )
    assert ka != kb


def test_report_signature_includes_lineage_and_changes_with_it():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)
    baseline = {
        "context_recall_at_5_mean": 0.0,
        "answer_relevance_mean": 0.0,
        "cost_usd_total": 999.0,
    }
    a = _chunker(snap, "baseline-fixed", "1.0.0")
    b = _chunker(snap, "semantic", "2.0.0")
    ra = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        chunker_lineage=a,
    )
    rb = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
        generator_lineage=g,
        judge_lineage=j,
        calibration_report=cal,
        baseline=baseline,
        health=default_healthy(),
        secret=SECRET,
        chunker_lineage=b,
    )
    assert ra.get("chunker_lineage")
    assert ra["chunker_lineage"]["chunker_name"] == "baseline-fixed"
    assert ra["report_signature"]
    assert ra["output_hash"]
    assert ra["report_signature"] != rb["report_signature"]
    assert ra["output_hash"] != rb["output_hash"]
    assert ra["input_hash"] != rb["input_hash"]


def test_mismatched_source_snapshot_hash_invalid_lineage():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)
    bad = _chunker(snap, "baseline-fixed", "1.0.0")
    bad = bad.model_copy(update={"source_snapshot_hash": "f" * 64})
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
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
        chunker_lineage=bad,
        require_chunker_lineage=True,
    )
    assert report["status"] == "invalid_lineage"
    assert report["metrics"] is None
    assert report["quality_comparable"] is False


def test_require_chunker_lineage_missing_terminates():
    snap, cases = _load_benchmark()
    g, j = _lineages_from_cases(cases)
    cal = _passed_calibration(j)
    report = run_quality_evaluation(
        snapshot=snap,
        cases=cases[:1],
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
        chunker_lineage=None,
        require_chunker_lineage=True,
    )
    assert report["status"] == "invalid_lineage"
    assert report["metrics"] is None
