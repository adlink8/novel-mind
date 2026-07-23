"""Complete metric cells for single-book hierarchical vs leaf qualification.

No silent zero-fill. Missing denominators or NaN → blocked/invalid cells.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from app.services.narrative_memory.qualification_contracts import (
    MetricCell,
    MetricStatus,
    QuestionBucket,
    RetrievalStrategy,
    stable_checksum,
)


def _finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))


def make_cell(
    name: str,
    *,
    numerator: float | int,
    denominator: float | int,
    unit: str,
    case_ids: Sequence[str] = (),
    bucket: QuestionBucket | None = None,
    strategy: RetrievalStrategy | None = None,
    note: str | None = None,
    force_status: MetricStatus | None = None,
) -> MetricCell:
    num = float(numerator)
    den = float(denominator)
    if force_status is not None:
        value = (num / den) if den > 0 and _finite(num) and _finite(den) else None
        return MetricCell(
            metric_name=name,
            numerator=numerator,
            denominator=denominator,
            value=value,
            unit=unit,
            status=force_status,
            case_ids=tuple(case_ids),
            bucket=bucket,
            strategy=strategy,
            note=note,
        )
    if not _finite(num) or not _finite(den):
        return MetricCell(
            metric_name=name,
            numerator=0,
            denominator=0,
            value=None,
            unit=unit,
            status=MetricStatus.INVALID,
            case_ids=tuple(case_ids),
            bucket=bucket,
            strategy=strategy,
            note=note or "non_finite",
        )
    if den <= 0:
        return MetricCell(
            metric_name=name,
            numerator=numerator,
            denominator=denominator,
            value=None,
            unit=unit,
            status=MetricStatus.MISSING,
            case_ids=tuple(case_ids),
            bucket=bucket,
            strategy=strategy,
            note=note or "empty_denominator",
        )
    return MetricCell(
        metric_name=name,
        numerator=numerator,
        denominator=denominator,
        value=num / den,
        unit=unit,
        status=MetricStatus.OK,
        case_ids=tuple(case_ids),
        bucket=bucket,
        strategy=strategy,
        note=note,
    )


def leaf_recall_at_k(
    retrieved_ids: Sequence[str],
    gold_ids: Sequence[str],
    *,
    k: int,
    case_id: str,
    strategy: RetrievalStrategy,
    bucket: QuestionBucket | None = None,
) -> MetricCell:
    gold = set(gold_ids)
    top = list(retrieved_ids)[:k]
    hits = sum(1 for g in gold if g in top)
    return make_cell(
        "leaf_recall_at_k",
        numerator=hits,
        denominator=max(len(gold), 1) if gold else 0,
        unit="ratio",
        case_ids=(case_id,),
        strategy=strategy,
        bucket=bucket,
        force_status=MetricStatus.MISSING if not gold else None,
    )


def reciprocal_rank(
    retrieved_ids: Sequence[str],
    gold_ids: Sequence[str],
    *,
    case_id: str,
    strategy: RetrievalStrategy,
    bucket: QuestionBucket | None = None,
) -> MetricCell:
    gold = set(gold_ids)
    if not gold:
        return make_cell(
            "reciprocal_rank",
            numerator=0,
            denominator=0,
            unit="score",
            case_ids=(case_id,),
            strategy=strategy,
            bucket=bucket,
            force_status=MetricStatus.MISSING,
        )
    rr = 0.0
    for i, lid in enumerate(retrieved_ids, start=1):
        if lid in gold:
            rr = 1.0 / i
            break
    return make_cell(
        "reciprocal_rank",
        numerator=rr,
        denominator=1,
        unit="score",
        case_ids=(case_id,),
        strategy=strategy,
        bucket=bucket,
    )


def ndcg_at_k(
    retrieved_ids: Sequence[str],
    graded: dict[str, float],
    *,
    k: int,
    case_id: str,
    strategy: RetrievalStrategy,
    bucket: QuestionBucket | None = None,
) -> MetricCell:
    if not graded:
        return make_cell(
            "ndcg_at_k",
            numerator=0,
            denominator=0,
            unit="score",
            case_ids=(case_id,),
            strategy=strategy,
            bucket=bucket,
            force_status=MetricStatus.MISSING,
        )

    def dcg(ids: Sequence[str]) -> float:
        s = 0.0
        for i, lid in enumerate(ids[:k], start=1):
            rel = float(graded.get(lid, 0.0))
            s += (2**rel - 1) / math.log2(i + 1)
        return s

    ideal = sorted(graded.values(), reverse=True)
    idcg = 0.0
    for i, rel in enumerate(ideal[:k], start=1):
        idcg += (2**rel - 1) / math.log2(i + 1)
    if idcg <= 0:
        return make_cell(
            "ndcg_at_k",
            numerator=0,
            denominator=0,
            unit="score",
            case_ids=(case_id,),
            strategy=strategy,
            bucket=bucket,
            force_status=MetricStatus.MISSING,
        )
    score = dcg(retrieved_ids) / idcg
    return make_cell(
        "ndcg_at_k",
        numerator=score,
        denominator=1,
        unit="score",
        case_ids=(case_id,),
        strategy=strategy,
        bucket=bucket,
    )


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile; order-independent for equal multisets."""
    if not values:
        raise ValueError("empty values for percentile")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(math.ceil(p / 100.0 * len(ordered)))))
    return ordered[rank - 1]


