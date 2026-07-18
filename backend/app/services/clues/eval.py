"""Frozen fiction clue qualification scoring (lineage-bound, fail-closed).

Separates candidate recall quality from published lifecycle quality.
Critical false active/paid_off, spoiler leaks, cross-scope links and
override overwrites must be exactly zero for quality_comparable reports.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from app.services.clues.evidence import sha256_json
from app.services.clues.gates import policy_hash as runtime_policy_hash

DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[3] / "evals" / "clue_fiction.v1.json"
)

REQUIRED_FIXTURE_KEYS = {
    "fixture_version",
    "dataset_version",
    "domain",
    "partition",
    "source_snapshot_hash",
    "hierarchy_build_id",
    "hierarchy_checksum",
    "prompt_hash",
    "schema_hash",
    "policy_hash",
    "model_lineage",
    "version_lineage",
    "deferred_products_absent",
    "thresholds",
    "cases",
    "adversarial_cases",
    "operational_expectations",
}

HARD_NEGATIVE_PREFIXES = (
    "hard_negative_",
    "hard_negative",
)

PUBLICATION_STATES = frozenset({"active", "reinforced", "paid_off"})
TERMINAL_OK = frozenset({"dismissed", "candidate"})


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_bytes(value: bytes | Any) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(_canonical(value)).hexdigest()


def fixture_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_fixture(path: Path | None = None) -> dict[str, Any]:
    fixture_path = path or DEFAULT_FIXTURE
    corpus = json.loads(fixture_path.read_text(encoding="utf-8"))
    missing = REQUIRED_FIXTURE_KEYS - set(corpus)
    if missing:
        raise ValueError(f"frozen fixture missing keys: {sorted(missing)}")
    if corpus["domain"] != "fiction":
        raise ValueError("qualification corpus must remain fiction-only")
    if corpus.get("partition") not in {"frozen", "dev"}:
        raise ValueError("fixture partition must be frozen or dev")
    cases = corpus["cases"]
    if len(cases) < 24:
        raise ValueError("fixture requires >=24 labeled examples")
    hard = [
        c
        for c in cases
        if str(c.get("category", "")).startswith("hard_negative")
        or c.get("hard_negative")
    ]
    chains = [c for c in cases if c.get("category") == "full_chain"]
    if len(hard) < 8:
        raise ValueError("fixture requires >=8 hard-negative examples")
    if len(chains) < 8:
        raise ValueError("fixture requires >=8 genuine full chains")
    required_hn = {
        "recurring_motif",
        "repeated_object",
        "similar_wording",
        "same_people_location",
        "summary_paraphrase",
        "payoff_before_cue",
        "unsupported_author_intent",
        "chat_only_assertion",
    }
    present_hn = {c.get("hard_negative") for c in hard if c.get("hard_negative")}
    if not required_hn <= present_hn:
        raise ValueError(
            f"missing hard-negative categories: {sorted(required_hn - present_hn)}"
        )
    return corpus


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def _p_quantile(values: Iterable[float | int | None], q: float) -> float:
    measured = sorted(float(v) for v in values if v is not None)
    if not measured:
        return 0.0
    if len(measured) == 1:
        return measured[0]
    rank = max(0, math.ceil(len(measured) * q) - 1)
    return float(measured[min(rank, len(measured) - 1)])


def gold_predictions_from_fixture(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Ideal predictions matching fixture labels (reproducibility baseline)."""

    out: list[dict[str, Any]] = []
    for case in fixture["cases"]:
        out.append(
            {
                "id": case["id"],
                "predicted_state": case["expected_state"],
                "recalled": case["expected_state"] not in {"dismissed"},
                "schema_valid": True,
                "evidence_valid": case["expected_state"]
                not in {"active", "reinforced", "paid_off"}
                or bool(case.get("cue")),
                "critical_false_active": False,
                "critical_false_paid_off": False,
                "spoiler_leak": False,
                "cross_scope_link": False,
                "override_overwrite": False,
                "chat_as_fact": False,
                "latency_ms": 12,
                "tokens": 40,
                "cost_usd": 0.0001,
            }
        )
    return out


