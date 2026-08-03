"""Candidate/leaf parity qualification runner for the reading-QA gate.

Phase 29-02 / REQ-QA-02; decisions D-02..D-05 from 29-CONTEXT.md.

The runner compares the QueryPlan/NM candidate against the leaf baseline under
the *identical* frozen gold set (same source snapshot, cutoff and budget),
consumes the Phase 28-04 ``DimensionResult``/``CandidateManifest`` contract,
and emits a lineage-bound report whose only verdicts are
``qualified_candidate`` or ``blocked`` (D-05).

Fail-closed order:

1. Header lineage (db fingerprint / dataset version / source snapshot / budget).
2. Manifest contract + candidate-vs-baseline manifest parity (Task 3).
3. Gold-set dataset audit (fingerprint, agreement, lineage, buckets).
4. Sample coverage parity between candidate and baseline.
5. Provider availability and budget overrun.
6. Rubric candidate audit (owner / spoiler / evidence / leakage gates).

A failure in 1-5 stops metric aggregation entirely; rubric violations (6)
still produce per-bucket metrics so the report never hides failures behind a
single aggregate score.

Pure module: no database, no network, no provider calls.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from app.services.narrative_memory.contracts import CandidateManifest
from app.services.narrative_memory.manifest_contract import (
    ManifestContractError,
    validate_candidate_manifest,
)
from app.services.qualification.gold_set import (
    GOLD_BUCKETS,
    GoldBucket,
    ReadingQAGoldSet,
)
from app.services.qualification.metrics import (
    SYSTEM_BASELINE,
    SYSTEM_CANDIDATE,
    CaseMetrics,
    aggregate_bucket_metrics,
    aggregate_operations,
    build_case_metrics,
    required_bucket_metrics_complete,
)
from app.services.qualification.report import (
    BucketReport,
    DimensionSnapshot,
    ManifestSnapshot,
    OperationsReport,
    QualificationHeader,
    QualificationReport,
    VERDICT_BLOCKED,
    VERDICT_QUALIFIED,
    WorstCase,
    build_report,
)
from app.services.qualification.rubric import (
    CODE_CROSS_NOVEL,
    CODE_CROSS_OWNER,
    CODE_CROSS_SNAPSHOT,
    CODE_CROSS_VERSION,
    audit_candidate_answer,
    audit_dataset,
)

# Stable machine codes (D-05: blocked is a first-class legal outcome).
CODE_MANIFEST_PARITY_FAILED = "manifest_parity_failed"
CODE_MANIFEST_CHECKSUM_FAILED = "manifest_checksum_failed"
CODE_HEADER_SNAPSHOT_MISMATCH = "header_source_snapshot_mismatch"
CODE_HEADER_DATASET_MISMATCH = "header_dataset_version_mismatch"
CODE_SAMPLE_COVERAGE_MISMATCH = "sample_coverage_mismatch"
CODE_PROVIDER_UNAVAILABLE = "provider_unavailable"
CODE_BUDGET_OVERRUN = "budget_overrun"
CODE_GOLD_AUDIT_FAILED = "gold_audit_failed"
CODE_CANDIDATE_VIOLATIONS = "candidate_violations"

# Lineage violations on EITHER system are parity failures (D-04): the
# candidate and leaf baseline must share the identical owner/snapshot lineage.
LINEAGE_BLOCK_CODES = frozenset(
    {CODE_CROSS_OWNER, CODE_CROSS_NOVEL, CODE_CROSS_VERSION, CODE_CROSS_SNAPSHOT}
)

MANIFEST_PARITY_FIELDS = (
    "source_snapshot_hash",
    "cutoff",
    "owner_id",
    "version_id",
    "version_key",
    "budget",
    "lineage",
)

# Sample artifact keys are read with safe defaults (missing keys never crash).


class QualificationRunnerError(ValueError):
    """Fail-closed runner error carrying a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Header / budget / coverage helpers
# ---------------------------------------------------------------------------


def _validate_header(header: dict[str, Any] | QualificationHeader) -> QualificationHeader:
    if isinstance(header, QualificationHeader):
        return header
    try:
        return QualificationHeader.model_validate(header)
    except Exception as exc:
        raise QualificationRunnerError(
            "invalid_header", f"run header invalid: {exc}"
        ) from exc


