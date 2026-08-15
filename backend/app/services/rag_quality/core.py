"""Input validation + full quality run orchestration (rag_quality package)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.schemas.eval import (
    INVALID_LINEAGE_REASON,
    LEGACY_INCOMPARABLE_REASON,
    SCHEMA_VERSION_RAG_QUALITY,
    CalibrationReport,
    ChunkerLineage,
    EvalCase,
    ModelLineage,
    SourceSnapshot,
)
from app.services.rag_fixture import (
    DEFAULT_SIGNING_SECRET,
    InvalidLineageError,
    fail_closed,
    sign_payload,
    stable_hash,
    validate_generator_judge_isolation,
    verify_frozen_case,
    verify_source_snapshot,
)

from .arbiter import apply_policy_arbiter
from .lineage import build_quality_input_hash, canonicalize_chunker_lineage
from .metrics import _retrieved_hashes
from .policy import load_policy, policy_hash
from .scoring import CaseRunArtifact, aggregate_run_metrics, run_case_once
from .types import AnswerFn, AnswerJudgeFn, RetrieveFn

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input validation (consume 06-03 artifacts)
# ---------------------------------------------------------------------------


def validate_fixtures_for_scoring(
    *,
    snapshot: SourceSnapshot,
    cases: list[EvalCase],
    secret: str = DEFAULT_SIGNING_SECRET,
) -> dict[str, Any] | None:
    """Return fail-closed dict if fixtures invalid; else None."""
    if not verify_source_snapshot(snapshot, secret):
        return fail_closed("invalid_fixture", "snapshot signature invalid").model_dump()
    for case in cases:
        if case.status == "quarantined":
            return fail_closed(
                "quarantined", f"case {case.case_id} is quarantined"
            ).model_dump()
        if case.status != "frozen":
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} status={case.status} not frozen",
            ).model_dump()
        if not verify_frozen_case(case, secret):
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} fixture signature invalid",
            ).model_dump()
        if case.snapshot_hash != snapshot.manifest_hash:
            return fail_closed(
                "invalid_fixture",
                f"case {case.case_id} snapshot_hash mismatch",
            ).model_dump()
        # Qualification rejects DB-id-only truth
        if case.gold_chunk_db_ids and not case.equivalent_evidence_sets:
            if case.case_type != "no_answer":
                return fail_closed(
                    "invalid_fixture",
                    f"case {case.case_id} has only gold_chunk_db_ids without hash evidence",
                ).model_dump()
    return None


def validate_calibrated_lineage(
    *,
    generator_lineage: ModelLineage | None,
    judge_lineage: ModelLineage | None,
    calibration_report: CalibrationReport | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Ensure G/J isolation and calibration-passed Judge lineage."""
    if generator_lineage is None or judge_lineage is None:
        return fail_closed(
            "invalid_lineage", "missing generator or judge lineage"
        ).model_dump()
    try:
        validate_generator_judge_isolation(generator_lineage, judge_lineage)
    except InvalidLineageError as exc:
        return fail_closed("invalid_lineage", str(exc)).model_dump()

    if calibration_report is None:
        return fail_closed(
            "invalid_lineage", "missing calibration report for Judge"
        ).model_dump()

    if isinstance(calibration_report, CalibrationReport):
        status = calibration_report.status
        cal_rev = calibration_report.judge_lineage.weights_revision
        metrics = calibration_report.metrics
        cfa = calibration_report.critical_false_accept
        consistency = calibration_report.consistency
    else:
        status = calibration_report.get("status")
        jl = calibration_report.get("judge_lineage") or {}
        cal_rev = jl.get("weights/revision") or jl.get("weights_revision")
        metrics = calibration_report.get("metrics")
        cfa = calibration_report.get("critical_false_accept", 0)
        consistency = float(calibration_report.get("consistency") or 0.0)

    if status != "passed":
        return fail_closed(
            "invalid_lineage",
            f"calibration status={status} not passed",
        ).model_dump()
    if metrics is None and status == "passed":
        # 06-03 sets metrics only when passed; if missing treat as incomplete
        pass
    if cfa != 0:
        return fail_closed(
            "invalid_lineage", "calibration critical_false_accept != 0"
        ).model_dump()
    if consistency < 0.80:
        return fail_closed(
            "invalid_lineage", "calibration consistency < 0.80"
        ).model_dump()
    if not cal_rev or cal_rev != judge_lineage.weights_revision:
        return fail_closed(
            "invalid_lineage",
            "Judge weights/revision does not match calibrated report",
        ).model_dump()
    return None


