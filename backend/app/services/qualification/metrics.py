"""Bucket-level retrieval, citation, faithfulness, relevance and operations
metrics for the reading-QA quality gate (Phase 29-02 / REQ-QA-02).

Decisions D-02..D-05 from 29-CONTEXT.md:

- D-03: retrieval, citation correctness, faithfulness, relevance, latency,
  cost, abstention, fallback and reuse are measured separately by bucket.
- D-04: candidate and leaf baseline are compared under identical source,
  cutoff and budget; violations block qualification.
- D-05: verdict is only ``qualified_candidate`` or ``blocked``.

No silent zero-fill: a missing denominator, NaN, or an unknown price produces
a ``None`` value / invalid cell rather than a fabricated zero. Pure module:
no database, no network, no provider calls.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.services.narrative_memory.qualification_contracts import MetricStatus
from app.services.qualification.gold_set import (
    GoldBucket,
    GoldSample,
    ReadingQAGoldSet,
)
from app.services.qualification.rubric import (
    CODE_BEYOND_CUTOFF,
    CODE_CHAPTER_NUMBER_MISMATCH,
    CODE_CITATION_OUTSIDE_GOLD,
    CODE_CONTENT_LEAK,
    CODE_CROSS_NOVEL,
    CODE_CROSS_OWNER,
    CODE_CROSS_SNAPSHOT,
    CODE_CROSS_VERSION,
    CODE_EVIDENCE_CONTENT_MISMATCH,
    CODE_FOREIGN_CHAPTER,
    CODE_INVALID_OFFSETS,
    CODE_SPOILER_LEAK,
    CODE_UNCITED_ASSERTION,
    audit_evidence_ref,
    to_evidence_ref,
)

SYSTEM_CANDIDATE = "candidate"
SYSTEM_BASELINE = "baseline"
SYSTEM_LABELS: tuple[str, ...] = (SYSTEM_CANDIDATE, SYSTEM_BASELINE)

TOP_K_DEFAULT = 8

# Every per-bucket metric emitted for each system. Non-applicable cells are
# ``None`` (visible), never silently zero-filled.
REQUIRED_BUCKET_METRICS: tuple[str, ...] = (
    "retrieval_recall_at_k",
    "retrieval_mrr",
    "citation_accept_rate",
    "invalid_citation_count",
    "stale_citation_count",
    "wrong_owner_citation_count",
    "spoiler_citation_count",
    "uncited_assertion_count",
    "faithfulness_mean",
    "relevance_mean",
    "abstention_rate",
    "false_answer_rate",
    "fallback_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "cost_usd_total",
    "calls_total",
    "tokens_total",
)

# Rubric codes grouped into citation categories. Every code is stable and
# defined in rubric.py; this mapping never invents new failure vocabulary.
STALE_CITATION_CODES = frozenset(
    {
        CODE_CROSS_SNAPSHOT,
        CODE_FOREIGN_CHAPTER,
        CODE_CHAPTER_NUMBER_MISMATCH,
        CODE_INVALID_OFFSETS,
        CODE_EVIDENCE_CONTENT_MISMATCH,
    }
)
WRONG_OWNER_CITATION_CODES = frozenset(
    {CODE_CROSS_OWNER, CODE_CROSS_NOVEL, CODE_CROSS_VERSION}
)
SPOILER_CITATION_CODES = frozenset({CODE_SPOILER_LEAK})
CUTOFF_CITATION_CODES = frozenset({CODE_BEYOND_CUTOFF, CODE_CONTENT_LEAK})
OUTSIDE_GOLD_CITATION_CODES = frozenset({CODE_CITATION_OUTSIDE_GOLD})


class ReadingQAMetricCell(BaseModel):
    """One deterministic metric cell; ``value`` is None for missing/invalid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_name: str
    numerator: float | int
    denominator: float | int
    value: float | int | None
    unit: str
    status: MetricStatus
    bucket: GoldBucket | None = None
    system: Literal["candidate", "baseline"] | None = None
    case_ids: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class CaseMetrics:
    """Per-sample metrics for one system, already audited against the rubric."""

    sample_id: str
    bucket: GoldBucket
    system: str
    recall: float
    mrr: float
    citation_total: int
    citation_valid: int
    stale_citations: int
    cutoff_citations: int
    wrong_owner_citations: int
    spoiler_citations: int
    outside_gold_citations: int
    uncited_assertions: int
    faithfulness: float | None
    relevance: float | None
    abstained: bool
    expected_answerability: str
    fallback_used: bool
    latency_ms: float
    calls: int
    tokens: int
    cost_usd: float | None
    provider_error: str | None
    reused: bool
    stale_reuse: bool
    carried_reuse: bool


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def _finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile; order-independent for equal multisets."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        raise ValueError("percentile of empty sequence")
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(math.ceil(p / 100.0 * len(ordered)))))
    return ordered[rank - 1]