def score_predictions(
    fixture: dict[str, Any],
    predictions: list[dict[str, Any]],
    *,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score predicted lifecycle states against frozen labels.

    Separates candidate recall from active/reinforced/paid_off quality.
    """

    controls = controls or {}
    by_id = {str(p["id"]): p for p in predictions}
    cases = fixture["cases"]
    thresholds = fixture["thresholds"]

    # Treat expected active/reinforced/paid_off as recall positives.
    gold_recall = [c for c in cases if c["expected_state"] in PUBLICATION_STATES]
    recalled = 0
    for case in gold_recall:
        pred = by_id.get(case["id"], {})
        if pred.get("recalled") or pred.get("predicted_state") in PUBLICATION_STATES:
            recalled += 1
    candidate_recall = _ratio(recalled, len(gold_recall))

    # Per-state precision/recall for publication states
    state_metrics: dict[str, dict[str, float]] = {}
    for state in ("active", "reinforced", "paid_off"):
        gold_ids = {c["id"] for c in cases if c["expected_state"] == state}
        pred_ids = {
            pid
            for pid, pred in by_id.items()
            if pred.get("predicted_state") == state
        }
        tp = len(gold_ids & pred_ids)
        precision = _ratio(tp, len(pred_ids))
        recall = _ratio(tp, len(gold_ids))
        state_metrics[state] = {
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "support": float(len(gold_ids)),
        }

    active_f1 = state_metrics["active"]["f1"]
    reinforced_f1 = state_metrics["reinforced"]["f1"]
    macro_f1 = round((active_f1 + reinforced_f1) / 2.0, 6)
    paid_off_precision = state_metrics["paid_off"]["precision"]

    # Critical counts (exactly zero required for release)
    critical = {
        "false_active": 0,
        "false_paid_off": 0,
        "spoiler_leak": int(controls.get("spoiler_leaks", 0)),
        "cross_scope_link": int(controls.get("cross_scope_links", 0)),
        "override_overwrite": int(controls.get("override_overwrites", 0)),
        "chat_as_fact": 0,
        "unsupported_acceptance": 0,
    }
    for case in cases:
        pred = by_id.get(case["id"], {})
        predicted = pred.get("predicted_state")
        must_not = set(case.get("must_not_accept") or [])
        if predicted in must_not:
            if predicted == "paid_off":
                critical["false_paid_off"] += 1
            if predicted in {"active", "reinforced"}:
                critical["false_active"] += 1
            critical["unsupported_acceptance"] += 1
        if pred.get("critical_false_active"):
            critical["false_active"] += 1
        if pred.get("critical_false_paid_off"):
            critical["false_paid_off"] += 1
        if pred.get("spoiler_leak"):
            critical["spoiler_leak"] += 1
        if pred.get("cross_scope_link"):
            critical["cross_scope_link"] += 1
        if pred.get("override_overwrite"):
            critical["override_overwrite"] += 1
        if pred.get("chat_as_fact"):
            critical["chat_as_fact"] += 1
        if (
            predicted in PUBLICATION_STATES
            and (
                pred.get("schema_valid") is False
                or pred.get("evidence_valid") is False
            )
        ):
            critical["unsupported_acceptance"] += 1

    latencies = [by_id.get(c["id"], {}).get("latency_ms") for c in cases]
    costs = [float(by_id.get(c["id"], {}).get("cost_usd") or 0) for c in cases]
    tokens = [int(by_id.get(c["id"], {}).get("tokens") or 0) for c in cases]

    schema_valid = all(
        by_id.get(c["id"], {}).get("schema_valid", True) for c in cases
    ) and controls.get("schema_valid", True)
    evidence_valid = all(
        by_id.get(c["id"], {}).get("evidence_valid", True) for c in cases
    ) and controls.get("evidence_valid", True)

    metrics = {
        "candidate_recall": candidate_recall,
        "state_metrics": state_metrics,
        "active_reinforced_macro_f1": macro_f1,
        "paid_off_precision": paid_off_precision,
        "critical": critical,
        "schema_validity": schema_valid,
        "evidence_validity": evidence_valid,
        "calls": len(predictions),
        "tokens_total": sum(tokens),
        "cost_usd_total": round(sum(costs), 8),
        "latency_p50_ms": _p_quantile(latencies, 0.50),
        "latency_p95_ms": _p_quantile(latencies, 0.95),
    }

    gates = {
        "fiction_only": fixture["domain"] == "fiction",
        "fixture_size": len(cases) >= 24,
        "schema_validity": schema_valid is True,
        "evidence_validity": evidence_valid is True,
        "critical_false_active_zero": critical["false_active"] == 0,
        "critical_false_paid_off_zero": critical["false_paid_off"] == 0,
        "critical_spoiler_zero": critical["spoiler_leak"] == 0,
        "critical_cross_scope_zero": critical["cross_scope_link"] == 0,
        "critical_override_overwrite_zero": critical["override_overwrite"] == 0,
        "critical_chat_fact_zero": critical["chat_as_fact"] == 0,
        "paid_off_precision": paid_off_precision
        >= float(thresholds["paid_off_precision_min"]),
        "active_reinforced_macro_f1": macro_f1
        >= float(thresholds["active_reinforced_macro_f1_min"]),
        "policy_hash_match": fixture["policy_hash"] == runtime_policy_hash()
        or controls.get("skip_policy_match") is True,
    }
    qualified = all(gates.values())
    return {
        "metrics": metrics if qualified or controls.get("emit_metrics_always") else (
            metrics if controls.get("always_metrics", True) else None
        ),
        "gates": gates,
        "qualified": qualified,
        "quality_comparable": qualified,
    }


def run_offline_qualification(
    path: Path | None = None,
    *,
    predictions: list[dict[str, Any]] | None = None,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lineage-bound offline report from frozen fixture (+ optional predictions)."""

    fixture_path = path or DEFAULT_FIXTURE
    fixture = load_fixture(fixture_path)
    controls = dict(controls or {})
    preds = predictions if predictions is not None else gold_predictions_from_fixture(fixture)
    scored = score_predictions(fixture, preds, controls=controls)

    lineage = {
        "source_snapshot_hash": fixture["source_snapshot_hash"],
        "hierarchy_build_id": fixture["hierarchy_build_id"],
        "hierarchy_checksum": fixture["hierarchy_checksum"],
        "prompt_hash": fixture["prompt_hash"],
        "schema_hash": fixture["schema_hash"],
        "policy_hash": fixture["policy_hash"],
        "policy_hash_runtime": runtime_policy_hash(),
        "model_lineage": fixture["model_lineage"],
        "version_lineage": fixture["version_lineage"],
        "fixture_sha256": fixture_sha256(fixture_path),
        "fixture_version": fixture["fixture_version"],
    }
    # Reproducibility: same inputs → same digest
    artifact = {
        "lineage": lineage,
        "case_count": len(fixture["cases"]),
        "adversarial_count": len(fixture["adversarial_cases"]),
        "prediction_ids": sorted(str(p["id"]) for p in preds),
        "gates": scored["gates"],
        "metrics": scored["metrics"],
        "deferred_products_absent": fixture["deferred_products_absent"],
    }
    report: dict[str, Any] = {
        "report_version": "clue-offline-qualification.v1",
        "fixture_version": fixture["fixture_version"],
        "domain": fixture["domain"],
        "status": "qualified" if scored["qualified"] else "failed_policy",
        "quality_comparable": scored["quality_comparable"],
        "lineage": lineage,
        "gates": scored["gates"],
        "metrics": scored["metrics"],
        "artifact": artifact,
        "artifact_sha256": sha256_bytes(artifact),
    }
    report["report_sha256"] = sha256_bytes(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )
    return report


def fail_closed_threshold_report(
    *,
    paid_off_precision: float,
    macro_f1: float,
    critical: dict[str, int],
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Boundary helper: any critical >0 or threshold miss fails closed."""

    thresholds = thresholds or {
        "paid_off_precision_min": 0.9,
        "active_reinforced_macro_f1_min": 0.85,
    }
    critical_ok = all(int(v) == 0 for v in critical.values())
    gates = {
        "critical_zero": critical_ok,
        "paid_off_precision": paid_off_precision
        >= float(thresholds["paid_off_precision_min"]),
        "macro_f1": macro_f1 >= float(thresholds["active_reinforced_macro_f1_min"]),
    }
    ok = all(gates.values())
    return {
        "status": "qualified" if ok else "failed_policy",
        "quality_comparable": ok,
        "gates": gates,
    }


# Keep sha256_json available for callers that already import from evidence.
__all__ = [
    "DEFAULT_FIXTURE",
    "fail_closed_threshold_report",
    "fixture_sha256",
    "gold_predictions_from_fixture",
    "load_fixture",
    "run_offline_qualification",
    "score_predictions",
    "sha256_bytes",
    "sha256_json",
]