def validate_dependency_health(health: dict[str, Any] | None) -> dict[str, Any] | None:
    if health is None:
        return fail_closed(
            "blocked_dependency", "missing dependency health"
        ).model_dump()
    if health.get("ok") is not True:
        return fail_closed(
            "blocked_dependency",
            health.get("reason") or "dependency health not ok",
            detail=health,
        ).model_dump()
    return None


# ---------------------------------------------------------------------------
# Full quality run orchestration (synchronous scoring path)
# ---------------------------------------------------------------------------


def run_quality_evaluation(
    *,
    snapshot: SourceSnapshot,
    cases: list[EvalCase],
    generator_lineage: ModelLineage | None = None,
    judge_lineage: ModelLineage | None = None,
    calibration_report: CalibrationReport | dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    health: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    policy_file: str | Path | None = None,
    secret: str = DEFAULT_SIGNING_SECRET,
    retrieve_fn: RetrieveFn | None = None,
    answer_fn: AnswerFn | None = None,
    judge_fn: AnswerJudgeFn | None = None,
    stage_cache: dict[str, Any] | None = None,
    repeats: int | None = None,
    top_k: int | None = None,
    chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
    require_chunker_lineage: bool = False,
    run_input_hash: str | None = None,
) -> dict[str, Any]:
    """Run SUT scoring + deterministic arbiter. Never swallows exceptions into 0 scores."""

    # Policy
    loaded_policy: dict[str, Any] | None
    try:
        loaded_policy = policy if policy is not None else load_policy(policy_file)
    except (OSError, ValueError, RuntimeError) as exc:
        return {
            "status": "failed_policy",
            "metrics": None,
            "quality_comparable": False,
            "reason": f"policy load failed: {exc}",
            "detail": {},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
        }

    run_cfg = loaded_policy.get("run") or {}
    n_repeats = int(repeats if repeats is not None else run_cfg.get("repeats", 3))
    k = int(top_k if top_k is not None else run_cfg.get("top_k", 5))
    p_hash = policy_hash(loaded_policy)

    # Chunker/source five-tuple lineage (before scoring when required)
    canonical_lineage, lineage_reason = canonicalize_chunker_lineage(
        chunker_lineage,
        expected_source_snapshot_hash=snapshot.manifest_hash,
    )
    if require_chunker_lineage and (
        canonical_lineage is None
        or (lineage_reason and lineage_reason.startswith(INVALID_LINEAGE_REASON))
        or lineage_reason == LEGACY_INCOMPARABLE_REASON
    ):
        reason = lineage_reason or INVALID_LINEAGE_REASON
        if reason == LEGACY_INCOMPARABLE_REASON:
            reason = f"{INVALID_LINEAGE_REASON}: missing chunker/source lineage"
        return {
            "status": "invalid_lineage",
            "metrics": None,
            "quality_comparable": False,
            "reason": reason,
            "detail": {"incomparable_reason": reason},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
            "incomparable_reason": reason,
        }
    if lineage_reason and lineage_reason.startswith(INVALID_LINEAGE_REASON):
        return {
            "status": "invalid_lineage",
            "metrics": None,
            "quality_comparable": False,
            "reason": lineage_reason,
            "detail": {"incomparable_reason": lineage_reason},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": None,
            "incomparable_reason": lineage_reason,
        }

    five = canonical_lineage.five_tuple() if canonical_lineage else None
    effective_input_hash = run_input_hash or build_quality_input_hash(
        snapshot_manifest_hash=snapshot.manifest_hash,
        case_fixture_hashes=[c.fixture_hash for c in cases],
        baseline=baseline,
        policy_hash_value=p_hash,
        chunker_lineage=canonical_lineage,
    )

    # Fixtures
    fixture_fail = validate_fixtures_for_scoring(
        snapshot=snapshot, cases=cases, secret=secret
    )
    if fixture_fail is not None:
        return {
            **fixture_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    # Infer lineages from first case if not provided
    g_lin = generator_lineage or (cases[0].generator_lineage if cases else None)
    j_lin = judge_lineage or (cases[0].judge_lineage if cases else None)

    lineage_fail = validate_calibrated_lineage(
        generator_lineage=g_lin,
        judge_lineage=j_lin,
        calibration_report=calibration_report,
    )
    if lineage_fail is not None:
        return {
            **lineage_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    health_fail = validate_dependency_health(health)
    if health_fail is not None:
        return {
            **health_fail,
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    cache = stage_cache if stage_cache is not None else {}
    artifacts: list[CaseRunArtifact] = []
    blocked = False
    blocked_reason = None

    try:
        for case in cases:
            for rep in range(n_repeats):
                art = run_case_once(
                    case,
                    snapshot,
                    repetition=rep,
                    top_k=k,
                    retrieve_fn=retrieve_fn,
                    answer_fn=answer_fn,
                    judge_fn=judge_fn,
                    judge_lineage=j_lin,
                    stage_cache=cache,
                    run_input_hash=effective_input_hash,
                    chunker_lineage=canonical_lineage,
                )
                if art.status == "blocked_dependency":
                    blocked = True
                    blocked_reason = "dependency outage during SUT run"
                    artifacts.append(art)
                    break
                artifacts.append(art)
            if blocked:
                break
    except Exception as exc:
        # Never convert exceptions into zero scores for the quality path
        logger.exception("quality evaluation failed")
        return {
            "status": "failed_policy",
            "metrics": None,
            "quality_comparable": False,
            "reason": f"unhandled exception: {type(exc).__name__}: {exc}",
            "detail": {},
            "usable_for_baseline": False,
            "artifacts": [],
            "report_signature": None,
            "output_hash": None,
            "chunker_lineage": five,
            "input_hash": effective_input_hash,
        }

    metrics = (
        None if blocked else aggregate_run_metrics(artifacts, policy=loaded_policy)
    )
    decision = apply_policy_arbiter(
        metrics=metrics,
        policy=loaded_policy,
        baseline=baseline,
        health=health,
        lineage_ok=True,
        fixture_ok=True,
        blocked=blocked,
        blocked_reason=blocked_reason,
    )

    report = {
        **decision,
        "policy_version": loaded_policy.get("version"),
        "policy_hash": p_hash,
        "schema_version": SCHEMA_VERSION_RAG_QUALITY,
        "n_cases": len(cases),
        "repeats": n_repeats,
        "input_hash": effective_input_hash,
        # Canonical lineage enters the signed report before signature/output_hash.
        "chunker_lineage": five,
        "artifacts": [
            {
                "case_id": a.case_id,
                "repetition": a.repetition,
                "status": a.status,
                "call_id": a.call_id,
                "cost_usd": a.cost_usd,
                "latency_ms": a.latency_ms,
                "deterministic_metrics": a.deterministic_metrics,
                "judge_scores": a.judge_scores,
                "answer": a.answer,
                "retrieved_hashes": _retrieved_hashes(a.retrieved, k),
            }
            for a in artifacts
        ],
        "sut_family_disclosure": {
            "note": "SUT may share family with G or J; disclosed for audit only",
        },
    }
    # Sign complete unsigned report (lineage included); then bind output_hash.
    unsigned = {
        k: v for k, v in report.items() if k not in ("report_signature", "output_hash")
    }
    report["report_signature"] = sign_payload(unsigned, secret)
    report["output_hash"] = stable_hash(unsigned)
    return report


def make_baseline_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Non-promotable metrics shape helper for tests / arbiter inputs.

    Does NOT authorize baseline promotion — only prepare/commit against a
    durable QualityRun with complete comparable lineage can promote.
    """
    return {
        "context_recall_at_5_mean": metrics.get("context_recall_at_5_mean", 0.0),
        "answer_relevance_mean": metrics.get("answer_relevance_mean", 0.0),
        "cost_usd_total": metrics.get("cost_usd_total", 0.0),
        "answer_faithfulness_95lb": metrics.get("answer_faithfulness_95lb", 0.0),
        "context_precision_mean": metrics.get("context_precision_mean", 0.0),
    }
