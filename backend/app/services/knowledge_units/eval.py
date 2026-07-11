"""Frozen retrieval evaluation and canary gates for narrative candidates."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.knowledge_units.materialize import stable_hash


class NarrativeEvalError(ValueError):
    pass


def _metrics(gold: list[str], recalled: list[str], top_k: int) -> dict[str, float]:
    ranked = recalled[:top_k]
    gold_set = set(gold)
    hits = [1 if item in gold_set else 0 for item in ranked]
    recall = len(set(ranked) & gold_set) / len(gold_set) if gold_set else 1.0
    precision = sum(hits) / len(ranked) if ranked else (1.0 if not gold_set else 0.0)
    first = next((index + 1 for index, hit in enumerate(hits) if hit), None)
    mrr = 1.0 / first if first else 0.0
    dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(gold_set), top_k)))
    return {"recall_at_5": recall, "precision_at_5": precision, "mrr_at_5": mrr, "ndcg_at_5": dcg / ideal if ideal else 1.0}


def evaluate_fixture(payload: dict[str, Any], *, latency_budget_ms: float = 1000.0) -> dict[str, Any]:
    if payload.get("split") != "frozen" or payload.get("domain") not in {"fiction", "history"}:
        raise NarrativeEvalError("fixture must be a frozen fiction/history dataset")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise NarrativeEvalError("fixture has no cases")
    dataset_hash = stable_hash({key: value for key, value in payload.items() if key != "dataset_hash"})
    if payload.get("dataset_hash") not in {None, dataset_hash}:
        raise NarrativeEvalError("frozen dataset hash mismatch")
    strategies: dict[str, list[dict[str, float]]] = {"chunks": [], "units": [], "hybrid": []}
    latencies: dict[str, list[float]] = {name: [] for name in strategies}
    critical = {"wrong": 0, "stale": 0, "cross_owner": 0}
    faithfulness_failures = 0
    zero_results = 0
    canary_total = 0
    for case in cases:
        gold = case.get("gold_ids", [])
        for strategy in strategies:
            recalled = case.get("retrieved", {}).get(strategy, [])
            strategies[strategy].append(_metrics(gold, recalled, 5))
            latencies[strategy].append(float(case.get("latency_ms", {}).get(strategy, 0.0)))
            if strategy == "hybrid" and not recalled:
                zero_results += 1
        if not case.get("faithful", False):
            faithfulness_failures += 1
        if case.get("canary"):
            canary_total += 1
            for key in critical:
                critical[key] += int(bool(case.get(key, False)))
    summary: dict[str, dict[str, float]] = {}
    for strategy, rows in strategies.items():
        summary[strategy] = {
            metric: round(mean(row[metric] for row in rows), 6)
            for metric in ("recall_at_5", "precision_at_5", "mrr_at_5", "ndcg_at_5")
        }
        ordered = sorted(latencies[strategy])
        p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
        summary[strategy]["latency_p95_ms"] = ordered[p95_index]
    canary_passed = canary_total > 0 and sum(critical.values()) == 0
    passed = (
        summary["hybrid"]["recall_at_5"] >= summary["chunks"]["recall_at_5"]
        and summary["hybrid"]["mrr_at_5"] >= summary["chunks"]["mrr_at_5"]
        and summary["hybrid"]["latency_p95_ms"] <= latency_budget_ms
        and faithfulness_failures == 0
        and canary_passed
    )
    return {
        "passed": passed,
        "domain": payload["domain"],
        "dataset_hash": dataset_hash,
        "case_count": len(cases),
        "strategies": summary,
        "faithfulness_failures": faithfulness_failures,
        "zero_result_rate": zero_results / len(cases),
        "canary": {"passed": canary_passed, "sample_size": canary_total, **critical},
        "frozen_unchanged": True,
    }


def load_and_evaluate(path: str | Path, *, latency_budget_ms: float = 1000.0) -> dict[str, Any]:
    return evaluate_fixture(json.loads(Path(path).read_text(encoding="utf-8")), latency_budget_ms=latency_budget_ms)
