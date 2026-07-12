#!/usr/bin/env python3
"""Prepare/commit RAG quality baseline from signed nightly reports (06-06 / D-18).

Only reports with status in {passed, qualified}, valid schema, and valid
HMAC report_signature may be promoted. Blocked/regression/unsigned reports
fail closed. Sensitive fulltext markers are rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required: pip install pyyaml") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"
DEFAULT_SECRET = os.environ.get(
    "RAG_SIGNING_SECRET", "novelmind-rag-fixture-dev-secret"
)

FULLTEXT_MARKERS = (
    "NOVEL_FULLTEXT_BEGIN",
    "<<<NOVEL_BODY>>>",
    "raw_chapter_text",
)


class BaselinePromotionError(Exception):
    """Fail-closed baseline promotion error."""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise BaselinePromotionError(f"policy must be mapping: {path}")
    return data


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise BaselinePromotionError(f"report must be object: {path}")
    return data


def stable_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


_SIG_KEYS = frozenset({"report_signature", "signature", "prepare_signature"})


def sign_report_payload(payload: dict[str, Any], secret: str) -> str:
    body = {k: v for k, v in payload.items() if k not in _SIG_KEYS}
    return hmac.new(
        secret.encode("utf-8"),
        stable_dumps(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_report_signature(report: dict[str, Any], secret: str) -> bool:
    actual = (
        report.get("report_signature")
        or report.get("prepare_signature")
        or report.get("signature")
        or ""
    )
    if not actual:
        return False
    expected = sign_report_payload(report, secret)
    return hmac.compare_digest(str(actual), expected)


def contains_sensitive_fulltext(obj: Any) -> bool:
    raw = stable_dumps(obj) if isinstance(obj, dict) else json.dumps(obj, default=str)
    return any(m in raw for m in FULLTEXT_MARKERS)


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = load_yaml(path)
    if policy.get("schema_version") != "baseline-policy.v1":
        raise BaselinePromotionError("invalid baseline-policy schema_version")
    return policy


def validate_report_for_promotion(
    report: dict[str, Any],
    policy: dict[str, Any],
    *,
    secret: str = DEFAULT_SECRET,
    require_signature: bool = True,
) -> dict[str, Any]:
    """Return normalized metrics snapshot or raise BaselinePromotionError."""
    promo = policy.get("promotion") or {}
    allowed = set(promo.get("allowed_statuses") or ["passed", "qualified"])
    rejected = set(promo.get("rejected_statuses") or [])
    required_schema = promo.get("require_schema_version") or "rag-quality.v1"

    status = report.get("status")
    if status in rejected:
        raise BaselinePromotionError(
            f"status {status!r} is rejected for baseline promotion"
        )
    if status not in allowed:
        raise BaselinePromotionError(
            f"status {status!r} not in allowed {sorted(allowed)}"
        )

    schema = report.get("schema_version")
    if schema != required_schema:
        raise BaselinePromotionError(
            f"schema_version must be {required_schema!r}, got {schema!r}"
        )

    if report.get("quality_comparable") is not True:
        raise BaselinePromotionError("quality_comparable must be true for promotion")

    metrics = report.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise BaselinePromotionError("metrics missing or null — fail closed")

    if require_signature and promo.get("require_report_signature", True):
        if not verify_report_signature(report, secret):
            raise BaselinePromotionError("report_signature invalid or missing")

    if promo.get("forbid_fulltext_in_baseline", True) and contains_sensitive_fulltext(
        report
    ):
        raise BaselinePromotionError("sensitive novel fulltext markers in report")

    repeats = report.get("repeats")
    need = int(promo.get("repeats_required") or 3)
    if repeats is not None and int(repeats) < need:
        raise BaselinePromotionError(f"repeats {repeats} < required {need}")

    # Extract baseline metric keys used by arbiter
    baseline = {
        "context_recall_at_5_mean": metrics.get("context_recall_at_5_mean"),
        "answer_relevance_mean": metrics.get("answer_relevance_mean"),
        "cost_usd_total": metrics.get("cost_usd_total"),
        "answer_faithfulness_95lb": metrics.get("answer_faithfulness_95lb"),
        "context_precision_mean": metrics.get("context_precision_mean"),
        "verdict_consistency": metrics.get("verdict_consistency"),
    }
    if any(v is None for v in baseline.values()):
        # allow partial only if keys exist with 0.0? fail closed on missing
        missing = [k for k, v in baseline.items() if v is None]
        raise BaselinePromotionError(f"baseline metrics incomplete: {missing}")

    return baseline


def prepare(
    report_path: Path,
    policy_path: Path,
    out_path: Path,
    *,
    secret: str = DEFAULT_SECRET,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    report = load_json(report_path)
    baseline_metrics = validate_report_for_promotion(report, policy, secret=secret)

    envelope = {
        "schema_version": "baseline-prepare.v1",
        "status": "prepared",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_status": report.get("status"),
        "policy_hash": report.get("policy_hash"),
        "report_schema_version": report.get("schema_version"),
        "report_signature": report.get("report_signature") or report.get("signature"),
        "metrics": baseline_metrics,
        "source_report_sha256": hashlib.sha256(
            report_path.read_bytes()
        ).hexdigest(),
    }
    envelope["prepare_signature"] = sign_report_payload(envelope, secret)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return envelope


def commit(
    prepared_path: Path,
    policy_path: Path,
    baseline_out: Path,
    *,
    secret: str = DEFAULT_SECRET,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    prepared = load_json(prepared_path)
    if prepared.get("status") != "prepared":
        raise BaselinePromotionError("envelope status must be prepared")
    if prepared.get("schema_version") != "baseline-prepare.v1":
        raise BaselinePromotionError("invalid prepare envelope schema")

    # Verify prepare signature
    if not verify_report_signature(
        {**prepared, "report_signature": prepared.get("prepare_signature")},
        secret,
    ):
        # verify using prepare_signature field
        expected = sign_report_payload(prepared, secret)
        actual = prepared.get("prepare_signature") or ""
        if not actual or not hmac.compare_digest(str(actual), expected):
            raise BaselinePromotionError("prepare_signature invalid")

    if contains_sensitive_fulltext(prepared):
        raise BaselinePromotionError("sensitive fulltext in prepared envelope")

    metrics = prepared.get("metrics")
    if not isinstance(metrics, dict):
        raise BaselinePromotionError("prepared metrics missing")

    # Re-check allowed source status recorded on envelope
    allowed = set((policy.get("promotion") or {}).get("allowed_statuses") or [])
    if prepared.get("source_status") not in allowed:
        raise BaselinePromotionError(
            f"source_status {prepared.get('source_status')!r} not promotable"
        )

    baseline = {
        "schema_version": "rag-baseline.v1",
        "status": "committed",
        "committed_at": datetime.now(timezone.utc).isoformat(),
        "source_status": prepared.get("source_status"),
        "policy_hash": prepared.get("policy_hash"),
        "metrics": metrics,
        "source_report_sha256": prepared.get("source_report_sha256"),
        "prepare_signature": prepared.get("prepare_signature"),
    }
    baseline["signature"] = sign_report_payload(baseline, secret)
    baseline_out.parent.mkdir(parents=True, exist_ok=True)
    baseline_out.write_text(
        json.dumps(baseline, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return baseline


def evaluate_flake_gates(
    *,
    pr_flake_count: int,
    required_failure_rate_30d: float,
    policy: dict[str, Any] | None = None,
) -> None:
    """D-10 flake gates used by policy tests / future ci-gate."""
    policy = policy or load_policy()
    flake = policy.get("flake") or {}
    pr_max = int(flake.get("pr_max", 0))
    rate_max = float(flake.get("required_check_30d_failure_rate_max", 0.001))
    if pr_flake_count > pr_max:
        raise BaselinePromotionError(
            f"PR flake={pr_flake_count} exceeds max {pr_max}"
        )
    if required_failure_rate_30d > rate_max:
        raise BaselinePromotionError(
            f"30d required failure rate {required_failure_rate_30d} > {rate_max}"
        )


def alert_allowed(
    *,
    event_name: str,
    ref: str,
    is_fork: bool,
    policy: dict[str, Any] | None = None,
) -> bool:
    """Whether D-18 alert job may run for this event context."""
    policy = policy or load_policy()
    alerts = policy.get("alerts") or {}
    if is_fork:
        return False
    if event_name == "pull_request":
        return False
    allowed_events = set(alerts.get("allowed_events") or ["schedule", "push"])
    if event_name not in allowed_events and event_name != "workflow_dispatch":
        return False
    if event_name == "push":
        allowed_refs = set(
            alerts.get("allowed_refs") or ["refs/heads/main", "refs/heads/master"]
        )
        if ref not in allowed_refs:
            return False
    return True


def dedupe_alert(
    *,
    fingerprint: str,
    open_issue_titles: list[str],
) -> str | None:
    """Return existing issue title if fingerprint already open (dedup)."""
    for title in open_issue_titles:
        if fingerprint in title:
            return title
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote signed RAG quality baseline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="Validate report and write prepare envelope")
    p_prep.add_argument("--report", type=Path, required=True)
    p_prep.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    p_prep.add_argument("--out", type=Path, required=True)
    p_prep.add_argument("--secret", default=DEFAULT_SECRET)

    p_commit = sub.add_parser("commit", help="Commit prepared baseline")
    p_commit.add_argument("--prepared", type=Path, required=True)
    p_commit.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    p_commit.add_argument("--baseline-out", type=Path, required=True)
    p_commit.add_argument("--secret", default=DEFAULT_SECRET)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "prepare":
            env = prepare(args.report, args.policy, args.out, secret=args.secret)
            print(json.dumps({"ok": True, "status": env["status"]}, sort_keys=True))
            return 0
        if args.cmd == "commit":
            base = commit(
                args.prepared, args.policy, args.baseline_out, secret=args.secret
            )
            print(json.dumps({"ok": True, "status": base["status"]}, sort_keys=True))
            return 0
    except BaselinePromotionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