def _mean_non_null(values: Iterable[float | int | None]) -> float | None:
    finite = [float(v) for v in values if v is not None and _finite(float(v))]
    return sum(finite) / len(finite) if finite else None


def _sum_or_none(values: Iterable[float | int | None]) -> float | None:
    finite = [float(v) for v in values if v is not None and _finite(float(v))]
    if not finite:
        return None
    return sum(finite)


def _rate(numerator: int, denominator: int) -> float | None:
    return (numerator / denominator) if denominator > 0 else None


def make_cell(
    name: str,
    *,
    numerator: float | int,
    denominator: float | int,
    unit: str,
    bucket: GoldBucket | None = None,
    system: str | None = None,
    case_ids: Sequence[str] = (),
    note: str | None = None,
    force_status: MetricStatus | None = None,
) -> ReadingQAMetricCell:
    num = float(numerator)
    den = float(denominator)
    if force_status is not None:
        value = num / den if den > 0 and _finite(num) and _finite(den) else None
        return ReadingQAMetricCell(
            metric_name=name,
            numerator=numerator,
            denominator=denominator,
            value=value,
            unit=unit,
            status=force_status,
            bucket=bucket,
            system=system,  # type: ignore[arg-type]
            case_ids=tuple(case_ids),
            note=note,
        )
    if not _finite(num) or not _finite(den):
        return ReadingQAMetricCell(
            metric_name=name,
            numerator=0,
            denominator=0,
            value=None,
            unit=unit,
            status=MetricStatus.INVALID,
            bucket=bucket,
            system=system,  # type: ignore[arg-type]
            case_ids=tuple(case_ids),
            note=note or "non_finite",
        )
    if den <= 0:
        return ReadingQAMetricCell(
            metric_name=name,
            numerator=numerator,
            denominator=denominator,
            value=None,
            unit=unit,
            status=MetricStatus.MISSING,
            bucket=bucket,
            system=system,  # type: ignore[arg-type]
            case_ids=tuple(case_ids),
            note=note or "empty_denominator",
        )
    return ReadingQAMetricCell(
        metric_name=name,
        numerator=numerator,
        denominator=denominator,
        value=num / den,
        unit=unit,
        status=MetricStatus.OK,
        bucket=bucket,
        system=system,  # type: ignore[arg-type]
        case_ids=tuple(case_ids),
        note=note,
    )


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------


def gold_evidence_keys(sample: GoldSample) -> set[str]:
    """Exact frozen evidence identities (chapter + offsets + content hash)."""
    return {
        ref.evidence_key()
        for answer in sample.source_answers
        for ref in answer.evidence
    }


def normalize_retrieved_ids(retrieved: Sequence[Any]) -> list[str]:
    """Coerce retrieved leaf ids into evidence-key strings.

    Dict entries are validated as ``GoldEvidenceRef``; anything unparseable is
    simply not a gold hit (it cannot be a hit without a frozen identity).
    """
    out: list[str] = []
    for item in retrieved or ():
        if isinstance(item, dict):
            try:
                out.append(to_evidence_ref(item).evidence_key())
            except Exception:
                continue
        else:
            out.append(str(item))
    return out


