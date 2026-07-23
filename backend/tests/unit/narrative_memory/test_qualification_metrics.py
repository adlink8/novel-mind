"""Unit tests for Phase 17 qualification metrics."""

from __future__ import annotations

import pytest

from app.services.narrative_memory.qualification_contracts import (
    MetricStatus,
    RetrievalStrategy,
)
from app.services.narrative_memory.qualification_metrics import (
    REQUIRED_METRIC_NAMES,
    build_complete_report_cells,
    leaf_recall_at_k,
    metric_report_checksum,
    ndcg_at_k,
    percentile,
    reciprocal_rank,
)

pytestmark = pytest.mark.unit


def test_percentile_order_independent():
    a = [10.0, 20.0, 30.0, 40.0, 50.0]
    b = list(reversed(a))
    assert percentile(a, 50) == percentile(b, 50)
    assert percentile(a, 95) == percentile(b, 95)


def test_leaf_recall_and_rr_and_ndcg():
    strat = RetrievalStrategy.HIERARCHICAL_CANDIDATE
    rec = leaf_recall_at_k(
        ["a", "b", "c"], ["a", "d"], k=2, case_id="c1", strategy=strat
    )
    assert rec.status == MetricStatus.OK
    assert rec.value == 0.5
    rr = reciprocal_rank(["x", "a"], ["a"], case_id="c1", strategy=strat)
    assert rr.value == 0.5
    nd = ndcg_at_k(["a", "b"], {"a": 3.0, "b": 1.0}, k=2, case_id="c1", strategy=strat)
    assert nd.status == MetricStatus.OK
    assert nd.value is not None and float(nd.value) > 0.9


def test_missing_gold_is_missing_not_zero_success():
    cell = leaf_recall_at_k(
        ["a"], [], k=5, case_id="c1", strategy=RetrievalStrategy.LEAF_RAW_BASELINE
    )
    assert cell.status == MetricStatus.MISSING


def test_complete_report_checksum_order_independent():
    arts = [
        {
            "case_key": "local_01",
            "bucket": "local",
            "strategy": "hierarchical_candidate",
            "retrieved_leaf_ids": ["leaf-ch1-01"],
            "gold_leaf_ids": ["leaf-ch1-01"],
            "graded_relevance": {"leaf-ch1-01": 3.0},
            "route_allowed": ["local"],
            "route_chosen": "local",
            "fallback_used": False,
            "citations_accepted": 1,
            "citations_total": 1,
            "abstained": False,
            "expected_answerability": "answerable",
            "spoiler_leaks": 0,
            "critical_unsupported": 0,
            "faithfulness": 0.9,
            "relevance": 0.9,
            "latency_ms": 20.0,
            "calls": 2,
            "input_tokens": 10,
            "output_tokens": 10,
            "cost_usd": 0.001,
            "cache_hit": False,
        },
        {
            "case_key": "local_01",
            "bucket": "local",
            "strategy": "leaf_raw_baseline",
            "retrieved_leaf_ids": ["leaf-ch1-01"],
            "gold_leaf_ids": ["leaf-ch1-01"],
            "graded_relevance": {"leaf-ch1-01": 3.0},
            "route_allowed": ["local"],
            "route_chosen": "leaf_raw",
            "fallback_used": False,
            "citations_accepted": 1,
            "citations_total": 1,
            "abstained": False,
            "expected_answerability": "answerable",
            "spoiler_leaks": 0,
            "critical_unsupported": 0,
            "faithfulness": 0.8,
            "relevance": 0.8,
            "latency_ms": 15.0,
            "calls": 2,
            "input_tokens": 10,
            "output_tokens": 10,
            "cost_usd": 0.001,
            "cache_hit": False,
        },
    ]
    reuse = {
        "rebuilt_count": 1,
        "carried_count": 2,
        "stale_count": 0,
        "observed_actual": {"cost_usd": 0.01},
        "full_rebuild_upper_bound": {"cost_usd": 0.05},
        "avoided_upper_bound": {"cost_usd": 0.04, "formula": "max(0, full-obs)"},
    }
    c1 = build_complete_report_cells(arts, reuse=reuse)
    c2 = build_complete_report_cells(list(reversed(arts)), reuse=reuse)
    assert metric_report_checksum(c1) == metric_report_checksum(c2)
    names = {c.metric_name for c in c1}
    for req in REQUIRED_METRIC_NAMES:
        assert req in names, req
    # no_answer metrics only when bucket present — not required in this local-only fixture


def test_unknown_cost_is_missing():
    arts = [
        {
            "case_key": "x",
            "bucket": "local",
            "strategy": "hierarchical_candidate",
            "retrieved_leaf_ids": [],
            "gold_leaf_ids": ["g"],
            "graded_relevance": {"g": 1.0},
            "route_allowed": ["local"],
            "route_chosen": "local",
            "fallback_used": False,
            "citations_accepted": 0,
            "citations_total": 0,
            "abstained": True,
            "expected_answerability": "answerable",
            "spoiler_leaks": 0,
            "critical_unsupported": 0,
            "faithfulness": 1.0,
            "relevance": 0.0,
            "latency_ms": 1.0,
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            "cache_hit": False,
        }
    ]
    cells = build_complete_report_cells(arts, reuse=None)
    cost = [c for c in cells if c.metric_name == "cost_usd_total"][0]
    assert cost.status == MetricStatus.MISSING
