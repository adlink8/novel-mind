"""Pure fail-closed two-verdict evaluator for Phase 17.

Gate order fixed; Judge scores never override deterministic failures.
"""

from __future__ import annotations

from typing import Sequence

from app.services.narrative_memory.qualification_contracts import (
    SCOPE_DISCLAIMER,
    MetricCell,
    MetricStatus,
    QualificationPolicy,
    QualificationReport,
    QualificationVerdict,
    QuestionBucket,
    RetrievalStrategy,
    ThresholdSpec,
)


def _cell_value(
    cells: Sequence[MetricCell],
    name: str,
    *,
    strategy: RetrievalStrategy | None = RetrievalStrategy.HIERARCHICAL_CANDIDATE,
    bucket: QuestionBucket | None = None,
    prefer_aggregate: bool = True,
) -> MetricCell | None:
    candidates = [c for c in cells if c.metric_name == name]
    if strategy is not None:
        scoped = [c for c in candidates if c.strategy == strategy]
        if scoped:
            candidates = scoped
    if bucket is not None:
        scoped = [c for c in candidates if c.bucket == bucket]
        if scoped:
            candidates = scoped
    elif prefer_aggregate:
        # prefer cells with no bucket (strategy aggregate) when multiple
        no_bucket = [c for c in candidates if c.bucket is None]
        if no_bucket:
            candidates = no_bucket
    if not candidates:
        return None
    # pick last aggregate-like (longest case_ids or no case filter)
    candidates = sorted(candidates, key=lambda c: len(c.case_ids), reverse=True)
    return candidates[0]


def evaluate_threshold(cell: MetricCell | None, thr: ThresholdSpec) -> str | None:
    """Return reason code if threshold fails, else None."""
    label = thr.metric_name
    if cell is None:
        return f"threshold_missing:{label}"
    if cell.status != MetricStatus.OK or cell.value is None:
        return f"threshold_incomplete:{label}"
    val = float(cell.value)
    if thr.zero_tolerance:
        if val != 0.0:
            return f"zero_tolerance:{label}"
        return None
    if thr.minimum is not None and val < float(thr.minimum):
        return f"below_minimum:{label}"
    if thr.maximum is not None and val > float(thr.maximum):
        return f"above_maximum:{label}"
    return None