def recall_at_k(retrieved: Sequence[str], gold: Sequence[str], *, k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top = list(retrieved)[:k]
    return sum(1 for g in gold_set if g in top) / len(gold_set)


def reciprocal_rank(retrieved: Sequence[str], gold: Sequence[str]) -> float:
    gold_set = set(gold)
    for i, lid in enumerate(retrieved, start=1):
        if lid in gold_set:
            return 1.0 / i
    return 0.0


# ---------------------------------------------------------------------------
# Citation classification
# ---------------------------------------------------------------------------


def _ref_categories(
    gold_set: ReadingQAGoldSet, sample: GoldSample, ref: Any
) -> set[str]:
    """Fail-closed citation categories for one cited evidence ref."""
    try:
        ref = to_evidence_ref(ref)
    except Exception:
        return {"stale"}
    categories: set[str] = set()
    for violation in audit_evidence_ref(gold_set, sample, ref):
        if violation.code in STALE_CITATION_CODES:
            categories.add("stale")
        elif violation.code in SPOILER_CITATION_CODES:
            categories.add("spoiler")
        elif violation.code in CUTOFF_CITATION_CODES:
            categories.add("cutoff")
    forbidden = {
        f.chapter_number
        for f in sample.spoiler_forbidden
        if f.chapter_number is not None
    }
    if ref.chapter_number in forbidden:
        categories.add("spoiler")
    if (
        sample.expected_answerability == "answerable"
        and ref.evidence_key() not in gold_evidence_keys(sample)
    ):
        categories.add("outside_gold")
    return categories


# ---------------------------------------------------------------------------
# Per-case metric construction
# ---------------------------------------------------------------------------


def build_case_metrics(
    gold_set: ReadingQAGoldSet,
    sample: GoldSample,
    *,
    system: str,
    artifact: dict[str, Any],
    violations: Sequence[Any] = (),
    top_k: int = TOP_K_DEFAULT,
) -> CaseMetrics:
    """Turn one audited artifact into a deterministic ``CaseMetrics``."""
    if system not in SYSTEM_LABELS:
        raise ValueError(f"unknown system label {system!r}")

    abstained = bool(artifact.get("abstained", False))
    cited = list(artifact.get("cited_evidence") or ())
    retrieved = normalize_retrieved_ids(artifact.get("retrieved_leaf_ids") or ())
    gold_keys = list(gold_evidence_keys(sample))

    recall = recall_at_k(retrieved, gold_keys, k=top_k)
    mrr = reciprocal_rank(retrieved, gold_keys)

    citation_total = len(cited)
    stale = cutoff = spoiler = outside_gold = 0
    for ref in cited:
        cats = _ref_categories(gold_set, sample, ref)
        stale += 1 if "stale" in cats else 0
        cutoff += 1 if "cutoff" in cats else 0
        spoiler += 1 if "spoiler" in cats else 0
        outside_gold += 1 if "outside_gold" in cats else 0
    invalid_refs = stale + cutoff + spoiler + outside_gold
    citation_valid = max(0, citation_total - invalid_refs)

    violation_codes = [getattr(v, "code", str(v)) for v in violations]
    wrong_owner = sum(1 for c in violation_codes if c in WRONG_OWNER_CITATION_CODES)
    uncited = sum(1 for c in violation_codes if c == CODE_UNCITED_ASSERTION)

    return CaseMetrics(
        sample_id=sample.id,
        bucket=sample.bucket,
        system=system,
        recall=recall,
        mrr=mrr,
        citation_total=citation_total,
        citation_valid=citation_valid,
        stale_citations=stale,
        cutoff_citations=cutoff,
        wrong_owner_citations=wrong_owner,
        spoiler_citations=spoiler,
        outside_gold_citations=outside_gold,
        uncited_assertions=uncited,
        faithfulness=(
            float(artifact["faithfulness"])
            if artifact.get("faithfulness") is not None
            and _finite(float(artifact["faithfulness"]))
            else None
        ),
        relevance=(
            float(artifact["relevance"])
            if artifact.get("relevance") is not None
            and _finite(float(artifact["relevance"]))
            else None
        ),
        abstained=abstained,
        expected_answerability=sample.expected_answerability,
        fallback_used=bool(artifact.get("fallback_used", False)),
        latency_ms=float(artifact.get("latency_ms") or 0),
        calls=int(artifact.get("calls") or 0),
        tokens=int(artifact.get("input_tokens") or 0) + int(
            artifact.get("output_tokens") or 0
        ),
        cost_usd=(
            float(artifact["cost_usd"])
            if artifact.get("cost_usd") is not None
            and _finite(float(artifact["cost_usd"]))
            else None
        ),
        provider_error=artifact.get("provider_error"),
        reused=bool(artifact.get("reused", False)),
        stale_reuse=bool(artifact.get("stale_reuse", False)),
        carried_reuse=bool(artifact.get("carried_reuse", False)),
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_bucket_metrics(
    cases: Sequence[CaseMetrics],
) -> dict[str, float | int | None]:
    """Per-bucket/per-system metric dict; missing cells are None, never 0."""
    n = len(cases)
    out: dict[str, float | int | None] = {
        "retrieval_recall_at_k": (
            _mean_non_null(c.recall for c in cases) if n else None
        ),
        "retrieval_mrr": _mean_non_null(c.mrr for c in cases) if n else None,
        "invalid_citation_count": sum(
            c.citation_total - c.citation_valid for c in cases
        ),
        "stale_citation_count": sum(c.stale_citations for c in cases),
        "wrong_owner_citation_count": sum(c.wrong_owner_citations for c in cases),
        "spoiler_citation_count": sum(c.spoiler_citations for c in cases),
        "uncited_assertion_count": sum(c.uncited_assertions for c in cases),
        "faithfulness_mean": _mean_non_null(c.faithfulness for c in cases),
        "relevance_mean": _mean_non_null(c.relevance for c in cases),
        "fallback_rate": _rate(sum(1 for c in cases if c.fallback_used), n),
        "latency_p50_ms": (
            percentile([c.latency_ms for c in cases], 50) if n else None
        ),
        "latency_p95_ms": (
            percentile([c.latency_ms for c in cases], 95) if n else None
        ),
        "cost_usd_total": _sum_or_none(c.cost_usd for c in cases),
        "calls_total": sum(c.calls for c in cases),
        "tokens_total": sum(c.tokens for c in cases),
    }
    total_cit = sum(c.citation_total for c in cases)
    valid_cit = sum(c.citation_valid for c in cases)
    out["citation_accept_rate"] = (
        (valid_cit / total_cit) if total_cit > 0 else None
    )

    non_answerable = [
        c for c in cases if c.expected_answerability != "answerable"
    ]
    out["abstention_rate"] = (
        _rate(sum(1 for c in non_answerable if c.abstained), len(non_answerable))
        if non_answerable
        else None
    )
    no_answer = [c for c in cases if c.expected_answerability == "no_answer"]
    out["false_answer_rate"] = (
        _rate(sum(1 for c in no_answer if not c.abstained), len(no_answer))
        if no_answer
        else None
    )
    return out


def aggregate_operations(
    cases: Sequence[CaseMetrics],
    reuse: dict[str, Any] | None = None,
) -> dict[str, float | int | None]:
    """Run-level operations metrics over candidate + baseline cases."""
    latencies = [c.latency_ms for c in cases]
    out: dict[str, float | int | None] = {
        "latency_p50_ms": (
            percentile(latencies, 50) if latencies else None
        ),
        "latency_p95_ms": (
            percentile(latencies, 95) if latencies else None
        ),
        "cost_usd_total": _sum_or_none(c.cost_usd for c in cases),
        "calls_total": sum(c.calls for c in cases),
        "tokens_total": sum(c.tokens for c in cases),
        "fallback_count": sum(1 for c in cases if c.fallback_used),
        "provider_error_count": sum(1 for c in cases if c.provider_error),
        "reused_case_count": sum(1 for c in cases if c.reused),
    }
    for key in (
        "reuse_rebuilt_count",
        "reuse_carried_count",
        "reuse_stale_count",
        "observed_actual_cost_usd",
        "full_rebuild_upper_bound_cost_usd",
        "avoided_upper_bound_cost_usd",
    ):
        out[key] = None
    if reuse is not None:
        out["reuse_rebuilt_count"] = int(reuse.get("rebuilt_count") or 0)
        out["reuse_carried_count"] = int(reuse.get("carried_count") or 0)
        out["reuse_stale_count"] = int(reuse.get("stale_count") or 0)
        for key, block in (
            (
                "observed_actual_cost_usd",
                reuse.get("observed_actual") or {},
            ),
            (
                "full_rebuild_upper_bound_cost_usd",
                reuse.get("full_rebuild_upper_bound") or {},
            ),
            (
                "avoided_upper_bound_cost_usd",
                reuse.get("avoided_upper_bound") or {},
            ),
        ):
            out[key] = float(block["cost_usd"]) if "cost_usd" in block else None
    return out


def required_bucket_metrics_complete(
    metrics: dict[str, float | int | None],
) -> list[str]:
    """Return missing required per-bucket metric names (fail closed)."""
    return sorted(set(REQUIRED_BUCKET_METRICS) - set(metrics))


def metrics_has_promotion_capability() -> bool:
    return False


def metrics_has_provider_capability() -> bool:
    return False