def _budget_overruns(
    artifact: dict[str, Any], budget: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    if (
        "max_calls" in budget
        and int(artifact.get("calls") or 0) > int(budget["max_calls"])
    ):
        reasons.append("calls")
    if (
        "max_input_tokens" in budget
        and int(artifact.get("input_tokens") or 0) > int(budget["max_input_tokens"])
    ):
        reasons.append("input_tokens")
    if (
        "max_output_tokens" in budget
        and int(artifact.get("output_tokens") or 0) > int(budget["max_output_tokens"])
    ):
        reasons.append("output_tokens")
    cost = artifact.get("cost_usd")
    if (
        "max_cost_usd" in budget
        and cost is not None
        and float(cost) > float(budget["max_cost_usd"])
    ):
        reasons.append("cost_usd")
    return reasons


def _manifest_comparison_reasons(
    candidate: CandidateManifest | None, baseline: CandidateManifest | None
) -> list[str]:
    """Field-by-field candidate-vs-baseline manifest parity (Task 3)."""
    if candidate is None and baseline is None:
        return []
    if candidate is None or baseline is None:
        return ["manifest_missing"]
    reasons: list[str] = []
    for field in MANIFEST_PARITY_FIELDS:
        if getattr(candidate, field) != getattr(baseline, field):
            reasons.append(f"{field}_mismatch")
    cand_dims = {d.dimension: d for d in candidate.dimensions}
    base_dims = {d.dimension: d for d in baseline.dimensions}
    for dimension in sorted(set(cand_dims) | set(base_dims)):
        left = cand_dims.get(dimension)
        right = base_dims.get(dimension)
        if left is None or right is None:
            reasons.append(f"dimension_missing:{dimension}")
            continue
        if left.blocked_reason != right.blocked_reason:
            reasons.append(f"blocked_reason_mismatch:{dimension}")
    return reasons


def _manifest_snapshot(manifest: CandidateManifest | None) -> ManifestSnapshot | None:
    if manifest is None:
        return None
    return ManifestSnapshot(
        source_snapshot_hash=manifest.source_snapshot_hash,
        cutoff=manifest.cutoff,
        owner_id=manifest.owner_id,
        version_id=manifest.version_id,
        version_key=manifest.version_key,
        dimensions=tuple(
            DimensionSnapshot(
                dimension=str(d.dimension),
                status=str(d.status),
                progress=d.progress,
                blocked_reason=d.blocked_reason,
            )
            for d in manifest.dimensions
        ),
    )


def _case_context(
    gold_set: ReadingQAGoldSet, artifact: dict[str, Any]
) -> dict[str, int]:
    """Server-enforced lineage context: owner/novel/version must match."""
    return {
        "owner_id": int(artifact.get("owner_id", gold_set.owner_id)),
        "novel_id": int(artifact.get("novel_id", gold_set.novel_id)),
        "version_id": int(artifact.get("version_id", gold_set.version_id)),
    }


# ---------------------------------------------------------------------------
# Worst-case sampling
# ---------------------------------------------------------------------------


def _worst_reason(cm: CaseMetrics, violations: Iterable[Any]) -> tuple[str, str]:
    """Pick one dominant weakness with a stable label and short detail."""
    vs = list(violations)
    if vs:
        codes = ";".join(sorted({getattr(v, "code", str(v)) for v in vs}))
        return "violations", codes
    if cm.recall < 1.0:
        return "lowest_recall", f"recall={cm.recall:.3f}"
    if cm.faithfulness is not None and cm.faithfulness < 1.0:
        return "lowest_faithfulness", f"faithfulness={cm.faithfulness:.3f}"
    if cm.relevance is not None and cm.relevance < 1.0:
        return "lowest_relevance", f"relevance={cm.relevance:.3f}"
    return "highest_latency", f"latency={cm.latency_ms:.1f}ms"


def _worst_cases_for_system(
    bucket: GoldBucket,
    system: str,
    case_metrics: dict[str, CaseMetrics],
    violations_by_sample: dict[str, list[Any]],
    limit: int = 3,
) -> tuple[WorstCase, ...]:
    ranked = sorted(
        case_metrics.values(),
        key=lambda cm: (
            1 if violations_by_sample.get(cm.sample_id) else 0,
            cm.recall,
            -(cm.faithfulness or 0.0),
            -(cm.relevance or 0.0),
            -cm.latency_ms,
        ),
    )
    worst: list[WorstCase] = []
    for cm in ranked[:limit]:
        reason, detail = _worst_reason(cm, violations_by_sample.get(cm.sample_id) or [])
        worst.append(
            WorstCase(
                sample_id=cm.sample_id,
                bucket=bucket,
                system=system,  # type: ignore[arg-type]
                reason=reason,
                detail=detail,
            )
        )
    return tuple(worst)


# ---------------------------------------------------------------------------
# Qualification entry point
# ---------------------------------------------------------------------------


def run_qualification(
    *,
    gold_set: ReadingQAGoldSet,
    header: dict[str, Any] | QualificationHeader,
    candidate_artifacts: dict[str, dict[str, Any]],
    baseline_artifacts: dict[str, dict[str, Any]],
    candidate_manifest: CandidateManifest | None = None,
    baseline_manifest: CandidateManifest | None = None,
    reuse: dict[str, Any] | None = None,
    top_k: int = 8,
) -> QualificationReport:
    """Deterministic two-verdict qualification of candidate vs leaf baseline."""
    reasons: list[str] = []

    # 1. Header lineage binding (D-02).
    hdr = _validate_header(header)
    if hdr.source_snapshot != gold_set.source_snapshot_hash:
        reasons.append(CODE_HEADER_SNAPSHOT_MISMATCH)
    if hdr.dataset_version != gold_set.dataset_version:
        reasons.append(CODE_HEADER_DATASET_MISMATCH)

    # 2. Manifest contract + candidate/baseline manifest parity (Task 3).
    manifest_snapshot = None
    if candidate_manifest is not None or baseline_manifest is not None:
        for label, manifest in (
            ("candidate", candidate_manifest),
            ("baseline", baseline_manifest),
        ):
            if manifest is None:
                continue
            try:
                validate_candidate_manifest(manifest)
            except ManifestContractError:
                reasons.append(CODE_MANIFEST_CHECKSUM_FAILED)
                break
            else:
                if manifest.source_snapshot_hash != gold_set.source_snapshot_hash:
                    reasons.append(CODE_HEADER_SNAPSHOT_MISMATCH)
        cmp_reasons = _manifest_comparison_reasons(
            candidate_manifest, baseline_manifest
        )
        if cmp_reasons:
            reasons.append(CODE_MANIFEST_PARITY_FAILED)
            # Expose the concrete parity fields so the report never hides why.
            reasons.extend(cmp_reasons)
        manifest_snapshot = _manifest_snapshot(
            candidate_manifest or baseline_manifest
        )

    # 3. Frozen gold-set audit (defense in depth; load already froze it).
    dataset_violations = audit_dataset(gold_set)
    if dataset_violations:
        reasons.append(CODE_GOLD_AUDIT_FAILED)

    # 4. Sample coverage parity (D-04: identical source/cutoff/budget).
    expected = {s.id for s in gold_set.samples}
    cand_keys = set(candidate_artifacts)
    base_keys = set(baseline_artifacts)
    if cand_keys != expected or base_keys != expected or cand_keys != base_keys:
        reasons.append(CODE_SAMPLE_COVERAGE_MISMATCH)

    # 5. Provider availability + budget parity.
    for label, artifacts in (
        (SYSTEM_CANDIDATE, candidate_artifacts),
        (SYSTEM_BASELINE, baseline_artifacts),
    ):
        for sample_id, art in artifacts.items():
            if art.get("provider_error"):
                reasons.append(CODE_PROVIDER_UNAVAILABLE)
            overruns = _budget_overruns(art, hdr.budget)
            if overruns:
                reasons.append(CODE_BUDGET_OVERRUN)

    # Hard gates fail closed BEFORE any metric aggregation.
    if reasons:
        return build_report(
            header=hdr,
            buckets=[],
            operations=OperationsReport(),
            manifest=manifest_snapshot,
            blocked_reasons=reasons,
            verdict=VERDICT_BLOCKED,
        )

    # 6. Rubric audit for candidate and baseline (per-sample violations).
    candidate_violations: dict[str, list[Any]] = {}
    baseline_violations: dict[str, list[Any]] = {}
    for sample in gold_set.samples:
        cand = candidate_artifacts[sample.id]
        base = baseline_artifacts[sample.id]
        candidate_violations[sample.id] = audit_candidate_answer(
            gold_set,
            sample,
            answer=cand.get("answer", ""),
            cited_evidence=cand.get("cited_evidence", ()),
            abstained=bool(cand.get("abstained", False)),
            context=_case_context(gold_set, cand),
        )
        baseline_violations[sample.id] = audit_candidate_answer(
            gold_set,
            sample,
            answer=base.get("answer", ""),
            cited_evidence=base.get("cited_evidence", ()),
            abstained=bool(base.get("abstained", False)),
            context=_case_context(gold_set, base),
        )

    candidate_violation_codes = sorted(
        {
            v.code
            for violations in candidate_violations.values()
            for v in violations
        }
    )
    # D-04: a lineage violation on the baseline is also a parity failure.
    baseline_lineage_codes = sorted(
        {
            v.code
            for violations in baseline_violations.values()
            for v in violations
            if v.code in LINEAGE_BLOCK_CODES
        }
    )
    if candidate_violation_codes:
        reasons.append(CODE_CANDIDATE_VIOLATIONS)

    # Per-case metrics.
    candidate_metrics: dict[str, CaseMetrics] = {}
    baseline_metrics: dict[str, CaseMetrics] = {}
    for sample in gold_set.samples:
        candidate_metrics[sample.id] = build_case_metrics(
            gold_set,
            sample,
            system=SYSTEM_CANDIDATE,
            artifact=candidate_artifacts[sample.id],
            violations=candidate_violations[sample.id],
            top_k=top_k,
        )
        baseline_metrics[sample.id] = build_case_metrics(
            gold_set,
            sample,
            system=SYSTEM_BASELINE,
            artifact=baseline_artifacts[sample.id],
            violations=baseline_violations[sample.id],
            top_k=top_k,
        )

    # Bucket reports.
    samples_by_bucket: dict[GoldBucket, list[Any]] = defaultdict(list)
    for sample in gold_set.samples:
        samples_by_bucket[sample.bucket].append(sample)

    buckets: list[BucketReport] = []
    for bucket in GOLD_BUCKETS:
        samples = samples_by_bucket.get(bucket, [])
        cand_cases = [candidate_metrics[s.id] for s in samples]
        base_cases = [baseline_metrics[s.id] for s in samples]
        missing = required_bucket_metrics_complete(
            aggregate_bucket_metrics(cand_cases)
        )
        metrics_block = {
            SYSTEM_CANDIDATE: aggregate_bucket_metrics(cand_cases),
            SYSTEM_BASELINE: aggregate_bucket_metrics(base_cases),
        }
        if missing:
            # Never silently drop a required bucket metric.
            for system in (SYSTEM_CANDIDATE, SYSTEM_BASELINE):
                for name in missing:
                    metrics_block[system][name] = None
        worst_cases = tuple(
            _worst_cases_for_system(
                bucket, SYSTEM_CANDIDATE, candidate_metrics, candidate_violations
            )
            + _worst_cases_for_system(
                bucket, SYSTEM_BASELINE, baseline_metrics, baseline_violations
            )
        )
        bucket_blocked = sorted(
            {
                v.code
                for s in samples
                for v in candidate_violations.get(s.id, [])
            }
        )
        buckets.append(
            BucketReport(
                bucket=bucket,
                metrics=metrics_block,
                worst_cases=worst_cases,
                blocked_reasons=tuple(bucket_blocked),
                sample_count=len(samples),
            )
        )

    operations = aggregate_operations(
        list(candidate_metrics.values()) + list(baseline_metrics.values()),
        reuse=reuse,
    )

    # Operations aggregation must not hide an incomplete cost ledger.
    if operations.get("cost_usd_total") is None:
        reasons.append("operations_cost_incomplete")

    # Expose every candidate violation code and baseline lineage code in the
    # report so no failure is hidden behind a single aggregate score.
    report_reasons = list(reasons)
    report_reasons.extend(candidate_violation_codes)
    report_reasons.extend(baseline_lineage_codes)

    verdict = VERDICT_BLOCKED if report_reasons else VERDICT_QUALIFIED
    return build_report(
        header=hdr,
        buckets=buckets,
        operations=OperationsReport(**operations),
        manifest=manifest_snapshot,
        blocked_reasons=report_reasons,
        verdict=verdict,
    )


def runner_has_promotion_capability() -> bool:
    return False


def runner_has_provider_capability() -> bool:
    return False
