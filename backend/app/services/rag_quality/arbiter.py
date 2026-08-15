"""Deterministic policy arbiter (D-08) (rag_quality package)."""

from __future__ import annotations

from typing import Any

from .lineage import COMPARABLE_STATUSES


def apply_policy_arbiter(
    *,
    metrics: dict[str, Any] | None,
    policy: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    health: dict[str, Any] | None,
    lineage_ok: bool,
    fixture_ok: bool,
    blocked: bool = False,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    """Deterministic final gate. Missing inputs fail closed with metrics=null."""

    def _term(
        status: str, reason: str, detail: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        comparable = status in COMPARABLE_STATUSES
        return {
            "status": status,
            "metrics": metrics if comparable else None,
            "quality_comparable": comparable,
            "reason": reason,
            "detail": detail or {},
            "usable_for_baseline": comparable,
        }

    if blocked:
        return _term(
            "blocked_dependency",
            blocked_reason or "dependency unavailable",
        )
    if not fixture_ok:
        return _term("invalid_fixture", "fixture validation failed")
    if not lineage_ok:
        return _term("invalid_lineage", "lineage/calibration validation failed")
    if policy is None:
        return _term("failed_policy", "missing policy")
    thresholds = policy.get("thresholds")
    p95 = policy.get("p95_budgets")
    if not thresholds or not p95:
        return _term("failed_policy", "policy missing thresholds or p95_budgets")
    if health is None or health.get("ok") is not True:
        return _term(
            "blocked_dependency",
            (health or {}).get("reason") or "missing or unhealthy dependencies",
        )
    if baseline is None:
        return _term("failed_policy", "missing baseline")
    if metrics is None:
        return _term("failed_policy", "missing metrics")

    # Absolute gates
    faith_lb = float(metrics.get("answer_faithfulness_95lb", 0.0))
    faith_min = float(thresholds["answer_faithfulness_95lb_min"])
    if faith_lb < faith_min:
        return _term(
            "failed_policy",
            f"faithfulness 95% LB {faith_lb:.4f} < {faith_min}",
            detail={"answer_faithfulness_95lb": faith_lb},
        )

    crit_rate = float(metrics.get("critical_unsupported_claim_rate", 1.0))
    crit_max = float(thresholds["critical_unsupported_claim_rate_max"])
    if crit_rate > crit_max:
        return _term(
            "failed_policy",
            f"critical unsupported claim rate {crit_rate} > {crit_max}",
            detail={"critical_unsupported_claim_rate": crit_rate},
        )

    consistency = float(metrics.get("verdict_consistency", 0.0))
    cons_min = float(thresholds["verdict_consistency_min"])
    if consistency < cons_min:
        return _term(
            "failed_policy",
            f"verdict consistency {consistency:.4f} < {cons_min}",
            detail={"verdict_consistency": consistency},
        )

    # p95 budgets
    lat_p95 = float(metrics.get("latency_ms_p95", 0.0))
    if lat_p95 > float(p95["latency_ms"]):
        return _term(
            "failed_policy",
            f"p95 latency {lat_p95} > budget {p95['latency_ms']}",
        )
    tokens_total = float(metrics.get("tokens_total", 0.0))
    if tokens_total > float(p95["tokens_total"]):
        return _term(
            "failed_policy",
            f"tokens_total {tokens_total} > budget {p95['tokens_total']}",
        )
    cost_total = float(metrics.get("cost_usd_total", 0.0))
    if cost_total > float(p95["cost_usd"]):
        return _term(
            "failed_policy",
            f"cost_usd {cost_total} > budget {p95['cost_usd']}",
        )

    # Relative regressions vs baseline
    base_rec = baseline.get("context_recall_at_5_mean")
    base_rel = baseline.get("answer_relevance_mean")
    base_cost = baseline.get("cost_usd_total")
    if base_rec is None or base_rel is None or base_cost is None:
        return _term(
            "failed_policy",
            "baseline missing context_recall_at_5_mean / answer_relevance_mean / cost_usd_total",
        )

    rec = float(metrics.get("context_recall_at_5_mean", 0.0))
    # regression in percentage points: (baseline - current) * 100
    rec_reg_pp = (float(base_rec) - rec) * 100.0
    rec_max = float(thresholds["context_recall_at_5_regression_pp_max"])
    if rec_reg_pp > rec_max:
        return _term(
            "quality_regression",
            f"context_recall@5 regression {rec_reg_pp:.2f}pp > {rec_max}pp",
            detail={
                "baseline": base_rec,
                "current": rec,
                "regression_pp": rec_reg_pp,
            },
        )

    rel = float(metrics.get("answer_relevance_mean", 0.0))
    rel_reg_pp = (float(base_rel) - rel) * 100.0
    rel_max = float(thresholds["answer_relevance_regression_pp_max"])
    if rel_reg_pp > rel_max:
        return _term(
            "quality_regression",
            f"answer_relevance regression {rel_reg_pp:.2f}pp > {rel_max}pp",
            detail={
                "baseline": base_rel,
                "current": rel,
                "regression_pp": rel_reg_pp,
            },
        )

    cost_ratio_max = float(thresholds["cost_vs_baseline_max_ratio"])
    base_cost_f = float(base_cost)
    if base_cost_f > 0 and cost_total > base_cost_f * cost_ratio_max:
        return _term(
            "failed_policy",
            f"cost {cost_total} > baseline {base_cost_f} * {cost_ratio_max}",
            detail={"cost_usd_total": cost_total, "baseline_cost": base_cost_f},
        )
    if base_cost_f == 0 and cost_total > 0 and cost_ratio_max < float("inf"):
        # zero baseline cost: only allow zero current cost for +15% rule
        if cost_total > 0:
            # If baseline is 0, any positive cost exceeds +15% of 0
            return _term(
                "failed_policy",
                f"cost {cost_total} > baseline 0 * {cost_ratio_max}",
            )

    # Qualified if all absolute metrics strong and no regression
    strong = (
        faith_lb >= max(faith_min, 0.95)
        and consistency >= 0.95
        and rec_reg_pp <= 0
        and rel_reg_pp <= 0
    )
    status = "qualified" if strong else "passed"
    return {
        "status": status,
        "metrics": metrics,
        "quality_comparable": True,
        "reason": "all policy gates passed",
        "detail": {
            "answer_faithfulness_95lb": faith_lb,
            "verdict_consistency": consistency,
            "context_recall_regression_pp": rec_reg_pp,
            "relevance_regression_pp": rel_reg_pp,
        },
        "usable_for_baseline": True,
    }
