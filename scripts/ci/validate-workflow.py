#!/usr/bin/env python3
"""Validate NovelMind unified CI producer DAG (Phase 06-06).

Checks event matrix, fork safety, job timeouts (D-16), concurrency,
artifact retention (D-17), alert isolation (D-18), and disabled legacy
workflows. Fail-closed: any violation raises WorkflowPolicyError.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required: pip install pyyaml") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEFAULT_POLICY = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"
LEGACY_WORKFLOWS = (
    "backend-ci.yml",
    "frontend-ci.yml",
    "full-ci.yml",
)

LOCKED_JOB_TIMEOUTS = {
    "static": 5,
    "unit": 10,
    "integration": 15,
    "browser": 15,
    "live": 45,
    "nightly-preflight": 5,
    "nightly": 60,
    "nightly-finalize": 5,
}

LOCKED_RETENTION = {
    "pr_junit_coverage_openapi": 14,
    "playwright_failure": 7,
    "main_integration_logs": 30,
    "nightly_signed_reports_baselines": 180,
}

SECRET_JOBS = ("live", "nightly", "nightly-finalize", "promote-baseline", "alert")
SELF_HOSTED_JOBS = ("nightly",)
WRITE_ISSUE_JOBS = ("alert",)


class WorkflowPolicyError(Exception):
    """Fail-closed CI workflow policy violation."""


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkflowPolicyError(f"Missing file: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise WorkflowPolicyError(f"YAML must be a mapping: {path}")
    return data


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    policy = load_yaml(path)
    if policy.get("schema_version") != "baseline-policy.v1":
        raise WorkflowPolicyError("baseline-policy schema_version must be baseline-policy.v1")
    return policy


def _jobs(wf: dict[str, Any]) -> dict[str, Any]:
    jobs = wf.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise WorkflowPolicyError("workflow has no jobs")
    return jobs


def _on(wf: dict[str, Any]) -> dict[str, Any]:
    on = wf.get("on") or wf.get(True)  # YAML may parse 'on' as True
    if on is True:
        # PyYAML 5.1+ can parse unquoted on: as boolean True key
        on = wf.get(True)
    if not isinstance(on, dict):
        raise WorkflowPolicyError("workflow 'on' must be a mapping")
    return on


def assert_no_pull_request_target(wf: dict[str, Any]) -> None:
    on = _on(wf)
    if "pull_request_target" in on:
        raise WorkflowPolicyError("pull_request_target is forbidden (fork secret risk)")


def assert_concurrency(wf: dict[str, Any], policy: dict[str, Any]) -> None:
    conc = wf.get("concurrency")
    if not isinstance(conc, dict):
        raise WorkflowPolicyError("concurrency block required")
    group = str(conc.get("group") or "")
    if "github.workflow" not in group or "github.ref" not in group:
        raise WorkflowPolicyError("concurrency.group must key by workflow and ref")
    cancel = conc.get("cancel-in-progress")
    # Must be expression that is true only for PR (not schedule)
    cancel_s = str(cancel)
    if "pull_request" not in cancel_s:
        raise WorkflowPolicyError(
            "cancel-in-progress must be PR-only expression (nightly must not cancel mid-run)"
        )
    sec = policy.get("security") or {}
    if sec.get("schedule_cancel_in_progress") is not False:
        raise WorkflowPolicyError("policy.security.schedule_cancel_in_progress must be false")


def assert_default_permissions(wf: dict[str, Any]) -> None:
    perms = wf.get("permissions")
    if not isinstance(perms, dict):
        raise WorkflowPolicyError("top-level permissions required")
    if perms.get("contents") != "read":
        raise WorkflowPolicyError("default contents permission must be read")
    # Must not grant issues:write at top level
    if perms.get("issues") in ("write", "write-all", True):
        raise WorkflowPolicyError("top-level issues:write forbidden (alert job only)")


def assert_job_timeouts(wf: dict[str, Any], policy: dict[str, Any] | None = None) -> None:
    expected = dict(LOCKED_JOB_TIMEOUTS)
    if policy:
        expected.update(policy.get("job_timeouts_minutes") or {})
    jobs = _jobs(wf)
    for name, minutes in expected.items():
        if name not in jobs:
            raise WorkflowPolicyError(f"missing required job: {name}")
        job = jobs[name]
        tm = job.get("timeout-minutes")
        if tm != minutes:
            raise WorkflowPolicyError(
                f"job {name} timeout-minutes must be {minutes}, got {tm}"
            )


def assert_secret_jobs_not_on_pr(wf: dict[str, Any]) -> None:
    """Secret / self-hosted / write jobs must gate on allow_* outputs or non-PR if."""
    jobs = _jobs(wf)
    for name in SECRET_JOBS:
        job = jobs.get(name)
        if not isinstance(job, dict):
            raise WorkflowPolicyError(f"missing secret-sensitive job: {name}")
        if_expr = str(job.get("if") or "")
        # Must not run on bare PR — require allow_secrets / allow_nightly / allow_alert
        # or explicit event_name != pull_request
        ok_markers = (
            "allow_secrets",
            "allow_nightly",
            "allow_alert",
            "event_name != 'pull_request'",
            'event_name != "pull_request"',
        )
        if not any(m in if_expr for m in ok_markers):
            raise WorkflowPolicyError(
                f"job {name} must gate secrets/write behind allow_* or non-PR if: got {if_expr!r}"
            )
        # Must not use pull_request_target in job
        if "pull_request_target" in if_expr:
            raise WorkflowPolicyError(f"job {name} references pull_request_target")


def assert_self_hosted_isolation(wf: dict[str, Any]) -> None:
    jobs = _jobs(wf)
    for name in SELF_HOSTED_JOBS:
        job = jobs[name]
        runs_on = job.get("runs-on")
        runs_s = str(runs_on).lower()
        if "self-hosted" not in runs_s:
            raise WorkflowPolicyError(f"job {name} must use self-hosted runners")
        if_expr = str(job.get("if") or "")
        if "allow_nightly" not in if_expr:
            raise WorkflowPolicyError(f"job {name} must gate on allow_nightly")


def assert_alert_isolation(wf: dict[str, Any], policy: dict[str, Any]) -> None:
    jobs = _jobs(wf)
    alert = jobs.get("alert")
    if not isinstance(alert, dict):
        raise WorkflowPolicyError("missing alert job")
    perms = alert.get("permissions") or {}
    if perms.get("contents") != "read":
        raise WorkflowPolicyError("alert permissions.contents must be read")
    if perms.get("issues") != "write":
        raise WorkflowPolicyError("alert permissions.issues must be write")
    # Only those two
    allowed = {"contents", "issues"}
    extra = set(perms) - allowed
    if extra:
        raise WorkflowPolicyError(f"alert has extra permissions: {extra}")

    env = alert.get("environment")
    expected_env = (policy.get("alerts") or {}).get("environment", "quality-alerts")
    if env != expected_env:
        raise WorkflowPolicyError(f"alert environment must be {expected_env}, got {env}")

    if_expr = str(alert.get("if") or "")
    if "allow_alert" not in if_expr:
        raise WorkflowPolicyError("alert must gate on allow_alert")
    if "pull_request" not in if_expr:
        raise WorkflowPolicyError("alert if must explicitly exclude pull_request")

    # Must not checkout untrusted PR code — no checkout of github.head_ref / PR head
    steps = alert.get("steps") or []
    step_blob = yaml.dump(steps)
    if "pull_request" in step_blob and "head" in step_blob and "checkout" in step_blob.lower():
        # soft: forbid checkout of PR head
        for step in steps:
            uses = str(step.get("uses") or "")
            if "checkout" in uses:
                ref = str((step.get("with") or {}).get("ref") or "")
                if "head" in ref or "pull_request" in ref:
                    raise WorkflowPolicyError("alert must not checkout PR head code")
    # Prefer: no actions/checkout at all on alert
    for step in steps:
        uses = str(step.get("uses") or "")
        if uses.startswith("actions/checkout"):
            raise WorkflowPolicyError(
                "alert must not actions/checkout (D-18: consume validated reports only)"
            )


def assert_artifact_retention_in_workflow(wf: dict[str, Any]) -> dict[str, list[int]]:
    """Scan upload-artifact retention-days values; return kind -> days seen."""
    jobs = _jobs(wf)
    found: dict[str, list[int]] = {
        "junit_coverage_openapi": [],
        "playwright_failure": [],
        "integration": [],
        "nightly": [],
    }
    for job_name, job in jobs.items():
        for step in job.get("steps") or []:
            uses = str(step.get("uses") or "")
            if "upload-artifact" not in uses:
                continue
            with_ = step.get("with") or {}
            name = str(with_.get("name") or "")
            retention = with_.get("retention-days")
            # retention may be expression; extract numeric literals
            days_list: list[int] = []
            if isinstance(retention, int):
                days_list = [retention]
            else:
                days_list = [int(x) for x in re.findall(r"\b(\d+)\b", str(retention))]
            if not days_list:
                raise WorkflowPolicyError(
                    f"upload-artifact {name} in {job_name} missing retention-days"
                )
            lower = name.lower()
            if "playwright" in lower:
                found["playwright_failure"].extend(days_list)
                if 7 not in days_list:
                    raise WorkflowPolicyError(
                        f"playwright artifact retention must include 7d, got {days_list}"
                    )
            elif "nightly" in lower or "baseline" in lower or "promoted" in lower:
                found["nightly"].extend(days_list)
                if 180 not in days_list:
                    raise WorkflowPolicyError(
                        f"nightly/baseline retention must include 180d, got {days_list}"
                    )
            elif "openapi" in lower or "unit" in lower or "junit" in lower or "coverage" in lower:
                found["junit_coverage_openapi"].extend(days_list)
                if 14 not in days_list:
                    raise WorkflowPolicyError(
                        f"PR junit/coverage/openapi retention must include 14d, got {days_list}"
                    )
            elif "integration" in lower:
                found["integration"].extend(days_list)
                # may be expression with 14 and 30
                if not any(d in (14, 30) for d in days_list):
                    raise WorkflowPolicyError(
                        f"integration retention must be 14 or 30, got {days_list}"
                    )
    return found


def assert_codeql_once(wf: dict[str, Any], policy: dict[str, Any]) -> None:
    jobs = _jobs(wf)
    codeql_jobs = [n for n, j in jobs.items() if "codeql" in n.lower()]
    if len(codeql_jobs) != 1:
        raise WorkflowPolicyError(f"exactly one CodeQL job required, found {codeql_jobs}")
    job = jobs[codeql_jobs[0]]
    matrix = ((job.get("strategy") or {}).get("matrix") or {})
    langs = matrix.get("language") or matrix.get("languages") or []
    expected = set((policy.get("security") or {}).get("codeql_languages") or [])
    if expected and set(langs) != expected:
        raise WorkflowPolicyError(f"CodeQL languages must be {expected}, got {set(langs)}")


def assert_dispatch_guard(wf: dict[str, Any]) -> None:
    on = _on(wf)
    dispatch = on.get("workflow_dispatch")
    if not isinstance(dispatch, dict):
        raise WorkflowPolicyError("workflow_dispatch must be configured")
    inputs = dispatch.get("inputs") or {}
    if "benchmark_commit" not in inputs:
        raise WorkflowPolicyError("workflow_dispatch requires benchmark_commit input")
    # Guard job must validate protected ref + SHA
    guard = _jobs(wf).get("guard") or {}
    blob = yaml.dump(guard)
    if "benchmark_commit" not in blob and "benchmark_sha" not in blob:
        raise WorkflowPolicyError("guard must validate benchmark_commit")
    if "refs/heads/main" not in blob:
        raise WorkflowPolicyError("guard must require protected main/master for dispatch")


def assert_fork_cannot_reach_secret_jobs(wf: dict[str, Any]) -> None:
    """Simulate matrix: PR event never sets allow_secrets/nightly/alert true in guard script."""
    guard = _jobs(wf).get("guard") or {}
    blob = yaml.dump(guard)
    # On pull_request path, allow_* must stay false
    if "pull_request" not in blob:
        raise WorkflowPolicyError("guard must handle pull_request")
    # Heuristic: ALLOW_SECRETS=true must not appear unconditionally on PR branch
    # We require that ALLOW_SECRETS=true only appears outside pull_request case
    if re.search(
        r"pull_request\)[\s\S]{0,400}ALLOW_SECRETS=true",
        blob,
    ):
        raise WorkflowPolicyError("guard sets ALLOW_SECRETS=true on pull_request (fork risk)")


def assert_legacy_workflows_disabled(workflows_dir: Path = REPO_ROOT / ".github" / "workflows") -> None:
    for name in LEGACY_WORKFLOWS:
        path = workflows_dir / name
        if not path.is_file():
            # deleted is OK
            continue
        wf = load_yaml(path)
        on = _on(wf)
        # Must not trigger on push or pull_request
        for forbidden in ("push", "pull_request", "pull_request_target", "schedule"):
            if forbidden in on:
                raise WorkflowPolicyError(
                    f"legacy workflow {name} still triggers on {forbidden}; disable it"
                )


def assert_service_lock(path: Path = REPO_ROOT / ".github" / "ci" / "service-lock.json") -> dict[str, Any]:
    import json

    if not path.is_file():
        raise WorkflowPolicyError(f"missing service-lock: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("fail_closed"):
        raise WorkflowPolicyError("service-lock.fail_closed must be true")
    for key in ("postgres", "chroma"):
        svc = data.get(key) or {}
        if not svc.get("digest") or "sha256:" not in str(svc.get("digest")):
            raise WorkflowPolicyError(f"service-lock {key} missing sha256 digest")
    return data


def assert_no_sensitive_artifact_paths(wf: dict[str, Any], policy: dict[str, Any]) -> None:
    forbidden = (policy.get("artifacts") or {}).get("forbidden_globs") or []
    blob = yaml.dump(wf)
    for pattern in forbidden:
        # rough: literal path segments that should never appear as upload paths
        token = pattern.replace("**/", "").replace("/**", "").replace("*", "")
        if not token:
            continue
        # uploads/ as artifact path is forbidden
        if token in ("uploads", "fulltext", "novel-content") and f"path:" in blob:
            for job in _jobs(wf).values():
                for step in job.get("steps") or []:
                    uses = str(step.get("uses") or "")
                    if "upload-artifact" not in uses:
                        continue
                    path_val = str((step.get("with") or {}).get("path") or "")
                    if token in path_val:
                        raise WorkflowPolicyError(
                            f"artifact path must not include sensitive '{token}': {path_val}"
                        )


def validate_workflow(
    ci_path: Path = DEFAULT_CI,
    policy_path: Path = DEFAULT_POLICY,
) -> dict[str, Any]:
    """Run all checks; return summary dict or raise WorkflowPolicyError."""
    policy = load_policy(policy_path)
    wf = load_yaml(ci_path)

    assert_no_pull_request_target(wf)
    assert_default_permissions(wf)
    assert_concurrency(wf, policy)
    assert_job_timeouts(wf, policy)
    assert_secret_jobs_not_on_pr(wf)
    assert_self_hosted_isolation(wf)
    assert_alert_isolation(wf, policy)
    assert_artifact_retention_in_workflow(wf)
    assert_codeql_once(wf, policy)
    assert_dispatch_guard(wf)
    assert_fork_cannot_reach_secret_jobs(wf)
    assert_legacy_workflows_disabled()
    assert_service_lock()
    assert_no_sensitive_artifact_paths(wf, policy)

    # actionlint version lock in env
    env = wf.get("env") or {}
    if str(env.get("ACTIONLINT_VERSION") or "") != "v1.7.12":
        raise WorkflowPolicyError("ACTIONLINT_VERSION env must be v1.7.12")

    return {
        "ok": True,
        "ci": str(ci_path),
        "jobs": sorted(_jobs(wf).keys()),
        "actionlint": env.get("ACTIONLINT_VERSION"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate NovelMind CI workflow policy")
    parser.add_argument("--ci", type=Path, default=DEFAULT_CI)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args(argv)
    try:
        summary = validate_workflow(args.ci, args.policy)
    except WorkflowPolicyError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] workflow policy valid: jobs={summary['jobs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
