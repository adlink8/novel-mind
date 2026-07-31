#!/usr/bin/env python3
"""Create one signed canonical report for every Nightly terminal state.

The provider benchmark may run on an optional self-hosted runner.  This
finalizer runs on the always-available control plane and turns runner absence,
provider failures, and quality results into the same fail-closed report shape.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {
    "passed",
    "qualified",
    "blocked_dependency",
    "failed_policy",
    "quality_regression",
}
PROMOTABLE_STATUSES = {"passed", "qualified"}
SIGNATURE_KEYS = {"report_signature", "signature", "prepare_signature"}


class NightlyReportError(RuntimeError):
    """Raised when control-plane inputs are malformed."""


def stable_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sign_canonical_report(report: dict[str, Any], secret: str) -> str:
    body = {key: value for key, value in report.items() if key not in SIGNATURE_KEYS}
    return hmac.new(
        secret.encode("utf-8"),
        stable_dumps(body).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_provider_report(report: dict[str, Any], secret: str) -> bool:
    """Verify the backend producer's pre-finalization signing contract."""
    actual = str(report.get("report_signature") or "")
    if not actual:
        return False
    unsigned = {
        key: value
        for key, value in report.items()
        if key not in {"report_signature", "output_hash"}
    }
    expected = hmac.new(
        secret.encode("utf-8"),
        stable_dumps(unsigned).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(actual, expected)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NightlyReportError(f"invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise NightlyReportError(f"JSON report must be an object: {path}")
    return value


def _find_provider_report(root: Path | None) -> Path | None:
    if root is None or not root.exists():
        return None
    if root.is_file():
        return root
    matches = sorted(root.rglob("rag-quality-report.json"))
    return matches[0] if matches else None


def finalize_report(
    *,
    authority: dict[str, Any],
    provider_result: str,
    provider_report: dict[str, Any] | None,
    secret: str,
    run_id: str,
    commit_sha: str,
    event_name: str,
) -> dict[str, Any]:
    if not secret:
        raise NightlyReportError("signing secret is required")

    ready = authority.get("provider_ready") is True
    reason = str(authority.get("reason") or "provider_authority_unknown")
    provider_result = provider_result.strip().lower() or "missing"

    if provider_report is not None and verify_provider_report(provider_report, secret):
        report = {
            key: value
            for key, value in provider_report.items()
            if key not in SIGNATURE_KEYS | {"output_hash"}
        }
        status = str(report.get("status") or "failed_policy")
        if status not in TERMINAL_STATUSES:
            status = "failed_policy"
            report["reason"] = "unsupported_provider_status"
            report["metrics"] = None
            report["quality_comparable"] = False
        report["status"] = status
    else:
        if not ready:
            status = "blocked_dependency"
        else:
            status = "failed_policy"
            reason = (
                "provider_report_missing"
                if provider_report is None
                else "provider_report_signature_invalid"
            )
        report = {
            "schema_version": "rag-quality.v1",
            "policy_version": "rag-quality-policy.v1",
            "policy_hash": None,
            "status": status,
            "quality_comparable": False,
            "metrics": None,
            "repeats": 0,
            "reason": reason,
        }

    status = str(report["status"])
    comparable = report.get("quality_comparable") is True
    metrics = report.get("metrics")
    if status in PROMOTABLE_STATUSES and (
        not comparable or not isinstance(metrics, dict) or not metrics
    ):
        report.update(
            status="failed_policy",
            quality_comparable=False,
            metrics=None,
            reason="promotable_status_without_comparable_metrics",
        )
        status = "failed_policy"
    elif status == "blocked_dependency":
        report["quality_comparable"] = False
        report["metrics"] = None

    report["execution_authority"] = authority
    report["run_lineage"] = {
        "run_id": run_id,
        "commit_sha": commit_sha,
        "event_name": event_name,
        "provider_job_result": provider_result,
        "finalized_at": datetime.now(UTC).isoformat(),
    }
    report["promotable"] = (
        report["status"] in PROMOTABLE_STATUSES
        and report.get("quality_comparable") is True
        and isinstance(report.get("metrics"), dict)
        and bool(report["metrics"])
    )
    unsigned_for_hash = {
        key: value
        for key, value in report.items()
        if key not in SIGNATURE_KEYS | {"output_hash"}
    }
    report["output_hash"] = hashlib.sha256(
        stable_dumps(unsigned_for_hash).encode("utf-8")
    ).hexdigest()
    report["report_signature"] = sign_canonical_report(report, secret)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--provider-result", required=True)
    parser.add_argument("--provider-report-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", default=os.getenv("GITHUB_RUN_ID", "local"))
    parser.add_argument("--commit-sha", default=os.getenv("GITHUB_SHA", "local"))
    parser.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME", "local"))
    args = parser.parse_args()

    authority = _read_json(args.authority)
    provider_path = _find_provider_report(args.provider_report_root)
    provider_report = _read_json(provider_path) if provider_path else None
    report = finalize_report(
        authority=authority,
        provider_result=args.provider_result,
        provider_report=provider_report,
        secret=os.getenv("RAG_SIGNING_SECRET", ""),
        run_id=args.run_id,
        commit_sha=args.commit_sha,
        event_name=args.event_name,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"status={report['status']}")
    print(f"promotable={str(report['promotable']).lower()}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
