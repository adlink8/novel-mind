"""Candidate-bound frozen retrieval evaluation and signed release evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from pathlib import Path
from statistics import mean
from typing import Any, Awaitable, Callable

from app.services.knowledge_units.materialize import stable_hash

RetrievalAdapter = Callable[[str, dict[str, Any]], Awaitable[list[dict[str, Any]]]]
STRATEGIES = ("chunks", "units", "hybrid")


class NarrativeEvalError(ValueError):
    pass


def fixture_hash(payload: dict[str, Any]) -> str:
    return stable_hash(
        {key: value for key, value in payload.items() if key != "dataset_hash"}
    )


def validate_fixture(payload: dict[str, Any]) -> str:
    if payload.get("split") != "frozen" or payload.get("domain") not in {
        "fiction",
        "history",
    }:
        raise NarrativeEvalError("fixture must be a frozen fiction/history dataset")
    if not isinstance(payload.get("cases"), list) or not payload["cases"]:
        raise NarrativeEvalError("fixture has no cases")
    forbidden = {
        "retrieved",
        "latency_ms",
        "faithful",
        "passed",
        "canary_passed",
        "wrong",
        "stale",
        "cross_owner",
    }
    if any(forbidden.intersection(case) for case in payload["cases"]):
        raise NarrativeEvalError(
            "frozen cases may contain only query/gold/expected evidence"
        )
    actual = fixture_hash(payload)
    if not payload.get("dataset_hash") or not hmac.compare_digest(
        payload["dataset_hash"], actual
    ):
        raise NarrativeEvalError("frozen dataset hash mismatch")
    return actual


def _metrics(gold: list[str], recalled: list[str], top_k: int = 5) -> dict[str, float]:
    ranked, gold_set = recalled[:top_k], set(gold)
    hits = [item in gold_set for item in ranked]
    dcg = sum(int(hit) / math.log2(i + 2) for i, hit in enumerate(hits))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gold_set), top_k)))
    first = next((i + 1 for i, hit in enumerate(hits) if hit), None)
    return {
        "recall_at_5": len(set(ranked) & gold_set) / len(gold_set) if gold_set else 1.0,
        "precision_at_5": sum(hits) / len(ranked)
        if ranked
        else (1.0 if not gold_set else 0.0),
        "mrr_at_5": 1 / first if first else 0.0,
        "ndcg_at_5": dcg / ideal if ideal else 1.0,
    }


def sign_run(report: dict[str, Any], secret: str) -> str:
    body = json.dumps(
        {k: v for k, v in report.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def verify_run(report: dict[str, Any], secret: str) -> bool:
    signature = report.get("signature", "")
    return bool(signature) and hmac.compare_digest(signature, sign_run(report, secret))


async def evaluate_candidate(
    payload: dict[str, Any],
    *,
    build: Any,
    retrieve: RetrievalAdapter,
    signing_secret: str,
    latency_budget_ms: float = 1000.0,
) -> dict[str, Any]:
    dataset_hash = validate_fixture(payload)
    if not build.collection_name or build.status not in {"candidate", "active"}:
        raise NarrativeEvalError("evaluation requires an indexed candidate build")
    outputs, rows = [], {name: [] for name in STRATEGIES}
    latencies = {name: [] for name in STRATEGIES}
    critical = {"wrong_build": 0, "cross_owner": 0, "stale": 0}
    faithfulness_failures = 0
    for case in payload["cases"]:
        case_output = {"id": case["id"], "query": case["query"], "strategies": {}}
        for strategy in STRATEGIES:
            context = {
                "strategy": strategy,
                "build_id": build.id,
                "candidate_checksum": build.manifest_checksum,
                "collection": build.collection_name,
                "owner_id": build.owner_id,
                "novel_id": build.novel_id,
                "domain": build.domain_profile,
                "top_k": 5,
            }
            started = time.perf_counter_ns()
            results = await retrieve(case["query"], context)
            elapsed = (time.perf_counter_ns() - started) / 1_000_000
            ids = [str(item["id"]) for item in results]
            rows[strategy].append(_metrics(case.get("gold_ids", []), ids))
            latencies[strategy].append(elapsed)
            faithful = (
                all(
                    set(item.get("evidence_ids", ()))
                    & set(case.get("gold_evidence_ids", ()))
                    for item in results
                )
                if results and case.get("gold_evidence_ids")
                else True
            )
            faithfulness_failures += int(strategy == "hybrid" and not faithful)
            for item in results:
                meta = item.get("metadata", {})
                critical["wrong_build"] += int(
                    meta.get("build_id") != build.id
                    or meta.get("manifest_checksum") != build.manifest_checksum
                )
                critical["cross_owner"] += int(
                    meta.get("owner_id") != build.owner_id
                    or meta.get("novel_id") != build.novel_id
                )
                critical["stale"] += int(
                    meta.get("lifecycle_status") in {"deleted", "deprecated"}
                )
            case_output["strategies"][strategy] = {
                "retrieved_ids": ids,
                "latency_ms": elapsed,
                "faithful": faithful,
            }
        outputs.append(case_output)
    summary = {}
    for strategy in STRATEGIES:
        ordered = sorted(latencies[strategy])
        summary[strategy] = {
            metric: round(mean(row[metric] for row in rows[strategy]), 6)
            for metric in rows[strategy][0]
        }
        summary[strategy].update(
            latency_p50_ms=ordered[(len(ordered) - 1) // 2],
            latency_p95_ms=ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        )
    canary_passed = not any(critical.values())
    passed = (
        summary["hybrid"]["recall_at_5"] >= summary["chunks"]["recall_at_5"]
        and summary["hybrid"]["mrr_at_5"] >= summary["chunks"]["mrr_at_5"]
        and summary["hybrid"]["latency_p95_ms"] <= latency_budget_ms
        and not faithfulness_failures
        and canary_passed
    )
    report = {
        "run_id": stable_hash(
            {"build": build.id, "dataset": dataset_hash, "outputs": outputs}
        ),
        "passed": passed,
        "domain": payload["domain"],
        "dataset_hash": dataset_hash,
        "build_id": build.id,
        "candidate_checksum": build.manifest_checksum,
        "collection": build.collection_name,
        "owner_id": build.owner_id,
        "novel_id": build.novel_id,
        "strategies": summary,
        "outputs": outputs,
        "faithfulness_failures": faithfulness_failures,
        "canary": {"passed": canary_passed, **critical},
    }
    report["signature"] = sign_run(report, signing_secret)
    return report


def load_fixture(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_fixture(payload)
    return payload


def candidate_retriever(
    build: Any,
    *,
    db: Any,
    strategy: Any | None = None,
) -> RetrievalAdapter:
    """Candidate-bound adapter over the same production retrieval policy as the API."""

    from app.services.knowledge_units.search import (
        production_retrieval_strategy,
        select_candidate_build,
    )

    policy = strategy or production_retrieval_strategy()
    selector = select_candidate_build(build)

    async def retrieve(query: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await policy.search_novel(
            db,
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            query=query,
            mode=context["strategy"],
            top_k=context["top_k"],
            build_selector=selector,
        )
        return [
            {
                "id": str(row.get("unit_id") or row.get("chunk_id")),
                "metadata": {
                    "build_id": build.id,
                    "manifest_checksum": build.manifest_checksum,
                    "owner_id": build.owner_id,
                    "novel_id": build.novel_id,
                    "lifecycle_status": row.get("lifecycle", "current"),
                    "source_type": row.get("source_type", "chunk"),
                },
                "evidence_ids": [
                    str(item)
                    for item in (
                        row.get("evidence_refs")
                        or [row.get("chunk_id")]
                    )
                    if item is not None
                ],
            }
            for row in rows
        ]

    return retrieve
