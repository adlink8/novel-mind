"""Offline fixture evaluation for the knowledge graph pipeline.

The evaluator keeps the Phase 04 boundary explicit:
- fixture candidate packages are deterministic recall signals;
- fixture judgments stand in for LLM semantic output;
- this script applies deterministic schema, evidence, threshold, and review gates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import ValidationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.models.knowledge import RELATION_TYPES_BY_DOMAIN_PROFILE  # noqa: E402
from app.schemas.knowledge import KnowledgeLLMRelationJudgmentOutput  # noqa: E402


FIXTURE_VERSION = "knowledge-graph-eval-fixture.v1"
EVAL_REPORT_VERSION = "knowledge-graph-eval-report.v1"
FAITHFULNESS_PROMPT_VERSION = "knowledge-graph-faithfulness.v1"
LLM_OUTPUT_FIELDS = {
    "candidate_id",
    "relation_type",
    "confidence",
    "evidence_refs",
    "rationale",
    "risk_flags",
    "needs_human_review",
}
TERMINAL_DECISIONS = {"accepted", "rejected", "needs_human_review"}


def resolve_fixture_path(raw_path: str) -> Path:
    """Resolve fixture paths from repo root or backend working directories."""

    requested = Path(raw_path)
    script_backend_dir = Path(__file__).resolve().parents[1]
    repo_root = script_backend_dir.parent
    candidates = [
        requested,
        Path.cwd() / requested,
        script_backend_dir / requested,
        repo_root / requested,
    ]
    parts = requested.parts
    if parts and parts[0] == "backend":
        stripped = Path(*parts[1:])
        candidates.extend(
            [
                script_backend_dir / stripped,
                repo_root / stripped,
            ]
        )

    for path in candidates:
        if path.exists():
            return path.resolve()
    raise FileNotFoundError(f"Fixture not found: {raw_path}")


def load_fixture(path: str | Path) -> dict[str, Any]:
    """Load and minimally validate a fixture dataset."""

    fixture_path = resolve_fixture_path(str(path))
    with fixture_path.open("r", encoding="utf-8") as handle:
        fixture = json.load(handle)

    if fixture.get("fixture_version") != FIXTURE_VERSION:
        raise ValueError(f"Unsupported fixture_version in {fixture_path}")
    domain_profile = fixture.get("domain_profile")
    if domain_profile not in RELATION_TYPES_BY_DOMAIN_PROFILE:
        raise ValueError(f"Unsupported domain_profile: {domain_profile}")
    examples = fixture.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ValueError("Fixture must contain non-empty examples list")
    return fixture


def evaluate_fixture(
    fixture: dict[str, Any],
    *,
    dry_run: bool = True,
    min_confidence: float = 0.75,
    budget_usd: float | None = None,
    faithfulness_mode: str = "off",
) -> dict[str, Any]:
    """Evaluate one fixture through deterministic graph pipeline gates."""

    domain_profile = fixture["domain_profile"]
    ontology_profile = fixture.get("ontology_profile") or f"{domain_profile}.v1"
    allowed_relation_types = set(RELATION_TYPES_BY_DOMAIN_PROFILE[domain_profile])

    example_results = []
    budget_spent = 0.0
    for example in fixture["examples"]:
        usage = _usage(example)
        if budget_usd is not None and budget_spent + usage["cost_usd"] > budget_usd:
            result = _budget_exceeded_result(example)
        else:
            budget_spent += usage["cost_usd"]
            result = evaluate_example(
                example,
                domain_profile=domain_profile,
                allowed_relation_types=allowed_relation_types,
                min_confidence=min_confidence,
            )
        example_results.append(result)

    cost_latency = _cost_latency_summary(fixture["examples"], example_results)
    recall_quality = _recall_signal_quality(example_results)
    accepted_quality = _accepted_graph_fact_quality(example_results)
    judgment_quality = _judgment_quality(example_results)
    faithfulness = _faithfulness_summary(example_results, mode=faithfulness_mode)

    success = (
        recall_quality["candidate_coverage_rate"] == 1.0
        and judgment_quality["route_accuracy"] == 1.0
        and accepted_quality["accepted_precision"] == 1.0
        and judgment_quality["budget_pending_count"] == 0
    )

    return {
        "report_version": EVAL_REPORT_VERSION,
        "fixture_name": fixture.get("name"),
        "domain_profile": domain_profile,
        "ontology_profile": ontology_profile,
        "dry_run": dry_run,
        "status": "completed" if success else "completed_with_findings",
        "success": success,
        "pipeline": {
            "core_steps": [
                "deterministic_candidate_package",
                "llm_semantic_judgment_fixture",
                "deterministic_schema_gate",
                "deterministic_evidence_gate",
                "deterministic_threshold_conflict_gate",
                "accepted_postgresql_judgment_rows",
            ],
            "llm_script_split": {
                "llm_responsibility": "semantic judgment only",
                "script_responsibility": "candidate recall, evidence refs, schema/evidence/threshold gates, reporting",
            },
            "projection_boundary": "accepted judgments are source-of-truth; recall signals are never graph facts",
        },
        "counts": {
            "examples": len(example_results),
            "expected_accepted": sum(
                1 for item in example_results if item["expected_decision"] == "accepted"
            ),
            "expected_rejected": sum(
                1 for item in example_results if item["expected_decision"] == "rejected"
            ),
            "expected_review": sum(
                1
                for item in example_results
                if item["expected_decision"] == "needs_human_review"
            ),
        },
        "recall_signal_quality": recall_quality,
        "judgment_quality": judgment_quality,
        "accepted_graph_fact_quality": accepted_quality,
        "faithfulness": faithfulness,
        "cost_latency": cost_latency,
        "examples": example_results,
    }


def evaluate_example(
    example: dict[str, Any],
    *,
    domain_profile: str,
    allowed_relation_types: set[str],
    min_confidence: float,
) -> dict[str, Any]:
    """Evaluate one labeled example with deterministic gates."""

    evidence_ids = {
        str(item.get("evidence_id"))
        for item in example.get("evidence", [])
        if item.get("evidence_id")
    }
    expected_refs = set(example.get("expected_evidence_refs", []))
    candidate = example.get("candidate") or {}
    judgment = example.get("judgment") or {}

    candidate_refs = set(candidate.get("evidence_refs") or [])
    recall_signals = candidate.get("recall_signals") or {}
    candidate_coverage = bool(expected_refs) and expected_refs.issubset(candidate_refs)
    evidence_bound_candidate = bool(candidate_refs) and candidate_refs.issubset(evidence_ids)

    schema_failures = _schema_failures(
        candidate=candidate,
        judgment=judgment,
        allowed_relation_types=allowed_relation_types,
    )
    evidence_failures = _evidence_failures(
        judgment=judgment,
        candidate_refs=candidate_refs,
        evidence_ids=evidence_ids,
    )

    if schema_failures or evidence_failures:
        predicted = "rejected"
    elif float(judgment.get("confidence", 0.0)) < min_confidence:
        predicted = "needs_human_review"
    elif judgment.get("risk_flags"):
        predicted = "needs_human_review"
    elif bool(judgment.get("needs_human_review")):
        predicted = "needs_human_review"
    else:
        predicted = "accepted"

    usage = _usage(example)
    faithfulness_passed = _deterministic_faithfulness_passed(
        judgment=judgment,
        evidence_ids=evidence_ids,
    )
    route_correct = predicted == example["expected_decision"]
    return {
        "id": example["id"],
        "category": example.get("category"),
        "domain_profile": domain_profile,
        "expected_decision": example["expected_decision"],
        "predicted_decision": predicted,
        "route_correct": route_correct,
        "candidate_coverage": candidate_coverage,
        "evidence_bound_candidate": evidence_bound_candidate,
        "recall_signal_kinds": sorted(recall_signals.keys()),
        "schema_failures": schema_failures,
        "evidence_gate_failures": evidence_failures,
        "review_routed": predicted == "needs_human_review",
        "accepted_graph_fact_correct": predicted == "accepted"
        and example["expected_decision"] == "accepted",
        "false_accepted": predicted == "accepted"
        and example["expected_decision"] != "accepted",
        "faithfulness_passed": faithfulness_passed,
        "rationale_supported_by_evidence": bool(
            judgment.get("rationale_supported_by_evidence", False)
        ),
        "llm_usage": usage,
    }


def _schema_failures(
    *,
    candidate: dict[str, Any],
    judgment: dict[str, Any],
    allowed_relation_types: set[str],
) -> list[str]:
    failures: list[str] = []
    payload = {key: judgment[key] for key in LLM_OUTPUT_FIELDS if key in judgment}
    try:
        output = KnowledgeLLMRelationJudgmentOutput.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        return [f"schema:{type(exc).__name__}"]

    if output.candidate_id != int(candidate.get("candidate_id", -1)):
        failures.append("candidate_id_mismatch")
    if output.relation_type not in allowed_relation_types:
        failures.append(f"relation_type_not_allowed:{output.relation_type}")
    return failures


def _evidence_failures(
    *,
    judgment: dict[str, Any],
    candidate_refs: set[str],
    evidence_ids: set[str],
) -> list[str]:
    refs = {str(ref) for ref in judgment.get("evidence_refs") or []}
    if not refs:
        return ["missing_evidence_refs"]
    out_of_package = sorted(refs - candidate_refs)
    if out_of_package:
        return [f"out_of_package_evidence:{','.join(out_of_package)}"]
    missing = sorted(refs - evidence_ids)
    if missing:
        return [f"missing_evidence:{','.join(missing)}"]
    return []


def _deterministic_faithfulness_passed(
    *,
    judgment: dict[str, Any],
    evidence_ids: set[str],
) -> bool:
    refs = {str(ref) for ref in judgment.get("evidence_refs") or []}
    return (
        bool(judgment.get("rationale"))
        and refs.issubset(evidence_ids)
        and bool(judgment.get("rationale_supported_by_evidence", False))
    )


def _recall_signal_quality(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    covered = [item for item in results if item["candidate_coverage"]]
    evidence_bound = [item for item in results if item["evidence_bound_candidate"]]
    signal_kinds = sorted(
        {
            signal
            for item in results
            for signal in item.get("recall_signal_kinds", [])
        }
    )
    return {
        "candidate_coverage_count": len(covered),
        "candidate_coverage_rate": _rate(len(covered), total),
        "evidence_bound_candidate_count": len(evidence_bound),
        "evidence_bound_candidate_rate": _rate(len(evidence_bound), total),
        "signal_kinds_observed": signal_kinds,
        "note": "Recall signals measure package coverage only; they are not accepted graph facts.",
    }


def _judgment_quality(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    route_correct = [item for item in results if item["route_correct"]]
    review_expected = [
        item for item in results if item["expected_decision"] == "needs_human_review"
    ]
    review_correct = [
        item
        for item in review_expected
        if item["predicted_decision"] == "needs_human_review"
    ]
    return {
        "route_accuracy": _rate(len(route_correct), total),
        "schema_failure_count": sum(1 for item in results if item["schema_failures"]),
        "evidence_gate_failure_count": sum(
            1 for item in results if item["evidence_gate_failures"]
        ),
        "review_routing_accuracy": _rate(len(review_correct), len(review_expected)),
        "budget_pending_count": sum(
            1 for item in results if item["predicted_decision"] == "pending_budget_exceeded"
        ),
        "incorrect_routes": [
            {
                "id": item["id"],
                "expected": item["expected_decision"],
                "predicted": item["predicted_decision"],
            }
            for item in results
            if not item["route_correct"]
        ],
    }


def _accepted_graph_fact_quality(results: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [item for item in results if item["predicted_decision"] == "accepted"]
    correct = [item for item in accepted if item["accepted_graph_fact_correct"]]
    expected_accepted = [
        item for item in results if item["expected_decision"] == "accepted"
    ]
    return {
        "accepted_count": len(accepted),
        "accepted_precision": _rate(len(correct), len(accepted)),
        "accepted_recall_on_labeled_set": _rate(len(correct), len(expected_accepted)),
        "false_accepted": [
            {
                "id": item["id"],
                "expected": item["expected_decision"],
                "predicted": item["predicted_decision"],
            }
            for item in results
            if item["false_accepted"]
        ],
        "note": "Accepted graph fact quality is computed only after deterministic gates pass.",
    }


def _faithfulness_summary(results: list[dict[str, Any]], *, mode: str) -> dict[str, Any]:
    deterministic_failures = [
        item["id"]
        for item in results
        if item["predicted_decision"] == "accepted"
        and not item["faithfulness_passed"]
    ]
    optional = {
        "mode": mode,
        "prompt_version": FAITHFULNESS_PROMPT_VERSION,
        "status": "skipped",
        "disagreement_cases": [],
    }
    if mode == "blocked":
        optional["status"] = "blocked"
        optional["reason"] = "simulated_llm_unavailable"
    elif mode == "live":
        optional.update(_live_llm_faithfulness_status())
    return {
        "deterministic_citation_support_rate": _rate(
            sum(1 for item in results if item["faithfulness_passed"]),
            len(results),
        ),
        "accepted_unsupported_rationale_failures": deterministic_failures,
        "optional_llm_check": optional,
    }


def _live_llm_faithfulness_status() -> dict[str, Any]:
    if os.getenv("NOVELMIND_ENABLE_LIVE_KG_FAITHFULNESS") != "1":
        return {
            "status": "blocked",
            "reason": "set NOVELMIND_ENABLE_LIVE_KG_FAITHFULNESS=1 and configure a live model to enable",
            "disagreement_cases": [],
        }
    return {
        "status": "blocked",
        "reason": "live faithfulness judge is intentionally optional and not required for deterministic fixtures",
        "disagreement_cases": [],
    }


def _cost_latency_summary(
    examples: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    usages = [_usage(example) for example in examples]
    latencies = [usage["latency_ms"] for usage in usages]
    return {
        "llm_calls": len(usages),
        "prompt_tokens": sum(usage["prompt_tokens"] for usage in usages),
        "completion_tokens": sum(usage["completion_tokens"] for usage in usages),
        "total_cost_usd": round(sum(usage["cost_usd"] for usage in usages), 6),
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "budget_pending_count": sum(
            1 for item in results if item["predicted_decision"] == "pending_budget_exceeded"
        ),
        "cost_source": "fixture mock or local model estimate",
    }


def _usage(example: dict[str, Any]) -> dict[str, float | int]:
    raw = example.get("llm_usage") or {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "cost_usd": float(raw.get("cost_usd") or 0.0),
        "latency_ms": float(raw.get("latency_ms") or 0.0),
    }


def _budget_exceeded_result(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": example["id"],
        "category": example.get("category"),
        "domain_profile": None,
        "expected_decision": example["expected_decision"],
        "predicted_decision": "pending_budget_exceeded",
        "route_correct": False,
        "candidate_coverage": False,
        "evidence_bound_candidate": False,
        "recall_signal_kinds": [],
        "schema_failures": [],
        "evidence_gate_failures": [],
        "review_routed": False,
        "accepted_graph_fact_correct": False,
        "false_accepted": False,
        "faithfulness_passed": False,
        "rationale_supported_by_evidence": False,
        "llm_usage": _usage(example),
    }


def _rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    if percentile == 50:
        return round(float(median(sorted_values)), 3)
    index = min(len(sorted_values) - 1, round((percentile / 100) * (len(sorted_values) - 1)))
    return round(float(sorted_values[index]), 3)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, help="Path to a knowledge graph eval fixture JSON file.")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate without writing any database rows.")
    parser.add_argument("--min-confidence", type=float, default=0.75)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument(
        "--faithfulness-mode",
        choices=("off", "blocked", "live"),
        default="off",
        help="Optional faithfulness judge mode; live never blocks deterministic tests.",
    )
    parser.add_argument(
        "--output",
        choices=("json", "pretty"),
        default="json",
        help="Report format.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        fixture = load_fixture(args.fixture)
        report = evaluate_fixture(
            fixture,
            dry_run=args.dry_run,
            min_confidence=args.min_confidence,
            budget_usd=args.budget_usd,
            faithfulness_mode=args.faithfulness_mode,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    if args.output == "pretty":
        print(_pretty_report(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["success"] else 2


def _pretty_report(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Fixture: {report['fixture_name']} ({report['domain_profile']})",
            f"Status: {report['status']}",
            f"Examples: {report['counts']['examples']}",
            f"Candidate coverage: {report['recall_signal_quality']['candidate_coverage_rate']:.2%}",
            f"Route accuracy: {report['judgment_quality']['route_accuracy']:.2%}",
            f"Accepted precision: {report['accepted_graph_fact_quality']['accepted_precision']:.2%}",
            f"Cost USD: {report['cost_latency']['total_cost_usd']:.6f}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