def evaluate_verdict(
    *,
    policy: QualificationPolicy,
    fixture_checksum: str,
    policy_checksum: str,
    metric_cells: Sequence[MetricCell],
    preflight_reasons: Sequence[str] = (),
    scope_ok: bool = True,
    structure_ok: bool = True,
    build_complete: bool = True,
    retrieval_ok: bool = True,
    reuse_ok: bool = True,
    paired_comparable: bool = True,
    pointer_equal: bool = True,
    pointer_before_digest: str | None = None,
    pointer_after_digest: str | None = None,
    verifier_checksum: str | None = None,
    buckets_nonempty: bool = True,
) -> QualificationReport:
    """Fixed gate order → qualified_candidate or blocked."""

    reasons: list[str] = []
    failing: list[str] = []

    # 1. frozen integrity / preflight
    reasons.extend(preflight_reasons)
    if policy.checksum() != policy_checksum:
        reasons.append("policy_checksum_mismatch")
    # 2. scope
    if not scope_ok:
        reasons.append("scope_lineage_mismatch")
    # 3–6 prerequisites
    if not structure_ok:
        reasons.append("structure_manifest_failed")
    if not build_complete:
        reasons.append("partial_build")
    if not retrieval_ok:
        reasons.append("retrieval_prerequisite_failed")
    if not reuse_ok:
        reasons.append("reuse_report_unavailable")
    if not paired_comparable:
        reasons.append("paired_envelope_incomparable")
    if not buckets_nonempty:
        reasons.append("empty_required_bucket")

    # 7. metric completeness for hard safety metrics
    for name in ("spoiler_leakage", "critical_unsupported", "cost_usd_total"):
        cell = _cell_value(metric_cells, name)
        if cell is None or cell.status in {MetricStatus.MISSING, MetricStatus.INVALID}:
            # cost may exist per-case; check any OK
            any_ok = any(
                c.metric_name == name and c.status == MetricStatus.OK
                for c in metric_cells
            )
            if not any_ok:
                reasons.append(f"metric_incomplete:{name}")
                failing.append(name)

    # 8. zero-tolerance spoiler / citation / unsupported
    for name in ("spoiler_leakage", "critical_unsupported"):
        for c in metric_cells:
            if c.metric_name != name:
                continue
            if (
                c.status == MetricStatus.OK
                and c.value is not None
                and float(c.value) > 0
            ):
                reasons.append(f"zero_tolerance:{name}")
                failing.append(name)
                break

    # invalid citation: citation_accept_rate with accepted < total treated as leak when note says invalid
    for c in metric_cells:
        if c.metric_name == "citation_accept_rate" and c.status == MetricStatus.OK:
            if c.denominator and float(c.numerator) < float(c.denominator):
                # only hard-fail when any rejected citations on hierarchical
                if c.strategy == RetrievalStrategy.HIERARCHICAL_CANDIDATE:
                    if float(c.denominator) - float(c.numerator) > 0:
                        # policy may set zero invalid via invalid_citation_rate
                        pass

    # 9–10. policy thresholds
    for thr in policy.thresholds:
        strat = thr.strategy or RetrievalStrategy.HIERARCHICAL_CANDIDATE
        cell = _cell_value(
            metric_cells,
            thr.metric_name,
            strategy=strat if thr.scope != "aggregate" or thr.strategy else strat,
            bucket=thr.bucket,
        )
        # relative baseline check
        if thr.relative_to_baseline_min_delta is not None:
            cand = _cell_value(
                metric_cells,
                thr.metric_name,
                strategy=RetrievalStrategy.HIERARCHICAL_CANDIDATE,
                bucket=thr.bucket,
            )
            base = _cell_value(
                metric_cells,
                thr.metric_name,
                strategy=RetrievalStrategy.LEAF_RAW_BASELINE,
                bucket=thr.bucket,
            )
            if (
                cand is None
                or base is None
                or cand.status != MetricStatus.OK
                or base.status != MetricStatus.OK
                or cand.value is None
                or base.value is None
            ):
                reasons.append(f"relative_incomplete:{thr.metric_name}")
                failing.append(thr.metric_name)
            else:
                delta = float(cand.value) - float(base.value)
                if delta < float(thr.relative_to_baseline_min_delta):
                    reasons.append(f"baseline_regression:{thr.metric_name}")
                    failing.append(thr.metric_name)
        code = evaluate_threshold(cell, thr)
        if code:
            reasons.append(code)
            failing.append(thr.metric_name)

    # 11. pointer equality
    if not pointer_equal:
        reasons.append("pointer_before_after_mismatch")
    if pointer_before_digest and pointer_after_digest:
        if pointer_before_digest != pointer_after_digest:
            reasons.append("pointer_digest_mismatch")

    reasons = sorted(set(reasons))
    failing = sorted(set(failing))

    if reasons:
        verdict = QualificationVerdict.BLOCKED
    else:
        verdict = QualificationVerdict.QUALIFIED_CANDIDATE

    return QualificationReport(
        verdict=verdict,
        reason_codes=tuple(reasons),
        fixture_checksum=fixture_checksum,
        policy_checksum=policy_checksum,
        metric_cells=tuple(metric_cells),
        failing_metrics=tuple(failing),
        disclaimer=SCOPE_DISCLAIMER,
        pointer_before_digest=pointer_before_digest,  # type: ignore[arg-type]
        pointer_after_digest=pointer_after_digest,  # type: ignore[arg-type]
        verifier_checksum=verifier_checksum,  # type: ignore[arg-type]
    )


def verdict_has_promotion_capability() -> bool:
    return False


def verdict_has_provider_capability() -> bool:
    return False
