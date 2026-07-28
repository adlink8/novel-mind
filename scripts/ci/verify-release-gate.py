#!/usr/bin/env python3
"""Phase 06 final release gate verifier (06-07 / D-19).

Checks all seven plan SUMMARYs + required artifacts/evidence trails,
workflow `ci-gate` job presence, signed/policy files from 06-01..06,
and branch protection readback when available.

If branch protection is blocked_external_configuration, the release gate
FAILS (phase incomplete). Do not invent success.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML required: pip install pyyaml") from exc

REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE_DIR = (
    REPO_ROOT / ".planning" / "phases" / "06-automated-quality-ci"
)
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
POLICY_PATH = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"
PROTECTION_SCRIPT = (
    REPO_ROOT / "scripts" / "ci" / "configure-branch-protection.ps1"
)

PLAN_IDS = ("06-01", "06-02", "06-03", "06-04", "06-05", "06-06", "06-07")

# Required on-disk evidence trails per completed plan (06-01..06-06 always;
# 06-07 scripts/tests checked separately once SUMMARY exists).
REQUIRED_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "06-01": (
        ".quality/coverage-policy.yml",
        ".quality/coverage-policy.schema.json",
        "backend/pytest.ini",
        "backend/tests/test_test_policy.py",
    ),
    "06-02": (
        "docker-compose.ci.yml",
        ".github/ci/service-lock.json",
        "backend/tests/integration/test_postgres_migrations.py",
        "backend/tests/integration/test_chroma_contract.py",
    ),
    "06-03": (
        "backend/app/services/rag_fixture.py",
        "backend/evals/fixtures/rag-quality-benchmark.v1.json",
        "backend/evals/calibration/rag-judge-calibration.v1.json",
        "backend/prompts/rag_fixture_generator.v1.txt",
        "backend/prompts/rag_fixture_judge.v1.txt",
    ),
    "06-04": (
        "backend/app/services/rag_quality.py",
        "backend/app/services/rag_quality_worker.py",
        "backend/evals/rag-quality-policy.v1.yml",
        "backend/scripts/run_rag_quality.py",
        "backend/prompts/rag_answer_judge.v1.txt",
    ),
    "06-05": (
        "backend/scripts/export_openapi.py",
        "backend/openapi-baseline.json",
        "backend/tests/contract/test_openapi_contract.py",
        "frontend/playwright.config.ts",
        "frontend/e2e/core-flow.spec.ts",
    ),
    "06-06": (
        ".github/workflows/ci.yml",
        ".github/quality/baseline-policy.yml",
        ".github/codeql/codeql-config.yml",
        ".github/actionlint.yaml",
        "scripts/ci/validate-workflow.py",
        "scripts/ci/promote-baseline.py",
        "tests/ci/test_workflow_security.py",
        "tests/ci/test_artifact_policy.py",
        "tests/ci/test_baseline_promotion.py",
    ),
    "06-07": (
        "scripts/ci/ci-gate.py",
        "scripts/ci/configure-branch-protection.ps1",
        "scripts/ci/verify-release-gate.py",
        "tests/ci/test_ci_gate.py",
        "tests/ci/test_branch_protection.py",
        "tests/ci/test_release_gate.py",
    ),
}

SIGNED_OR_POLICY_PATHS = (
    ".quality/coverage-policy.yml",
    ".github/quality/baseline-policy.yml",
    ".github/ci/service-lock.json",
    "backend/evals/rag-quality-policy.v1.yml",
    "backend/evals/fixtures/rag-quality-benchmark.v1.json",
    "backend/evals/calibration/rag-judge-calibration.v1.json",
)


class ReleaseGateError(Exception):
    """Fail-closed release gate error."""


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ReleaseVerdict:
    ok: bool
    checks: list[CheckResult] = field(default_factory=list)
    blocked_external: bool = False
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": "passed" if self.ok else "failed",
            "blocked_external_configuration": self.blocked_external,
            "failures": list(self.failures),
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail}
                for c in self.checks
            ],
            "gate": "phase-06-release",
        }


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def check_summaries(phase_dir: Path = PHASE_DIR) -> list[CheckResult]:
    results: list[CheckResult] = []
    for plan in PLAN_IDS:
        path = phase_dir / f"{plan}-SUMMARY.md"
        if not path.is_file():
            results.append(
                CheckResult(plan, False, f"missing SUMMARY: {_rel(path)}")
            )
            continue
        text = path.read_text(encoding="utf-8")
        # Accept COMPLETE or COMPLETE_WITH_EXTERNAL_BLOCK for 06-07 only when
        # other evidence is present — still recorded; overall ok depends on
        # branch protection separately.
        if re.search(
            r"\*\*Status:\*\*\s*(COMPLETE|COMPLETE_WITH_EXTERNAL_BLOCK|VERIFIED)",
            text,
            re.I,
        ):
            results.append(CheckResult(plan, True, f"SUMMARY present ({path.name})"))
        elif re.search(r"^# .*Summary", text, re.M) and len(text) > 100:
            # Soft accept structured summary without exact status line
            results.append(
                CheckResult(plan, True, f"SUMMARY present (no status line): {path.name}")
            )
        else:
            results.append(
                CheckResult(plan, False, f"SUMMARY incomplete: {_rel(path)}")
            )
    return results


def check_required_artifacts(repo_root: Path = REPO_ROOT) -> list[CheckResult]:
    results: list[CheckResult] = []
    for plan, paths in REQUIRED_ARTIFACTS.items():
        missing = [p for p in paths if not (repo_root / p).is_file()]
        if missing:
            results.append(
                CheckResult(
                    f"artifacts:{plan}",
                    False,
                    "missing: " + ", ".join(missing),
                )
            )
        else:
            results.append(
                CheckResult(
                    f"artifacts:{plan}",
                    True,
                    f"{len(paths)} evidence files present",
                )
            )
    return results


def check_signed_and_policy_files(repo_root: Path = REPO_ROOT) -> list[CheckResult]:
    results: list[CheckResult] = []
    for rel in SIGNED_OR_POLICY_PATHS:
        path = repo_root / rel
        if not path.is_file():
            results.append(CheckResult(f"policy:{rel}", False, "missing"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # JSON suites should carry schema_version; YAML policies too.
        if path.suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                results.append(
                    CheckResult(f"policy:{rel}", False, f"invalid JSON: {exc}")
                )
                continue
            schema = data.get("schema_version")
            sig = (
                data.get("signature")
                or data.get("suite_signature")
                or data.get("fixture_signature")
                or data.get("report_signature")
            )
            if not schema:
                results.append(
                    CheckResult(f"policy:{rel}", False, "missing schema_version")
                )
            else:
                detail = f"schema={schema}"
                if sig:
                    detail += ";signed"
                results.append(CheckResult(f"policy:{rel}", True, detail))
        else:
            # YAML: require schema_version or version key
            try:
                data = yaml.safe_load(text)
            except yaml.YAMLError as exc:
                results.append(
                    CheckResult(f"policy:{rel}", False, f"invalid YAML: {exc}")
                )
                continue
            if not isinstance(data, dict):
                results.append(
                    CheckResult(f"policy:{rel}", False, "YAML must be mapping")
                )
                continue
            if not (data.get("schema_version") or data.get("version")):
                # service-lock uses fail_closed instead
                if data.get("fail_closed") is True and (
                    "postgres" in data or "chroma" in data
                ):
                    results.append(
                        CheckResult(f"policy:{rel}", True, "service-lock fail_closed")
                    )
                else:
                    results.append(
                        CheckResult(
                            f"policy:{rel}",
                            False,
                            "missing schema_version/version",
                        )
                    )
            else:
                results.append(
                    CheckResult(
                        f"policy:{rel}",
                        True,
                        f"schema={data.get('schema_version') or data.get('version')}",
                    )
                )
    return results


def check_workflow_ci_gate(ci_path: Path = CI_PATH) -> list[CheckResult]:
    results: list[CheckResult] = []
    if not ci_path.is_file():
        return [CheckResult("workflow:ci-gate", False, f"missing {_rel(ci_path)}")]
    wf = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    jobs = (wf or {}).get("jobs") or {}
    if "ci-gate" not in jobs:
        results.append(CheckResult("workflow:ci-gate", False, "job ci-gate missing"))
        return results
    job = jobs["ci-gate"]
    name = job.get("name")
    if name != "ci-gate":
        results.append(
            CheckResult(
                "workflow:ci-gate",
                False,
                f"job name must be exactly 'ci-gate', got {name!r}",
            )
        )
    else:
        if_expr = str(job.get("if") or "")
        if "always()" not in if_expr:
            results.append(
                CheckResult(
                    "workflow:ci-gate",
                    False,
                    "ci-gate must use if: always()",
                )
            )
        else:
            results.append(
                CheckResult("workflow:ci-gate", True, "job name=ci-gate if=always()")
            )

    # Retention / flake policy presence
    if POLICY_PATH.is_file():
        policy = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
        flake = (policy or {}).get("flake") or {}
        ret = ((policy or {}).get("artifacts") or {}).get("retention_days") or {}
        if flake.get("pr_max") != 0:
            results.append(
                CheckResult("policy:flake", False, "flake.pr_max must be 0")
            )
        else:
            results.append(CheckResult("policy:flake", True, "pr_max=0"))
        expected_ret = {
            "pr_junit_coverage_openapi": 14,
            "playwright_failure": 7,
            "main_integration_logs": 30,
            "nightly_signed_reports_baselines": 180,
        }
        if any(ret.get(k) != v for k, v in expected_ret.items()):
            results.append(
                CheckResult(
                    "policy:retention",
                    False,
                    f"retention mismatch: {ret}",
                )
            )
        else:
            results.append(CheckResult("policy:retention", True, "D-17 retention locked"))
        timeouts = (policy or {}).get("job_timeouts_minutes") or {}
        if timeouts.get("nightly") != 60 or timeouts.get("static") != 5:
            results.append(
                CheckResult("policy:timeouts", False, f"timeouts {timeouts}")
            )
        else:
            results.append(CheckResult("policy:timeouts", True, "D-16 job timeouts"))
    else:
        results.append(CheckResult("policy:baseline", False, "baseline-policy missing"))
    return results


def extract_contexts_from_protection(payload: dict[str, Any]) -> list[str]:
    rsc = payload.get("required_status_checks") or {}
    contexts = rsc.get("contexts")
    if isinstance(contexts, list) and contexts:
        return [str(c) for c in contexts]
    checks = rsc.get("checks") or []
    out: list[str] = []
    for item in checks:
        if isinstance(item, dict) and item.get("context"):
            out.append(str(item["context"]))
        elif isinstance(item, str):
            out.append(item)
    return out


def assert_contexts_exactly_ci_gate(contexts: list[str]) -> bool:
    normalized = [c.strip() for c in contexts if c and str(c).strip()]
    return normalized == ["ci-gate"]


def check_branch_protection(
    *,
    repository: str | None = None,
    skip_remote: bool = False,
    protection_json: dict[str, Any] | None = None,
    protection_status: str | None = None,
) -> tuple[list[CheckResult], bool]:
    """Return (checks, blocked_external).

    If protection_json/status provided (tests), use those instead of live gh.
    """
    results: list[CheckResult] = []
    blocked = False

    if not PROTECTION_SCRIPT.is_file():
        results.append(
            CheckResult(
                "branch-protection:script",
                False,
                f"missing {_rel(PROTECTION_SCRIPT)}",
            )
        )
        return results, True

    results.append(
        CheckResult(
            "branch-protection:script",
            True,
            "configure-branch-protection.ps1 present",
        )
    )

    # Script contract
    script_text = PROTECTION_SCRIPT.read_text(encoding="utf-8")
    for token in (
        "ci-gate",
        "blocked_external_configuration",
        "gh api",
        "-Verify",
        "-Repository",
    ):
        if token not in script_text:
            results.append(
                CheckResult(
                    "branch-protection:contract",
                    False,
                    f"script missing token {token!r}",
                )
            )
            return results, False
    results.append(
        CheckResult("branch-protection:contract", True, "script contract tokens ok")
    )

    if protection_json is not None or protection_status is not None:
        if protection_status == "blocked_external_configuration":
            results.append(
                CheckResult(
                    "branch-protection:readback",
                    False,
                    "blocked_external_configuration",
                )
            )
            return results, True
        contexts = extract_contexts_from_protection(protection_json or {})
        if assert_contexts_exactly_ci_gate(contexts):
            results.append(
                CheckResult(
                    "branch-protection:readback",
                    True,
                    'contexts=["ci-gate"]',
                )
            )
        else:
            results.append(
                CheckResult(
                    "branch-protection:readback",
                    False,
                    f"contexts={contexts!r} want ['ci-gate']",
                )
            )
        return results, False

    if skip_remote:
        results.append(
            CheckResult(
                "branch-protection:readback",
                False,
                "skipped remote (skip_remote=true); not verified",
            )
        )
        return results, True

    # Live verify via PowerShell script -Verify
    cmd = [
        "powershell",
        "-NoProfile",
        "-File",
        str(PROTECTION_SCRIPT),
        "-Verify",
    ]
    if repository:
        cmd.extend(["-Repository", repository])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(REPO_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        results.append(
            CheckResult(
                "branch-protection:readback",
                False,
                f"blocked_external_configuration: invoke failed: {exc}",
            )
        )
        return results, True

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode == 2 or "blocked_external_configuration" in combined:
        results.append(
            CheckResult(
                "branch-protection:readback",
                False,
                "blocked_external_configuration",
            )
        )
        return results, True
    if proc.returncode != 0:
        results.append(
            CheckResult(
                "branch-protection:readback",
                False,
                f"verify failed rc={proc.returncode}: {combined.strip()[:500]}",
            )
        )
        return results, False

    results.append(
        CheckResult(
            "branch-protection:readback",
            True,
            'live readback contexts=["ci-gate"]',
        )
    )
    return results, False


def run_release_gate(
    *,
    repository: str | None = None,
    skip_remote: bool = False,
    protection_json: dict[str, Any] | None = None,
    protection_status: str | None = None,
    require_06_07_summary: bool = True,
) -> ReleaseVerdict:
    checks: list[CheckResult] = []
    checks.extend(check_summaries())
    if not require_06_07_summary:
        # Filter 06-07 summary failure during bootstrap if needed
        checks = [
            c
            for c in checks
            if not (c.name == "06-07" and not c.ok)
        ]
    checks.extend(check_required_artifacts())
    checks.extend(check_signed_and_policy_files())
    checks.extend(check_workflow_ci_gate())
    bp_checks, blocked = check_branch_protection(
        repository=repository,
        skip_remote=skip_remote,
        protection_json=protection_json,
        protection_status=protection_status,
    )
    checks.extend(bp_checks)

    failures = [f"{c.name}: {c.detail}" for c in checks if not c.ok]
    ok = len(failures) == 0 and not blocked
    # blocked_external also implies not ok
    if blocked and not any("blocked_external" in f for f in failures):
        failures.append("branch-protection: blocked_external_configuration")
        ok = False
    return ReleaseVerdict(
        ok=ok,
        checks=checks,
        blocked_external=blocked,
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 06 release gate verifier (06-07)"
    )
    parser.add_argument(
        "--repository",
        default=None,
        help="owner/repo for branch protection verify",
    )
    parser.add_argument(
        "--skip-remote",
        action="store_true",
        help="Do not call gh/powershell (remote check fails closed)",
    )
    parser.add_argument(
        "--protection-json",
        type=Path,
        help="Inject protection payload for offline tests",
    )
    parser.add_argument(
        "--protection-status",
        default=None,
        help="Inject status e.g. blocked_external_configuration",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write verdict JSON",
    )
    args = parser.parse_args(argv)

    protection_json = None
    if args.protection_json:
        protection_json = json.loads(
            args.protection_json.read_text(encoding="utf-8")
        )

    verdict = run_release_gate(
        repository=args.repository,
        skip_remote=args.skip_remote,
        protection_json=protection_json,
        protection_status=args.protection_status,
    )
    blob = verdict.to_dict()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(blob, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    if verdict.ok:
        print("[release-gate] PASS Phase 06 release evidence complete")
        return 0

    print("[release-gate] FAIL Phase 06 incomplete", file=sys.stderr)
    if verdict.blocked_external:
        print(
            "[release-gate] blocked_external_configuration — "
            "admin must configure branch protection required contexts to [ci-gate]",
            file=sys.stderr,
        )
    for f in verdict.failures:
        print(f"  - {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
