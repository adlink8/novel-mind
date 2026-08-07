"""Reproducible report contract tests for the reading-QA qualification runner.

Phase 29-02 / REQ-QA-02; decisions D-02..D-05 from 29-CONTEXT.md.

Covers: frozen evaluation reproducibility, candidate/leaf parity, stale /
wrong-owner / spoiler citations, provider unavailable, budget overrun, reuse
metrics, per-bucket metrics (never a single aggregate), and the two-value
verdict. Pure tests — no database, no provider.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.services.qualification.gold_set import (
    GoldBucket,
    load_gold_set,
    slice_content_hash,
)
from app.services.qualification.metrics import REQUIRED_BUCKET_METRICS
from app.services.qualification.runner import (
    CODE_BUDGET_OVERRUN,
    CODE_PROVIDER_UNAVAILABLE,
    CODE_SAMPLE_COVERAGE_MISMATCH,
    run_qualification,
)

pytestmark = [pytest.mark.integration]

GOLD_PATH = Path(__file__).resolve().parents[3] / "evals" / "reading_qa_v1.json"
QUAL_DIR = Path(__file__).resolve().parents[3] / "app" / "services" / "qualification"

COMMIT = "912ca6b423d6c2309bc2972cbfc083c4eaa280e1"


@pytest.fixture(scope="module")
def gold_set():
    return load_gold_set(GOLD_PATH)


def _header(gold_set) -> dict:
    return {
        "db_fingerprint": "db-fp-integration-001",
        "dataset_version": gold_set.dataset_version,
        "source_snapshot": gold_set.source_snapshot_hash,
        "commit": COMMIT,
        "model": "queryplan-nm-candidate.v1",
        "prompt": "prompt-hash-001",
        "schema_version": "reading-qa-canon.v1",
        "config": "config-hash-001",
        "budget": {
            "max_calls": 100,
            "max_input_tokens": 50_000,
            "max_output_tokens": 20_000,
            "max_cost_usd": "5.00",
        },
    }


def _common_fields() -> dict:
    return {
        "faithfulness": 1.0,
        "relevance": 1.0,
        "latency_ms": 12.0,
        "calls": 2,
        "input_tokens": 60,
        "output_tokens": 40,
        "cost_usd": 0.002,
        "fallback_used": False,
        "provider_error": None,
    }


def _clean_artifacts(gold_set) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sample in gold_set.samples:
        if sample.expected_answerability == "answerable":
            sa = sample.source_answers[0]
            out[sample.id] = {
                "answer": sa.answer,
                "cited_evidence": [r.model_dump(mode="json") for r in sa.evidence],
                "retrieved_leaf_ids": [r.evidence_key() for r in sa.evidence],
                "abstained": False,
            }
        else:
            out[sample.id] = {
                "answer": "",
                "cited_evidence": [],
                "retrieved_leaf_ids": [],
                "abstained": True,
            }
        out[sample.id].update(_common_fields())
    return out


def _run(gold_set, candidate, baseline, **overrides):
    kwargs = dict(
        gold_set=gold_set,
        header=_header(gold_set),
        candidate_artifacts=candidate,
        baseline_artifacts=baseline,
    )
    kwargs.update(overrides)
    return run_qualification(**kwargs)


# ---------------------------------------------------------------------------
# Frozen evaluation / report contract (D-02, D-03, D-05)
# ---------------------------------------------------------------------------


def test_clean_run_qualifies_candidate(gold_set):
    cand = _clean_artifacts(gold_set)
    report = _run(gold_set, cand, deepcopy(cand))
    assert report.verdict == "qualified_candidate"
    assert report.blocked_reasons == ()
    assert report.checksum_valid

    # Header binds db fingerprint / dataset version / source snapshot / commit /
    # model / prompt / schema / config / budget (D-02).
    assert report.header.db_fingerprint == "db-fp-integration-001"
    assert report.header.dataset_version == gold_set.dataset_version
    assert report.header.source_snapshot == gold_set.source_snapshot_hash
    assert report.header.commit == COMMIT
    assert report.header.model and report.header.prompt
    assert report.header.schema_version and report.header.config
    assert report.header.budget

    # All eight buckets, each with candidate + baseline metric blocks (D-03).
    assert len(report.buckets) == 8
    for bucket in report.buckets:
        assert bucket.sample_count >= 1
        assert "candidate" in bucket.metrics
        assert "baseline" in bucket.metrics
        for name in REQUIRED_BUCKET_METRICS:
            assert name in bucket.metrics["candidate"], (
                f"bucket {bucket.bucket} missing {name}"
            )
            assert name in bucket.metrics["baseline"]


def test_report_never_hides_failures_behind_single_score(gold_set):
    cand = _clean_artifacts(gold_set)
    report = _run(gold_set, cand, deepcopy(cand))
    dump = report.model_dump(mode="json")
    for banned in ("overall_score", "total_score", "single_score", "aggregate_score"):
        assert banned not in dump
    # Metrics are keyed per bucket, per system — not one merged score.
    assert all(
        isinstance(bucket.metrics, dict) and bucket.metrics for bucket in report.buckets
    )


def test_frozen_evaluation_reproducible(gold_set):
    cand = _clean_artifacts(gold_set)
    first = _run(gold_set, cand, deepcopy(cand))
    second = _run(gold_set, cand, deepcopy(cand))
    assert first.checksum == second.checksum
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_verdict_only_two_values(gold_set):
    cand = _clean_artifacts(gold_set)
    reports = [
        _run(gold_set, cand, deepcopy(cand)),
        _run(
            gold_set,
            {
                **cand,
                next(iter(cand)): {**cand[next(iter(cand))], "provider_error": "x"},
            },
            deepcopy(cand),
        ),
    ]
    for report in reports:
        assert report.verdict in ("qualified_candidate", "blocked")


# ---------------------------------------------------------------------------
# Candidate / leaf parity (D-04)
# ---------------------------------------------------------------------------


def test_sample_coverage_mismatch_stops_aggregation(gold_set):
    cand = _clean_artifacts(gold_set)
    base = deepcopy(cand)
    base.pop("local_01")
    report = _run(gold_set, cand, base)
    assert report.verdict == "blocked"
    assert CODE_SAMPLE_COVERAGE_MISMATCH in report.blocked_reasons
    assert report.buckets == ()  # metric aggregation stopped


def test_candidate_baseline_same_artifacts_identical_metrics(gold_set):
    cand = _clean_artifacts(gold_set)
    report = _run(gold_set, cand, deepcopy(cand))
    for bucket in report.buckets:
        assert bucket.metrics["candidate"] == bucket.metrics["baseline"]


# ---------------------------------------------------------------------------
# Citation gates: stale / wrong-owner / spoiler
# ---------------------------------------------------------------------------


def test_stale_citation_blocks_and_is_visible(gold_set):
    cand = _clean_artifacts(gold_set)
    stale_ref = cand["local_01"]["cited_evidence"][0]
    stale_ref["content_hash"] = "f" * 64  # no longer re-slices against snapshot
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert "evidence_content_mismatch" in report.blocked_reasons
    bucket = next(b for b in report.buckets if b.bucket == GoldBucket.LOCAL)
    assert bucket.blocked_reasons
    assert bucket.worst_cases
    # Candidate citation accept rate is not silently 1.0.
    assert bucket.metrics["candidate"]["citation_accept_rate"] != 1.0
    assert bucket.metrics["candidate"]["invalid_citation_count"] >= 1


def test_wrong_owner_blocks(gold_set):
    cand = _clean_artifacts(gold_set)
    cand["local_01"]["owner_id"] = gold_set.owner_id + 1
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert "cross_owner" in report.blocked_reasons


def test_baseline_lineage_violation_blocks(gold_set):
    base = _clean_artifacts(gold_set)
    base["local_01"]["owner_id"] = gold_set.owner_id + 1
    report = _run(gold_set, _clean_artifacts(gold_set), base)
    assert report.verdict == "blocked"
    assert "cross_owner" in report.blocked_reasons


def test_spoiler_citation_blocks(gold_set):
    cand = _clean_artifacts(gold_set)
    sample = next(s for s in gold_set.samples if s.bucket == GoldBucket.SPOILER)
    chapter = gold_set.chapter_by_number(6)
    spoiler_ref = {
        "chapter_id": chapter.chapter_id,
        "chapter_number": 6,
        "source_start": 0,
        "source_end": 6,
        "content_hash": slice_content_hash(chapter.content, 0, 6),
        "source_snapshot_hash": gold_set.source_snapshot_hash,
    }
    cand[sample.id] = {
        "answer": "何太太被捕，灯塔重新亮起",
        "cited_evidence": [spoiler_ref],
        "retrieved_leaf_ids": [],
        "abstained": False,
        **_common_fields(),
    }
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert "spoiler_leak" in report.blocked_reasons


# ---------------------------------------------------------------------------
# Operations gates: provider unavailable / budget overrun
# ---------------------------------------------------------------------------


def test_provider_unavailable_blocks(gold_set):
    cand = _clean_artifacts(gold_set)
    cand["local_01"]["provider_error"] = "provider_timeout"
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert CODE_PROVIDER_UNAVAILABLE in report.blocked_reasons
    assert report.buckets == ()


def test_budget_overrun_blocks(gold_set):
    cand = _clean_artifacts(gold_set)
    cand["local_01"]["calls"] = 10_000  # > max_calls 100
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert CODE_BUDGET_OVERRUN in report.blocked_reasons
    assert report.buckets == ()


# ---------------------------------------------------------------------------
# Answer gates: no-answer hallucination (metrics stay visible)
# ---------------------------------------------------------------------------


def test_no_answer_hallucination_blocked_with_bucket_metrics(gold_set):
    cand = _clean_artifacts(gold_set)
    sample = next(s for s in gold_set.samples if s.bucket == GoldBucket.NO_ANSWER)
    cand[sample.id] = {
        "answer": "她叫李月",
        "cited_evidence": [],
        "retrieved_leaf_ids": [],
        "abstained": False,
        **_common_fields(),
    }
    report = _run(gold_set, cand, deepcopy(_clean_artifacts(gold_set)))
    assert report.verdict == "blocked"
    assert "no_answer_hallucination" in report.blocked_reasons
    # Failures are per-bucket visible, never hidden behind one score.
    assert report.buckets
    bucket = next(b for b in report.buckets if b.bucket == GoldBucket.NO_ANSWER)
    assert bucket.blocked_reasons
    assert bucket.worst_cases


# ---------------------------------------------------------------------------
# Metric completeness (D-03)
# ---------------------------------------------------------------------------


def test_latency_cost_calls_tokens_present_per_bucket(gold_set):
    cand = _clean_artifacts(gold_set)
    report = _run(gold_set, cand, deepcopy(cand))
    for bucket in report.buckets:
        for system in ("candidate", "baseline"):
            metrics = bucket.metrics[system]
            assert metrics["latency_p50_ms"] is not None
            assert metrics["latency_p95_ms"] is not None
            assert metrics["cost_usd_total"] is not None
            assert metrics["calls_total"] >= 1
            assert metrics["tokens_total"] >= 1


def test_abstention_and_false_answer_metrics(gold_set):
    cand = _clean_artifacts(gold_set)
    report = _run(gold_set, cand, deepcopy(cand))
    no_answer = next(b for b in report.buckets if b.bucket == GoldBucket.NO_ANSWER)
    assert no_answer.metrics["candidate"]["abstention_rate"] == 1.0
    assert no_answer.metrics["candidate"]["false_answer_rate"] == 0.0
    spoiler = next(b for b in report.buckets if b.bucket == GoldBucket.SPOILER)
    assert spoiler.metrics["candidate"]["abstention_rate"] == 1.0


def test_reuse_metrics_present(gold_set):
    cand = _clean_artifacts(gold_set)
    reuse = {
        "rebuilt_count": 2,
        "carried_count": 5,
        "stale_count": 1,
        "observed_actual": {"cost_usd": 0.2},
        "full_rebuild_upper_bound": {"cost_usd": 1.5},
        "avoided_upper_bound": {"cost_usd": 1.3},
    }
    report = _run(gold_set, cand, deepcopy(cand), reuse=reuse)
    assert report.operations.reuse_rebuilt_count == 2
    assert report.operations.reuse_carried_count == 5
    assert report.operations.reuse_stale_count == 1
    assert report.operations.observed_actual_cost_usd == 0.2
    assert report.operations.full_rebuild_upper_bound_cost_usd == 1.5
    assert report.operations.avoided_upper_bound_cost_usd == 1.3
    assert report.operations.calls_total >= 1
    assert report.operations.tokens_total >= 1
    assert report.operations.latency_p50_ms is not None
    assert report.operations.latency_p95_ms is not None


# ---------------------------------------------------------------------------
# Module purity
# ---------------------------------------------------------------------------


def test_modules_never_touch_provider_or_promotion():
    for name in ("runner.py", "metrics.py", "report.py"):
        src = (QUAL_DIR / name).read_text(encoding="utf-8")
        for forbidden in (
            "litellm",
            "openai",
            "prepare_baseline",
            "ActiveBaseline",
            "httpx",
            "asyncpg",
            "requests",
        ):
            assert forbidden not in src, f"{name} contains {forbidden!r}"
    from app.services.qualification.runner import (
        runner_has_promotion_capability,
        runner_has_provider_capability,
    )

    assert runner_has_promotion_capability() is False
    assert runner_has_provider_capability() is False