def latency_cells(
    latencies_ms: Sequence[float],
    *,
    name_prefix: str,
    strategy: RetrievalStrategy | None,
    case_ids: Sequence[str],
) -> list[MetricCell]:
    if not latencies_ms:
        return [
            make_cell(
                f"{name_prefix}_p50_ms",
                numerator=0,
                denominator=0,
                unit="ms",
                case_ids=case_ids,
                strategy=strategy,
                force_status=MetricStatus.MISSING,
            ),
            make_cell(
                f"{name_prefix}_p95_ms",
                numerator=0,
                denominator=0,
                unit="ms",
                case_ids=case_ids,
                strategy=strategy,
                force_status=MetricStatus.MISSING,
            ),
        ]
    p50 = percentile(latencies_ms, 50)
    p95 = percentile(latencies_ms, 95)
    n = len(latencies_ms)
    return [
        make_cell(
            f"{name_prefix}_p50_ms",
            numerator=p50,
            denominator=1,
            unit="ms",
            case_ids=case_ids,
            strategy=strategy,
        ),
        make_cell(
            f"{name_prefix}_p95_ms",
            numerator=p95,
            denominator=1,
            unit="ms",
            case_ids=case_ids,
            strategy=strategy,
        ),
        make_cell(
            f"{name_prefix}_sample_count",
            numerator=n,
            denominator=1,
            unit="count",
            case_ids=case_ids,
            strategy=strategy,
        ),
    ]


def rate_cell(
    name: str,
    successes: int,
    total: int,
    *,
    case_ids: Sequence[str],
    strategy: RetrievalStrategy | None = None,
    bucket: QuestionBucket | None = None,
) -> MetricCell:
    return make_cell(
        name,
        numerator=successes,
        denominator=total,
        unit="ratio",
        case_ids=case_ids,
        strategy=strategy,
        bucket=bucket,
    )


def aggregate_mean_cells(
    cells: Iterable[MetricCell],
    *,
    metric_name: str,
    strategy: RetrievalStrategy | None = None,
    bucket: QuestionBucket | None = None,
) -> MetricCell:
    ok = [
        c
        for c in cells
        if c.metric_name == metric_name
        and c.status == MetricStatus.OK
        and c.value is not None
    ]
    if not ok:
        return make_cell(
            metric_name,
            numerator=0,
            denominator=0,
            unit="ratio",
            strategy=strategy,
            bucket=bucket,
            force_status=MetricStatus.MISSING,
        )
    total = sum(float(c.value) for c in ok)  # type: ignore[arg-type]
    ids: list[str] = []
    for c in ok:
        ids.extend(c.case_ids)
    return make_cell(
        metric_name,
        numerator=total,
        denominator=len(ok),
        unit=ok[0].unit,
        case_ids=tuple(sorted(set(ids))),
        strategy=strategy,
        bucket=bucket,
    )


REQUIRED_METRIC_NAMES = (
    "leaf_recall_at_k",
    "reciprocal_rank",
    "ndcg_at_k",
    "route_hit",
    "fallback_rate",
    "citation_accept_rate",
    "invalid_citation_rate",
    "spoiler_leakage",
    "critical_unsupported",
    "faithfulness_mean",
    "relevance_mean",
    "end_to_end_latency_p95_ms",
    "calls_total",
    "tokens_total",
    "cost_usd_total",
    "cache_hit_rate",
    "reuse_rebuilt_count",
    "reuse_carried_count",
    "reuse_stale_count",
    "observed_actual_cost_usd",
    "full_rebuild_upper_bound_cost_usd",
    "avoided_upper_bound_cost_usd",
)

# Emitted when corresponding buckets are present in the run.
CONDITIONAL_METRIC_NAMES = (
    "no_answer_abstention",
    "false_answer_rate",
)


def build_complete_report_cells(
    case_artifacts: list[dict[str, Any]],
    *,
    reuse: dict[str, Any] | None,
    top_k: int = 8,
) -> list[MetricCell]:
    """Build case + bucket + strategy + aggregate cells from strategy artifacts.

    Each artifact keys: case_key, bucket, strategy, retrieved_leaf_ids, gold_leaf_ids,
    graded_relevance, route_allowed, route_chosen, fallback_used, citations_accepted,
    citations_total, abstained, expected_answerability, spoiler_leaks,
    critical_unsupported, faithfulness, relevance, latency_ms, calls, input_tokens,
    output_tokens, cost_usd, cache_hit.
    """
    cells: list[MetricCell] = []
    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for art in case_artifacts:
        strat = RetrievalStrategy(art["strategy"])
        bucket = QuestionBucket(art["bucket"])
        case_id = art["case_key"]
        by_strategy[strat.value].append(art)
        by_bucket[bucket.value].append(art)

        gold = list(art.get("gold_leaf_ids") or [])
        retrieved = list(art.get("retrieved_leaf_ids") or [])
        graded = dict(art.get("graded_relevance") or {})

        cells.append(
            leaf_recall_at_k(
                retrieved, gold, k=top_k, case_id=case_id, strategy=strat, bucket=bucket
            )
        )
        cells.append(
            reciprocal_rank(
                retrieved, gold, case_id=case_id, strategy=strat, bucket=bucket
            )
        )
        cells.append(
            ndcg_at_k(
                retrieved,
                graded,
                k=top_k,
                case_id=case_id,
                strategy=strat,
                bucket=bucket,
            )
        )

        route_hit = (
            1 if art.get("route_chosen") in (art.get("route_allowed") or []) else 0
        )
        cells.append(
            rate_cell(
                "route_hit",
                route_hit,
                1,
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )
        cells.append(
            rate_cell(
                "fallback_rate",
                1 if art.get("fallback_used") else 0,
                1,
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )
        cit_t = int(art.get("citations_total") or 0)
        cit_a = int(art.get("citations_accepted") or 0)
        cells.append(
            rate_cell(
                "citation_accept_rate",
                cit_a,
                cit_t if cit_t > 0 else 0,
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )
        invalid_n = max(0, cit_t - cit_a)
        cells.append(
            make_cell(
                "invalid_citation_rate",
                numerator=invalid_n,
                denominator=max(cit_t, 1) if cit_t > 0 else 1,
                unit="ratio",
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
            if cit_t > 0
            else make_cell(
                "invalid_citation_rate",
                numerator=0,
                denominator=1,
                unit="ratio",
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )

        expected = art.get("expected_answerability")
        abstained = bool(art.get("abstained"))
        if expected == "no_answer":
            cells.append(
                rate_cell(
                    "no_answer_abstention",
                    1 if abstained else 0,
                    1,
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                )
            )
            cells.append(
                rate_cell(
                    "false_answer_rate",
                    0 if abstained else 1,
                    1,
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                )
            )

        leaks = int(art.get("spoiler_leaks") or 0)
        cells.append(
            make_cell(
                "spoiler_leakage",
                numerator=leaks,
                denominator=1,
                unit="count",
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )
        unsup = int(art.get("critical_unsupported") or 0)
        cells.append(
            make_cell(
                "critical_unsupported",
                numerator=unsup,
                denominator=1,
                unit="count",
                case_ids=(case_id,),
                strategy=strat,
                bucket=bucket,
            )
        )
        faith = art.get("faithfulness")
        rel = art.get("relevance")
        if faith is None or not _finite(float(faith)):
            cells.append(
                make_cell(
                    "faithfulness_mean",
                    numerator=0,
                    denominator=0,
                    unit="score",
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                    force_status=MetricStatus.MISSING,
                )
            )
        else:
            cells.append(
                make_cell(
                    "faithfulness_mean",
                    numerator=float(faith),
                    denominator=1,
                    unit="score",
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                )
            )
        if rel is None or not _finite(float(rel)):
            cells.append(
                make_cell(
                    "relevance_mean",
                    numerator=0,
                    denominator=0,
                    unit="score",
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                    force_status=MetricStatus.MISSING,
                )
            )
        else:
            cells.append(
                make_cell(
                    "relevance_mean",
                    numerator=float(rel),
                    denominator=1,
                    unit="score",
                    case_ids=(case_id,),
                    strategy=strat,
                    bucket=bucket,
                )
            )

        lat = float(art.get("latency_ms") or 0)
        cells.extend(
            latency_cells(
                [lat],
                name_prefix="end_to_end_latency",
                strategy=strat,
                case_ids=(case_id,),
            )
        )
        cells.append(
            make_cell(
                "calls_total",
                numerator=int(art.get("calls") or 0),
                denominator=1,
                unit="count",
                case_ids=(case_id,),
                strategy=strat,
            )
        )
        tokens = int(art.get("input_tokens") or 0) + int(art.get("output_tokens") or 0)
        cells.append(
            make_cell(
                "tokens_total",
                numerator=tokens,
                denominator=1,
                unit="count",
                case_ids=(case_id,),
                strategy=strat,
            )
        )
        cost = art.get("cost_usd")
        if cost is None:
            cells.append(
                make_cell(
                    "cost_usd_total",
                    numerator=0,
                    denominator=0,
                    unit="usd",
                    case_ids=(case_id,),
                    strategy=strat,
                    force_status=MetricStatus.MISSING,
                    note="unknown_price_or_usage",
                )
            )
        else:
            cells.append(
                make_cell(
                    "cost_usd_total",
                    numerator=float(cost),
                    denominator=1,
                    unit="usd",
                    case_ids=(case_id,),
                    strategy=strat,
                )
            )
        cells.append(
            rate_cell(
                "cache_hit_rate",
                1 if art.get("cache_hit") else 0,
                1,
                case_ids=(case_id,),
                strategy=strat,
            )
        )

    # Strategy-level latency aggregates
    for strat_name, arts in by_strategy.items():
        strat = RetrievalStrategy(strat_name)
        lats = [float(a.get("latency_ms") or 0) for a in arts]
        ids = [a["case_key"] for a in arts]
        cells.extend(
            latency_cells(
                lats, name_prefix="end_to_end_latency", strategy=strat, case_ids=ids
            )
        )
        cells.append(
            aggregate_mean_cells(
                [c for c in cells if c.strategy == strat],
                metric_name="leaf_recall_at_k",
                strategy=strat,
            )
        )
        cells.append(
            aggregate_mean_cells(
                [c for c in cells if c.strategy == strat],
                metric_name="faithfulness_mean",
                strategy=strat,
            )
        )

    # Bucket aggregates for hierarchical
    for bname, arts in by_bucket.items():
        bucket = QuestionBucket(bname)
        for strat_name in ("hierarchical_candidate", "leaf_raw_baseline"):
            subset = [a for a in arts if a["strategy"] == strat_name]
            if not subset:
                continue
            strat = RetrievalStrategy(strat_name)
            cells.append(
                aggregate_mean_cells(
                    [c for c in cells if c.bucket == bucket and c.strategy == strat],
                    metric_name="leaf_recall_at_k",
                    strategy=strat,
                    bucket=bucket,
                )
            )
            if bucket == QuestionBucket.NO_ANSWER:
                cells.append(
                    aggregate_mean_cells(
                        [
                            c
                            for c in cells
                            if c.bucket == bucket and c.strategy == strat
                        ],
                        metric_name="no_answer_abstention",
                        strategy=strat,
                        bucket=bucket,
                    )
                )

    # Reuse economics (Phase 16)
    if reuse is None:
        for name in (
            "reuse_rebuilt_count",
            "reuse_carried_count",
            "reuse_stale_count",
            "observed_actual_cost_usd",
            "full_rebuild_upper_bound_cost_usd",
            "avoided_upper_bound_cost_usd",
        ):
            cells.append(
                make_cell(
                    name,
                    numerator=0,
                    denominator=0,
                    unit="count" if "count" in name else "usd",
                    force_status=MetricStatus.MISSING,
                    note="reuse_unavailable",
                )
            )
    else:
        cells.append(
            make_cell(
                "reuse_rebuilt_count",
                numerator=int(reuse.get("rebuilt_count") or 0),
                denominator=1,
                unit="count",
            )
        )
        cells.append(
            make_cell(
                "reuse_carried_count",
                numerator=int(reuse.get("carried_count") or 0),
                denominator=1,
                unit="count",
            )
        )
        cells.append(
            make_cell(
                "reuse_stale_count",
                numerator=int(reuse.get("stale_count") or 0),
                denominator=1,
                unit="count",
            )
        )
        obs = reuse.get("observed_actual") or {}
        full = reuse.get("full_rebuild_upper_bound") or {}
        avoided = reuse.get("avoided_upper_bound") or {}
        for name, block, key in (
            ("observed_actual_cost_usd", obs, "cost_usd"),
            ("full_rebuild_upper_bound_cost_usd", full, "cost_usd"),
            ("avoided_upper_bound_cost_usd", avoided, "cost_usd"),
        ):
            if key not in block:
                cells.append(
                    make_cell(
                        name,
                        numerator=0,
                        denominator=0,
                        unit="usd",
                        force_status=MetricStatus.MISSING,
                    )
                )
            else:
                cells.append(
                    make_cell(
                        name,
                        numerator=float(block[key]),
                        denominator=1,
                        unit="usd",
                        note=block.get("label") or block.get("formula"),
                    )
                )

    return cells


def metric_report_checksum(cells: Sequence[MetricCell]) -> str:
    payload = [c.model_dump(mode="json") for c in cells]
    # sort for order independence
    payload.sort(
        key=lambda d: (
            d["metric_name"],
            d.get("strategy") or "",
            d.get("bucket") or "",
            list(d.get("case_ids") or []),
        )
    )
    return stable_checksum(payload)


def required_metrics_complete(cells: Sequence[MetricCell]) -> list[str]:
    """Return reason codes for missing required aggregate metrics."""
    reasons: list[str] = []
    names = {c.metric_name for c in cells}
    for req in REQUIRED_METRIC_NAMES:
        if req not in names:
            reasons.append(f"missing_metric:{req}")
            continue
        # any INVALID for zero-tolerance family blocks
        bad = [
            c
            for c in cells
            if c.metric_name == req
            and c.status in {MetricStatus.MISSING, MetricStatus.INVALID}
            and c.strategy in (None, RetrievalStrategy.HIERARCHICAL_CANDIDATE)
        ]
        # reuse metrics are strategy-less
        if (
            req.startswith("reuse_")
            or req.endswith("_cost_usd")
            and "actual" in req
            or "upper_bound" in req
        ):
            bad = [
                c for c in cells if c.metric_name == req and c.status != MetricStatus.OK
            ]
        if bad and req in {
            "spoiler_leakage",
            "critical_unsupported",
            "cost_usd_total",
            "observed_actual_cost_usd",
            "full_rebuild_upper_bound_cost_usd",
            "avoided_upper_bound_cost_usd",
            "reuse_rebuilt_count",
            "reuse_carried_count",
            "reuse_stale_count",
        }:
            reasons.append(f"incomplete_metric:{req}")
    return sorted(set(reasons))
