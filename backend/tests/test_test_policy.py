"""
Contract tests for automated quality policy (Phase 06-01).

Locks D-09 coverage gates, D-10 flake policy, D-16 timeouts, and tool versions.
Fail closed on missing policy fields, critical glob zero hits, and low coverage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / ".quality" / "coverage-policy.yml"
SCHEMA_PATH = REPO_ROOT / ".quality" / "coverage-policy.schema.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Locked tool versions (06-RESEARCH)
LOCKED_TOOLS = {
    "pytest_cov": "7.1.0",
    "pytest_timeout": "2.4.0",
    "vitest": "4.1.10",
    "vitest_coverage_v8": "4.1.10",
}

# D-09
LOCKED_BACKEND_OVERALL = {"line": 80, "branch": 70}
LOCKED_BACKEND_CRITICAL = {"line": 90, "branch": 85}
LOCKED_FRONTEND_OVERALL = {"line": 75, "branch": 65}
LOCKED_FRONTEND_CRITICAL = {"line": 85, "branch": 75}
LOCKED_DIFF = 90

# D-16
LOCKED_TIMEOUTS = {
    "unit": 5,
    "contract": 15,
    "integration": 30,
    "browser": 60,
    "live": 180,
}


class PolicyError(Exception):
    """Fail-closed quality policy violation."""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise PolicyError(f"Policy must be a mapping: {path}")
    return data


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_schema(policy: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    schema = schema or load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(policy), key=lambda e: list(e.path))
    if errors:
        messages = "; ".join(e.message for e in errors[:5])
        raise PolicyError(f"Policy schema validation failed: {messages}")


def resolve_side_globs(side_root: Path, globs: list[str]) -> list[Path]:
    """Resolve coverage globs relative to backend/ or frontend/ package root."""
    hits: list[Path] = []
    for pattern in globs:
        direct = side_root / pattern
        if direct.is_file():
            hits.append(direct)
            continue
        hits.extend(p for p in side_root.glob(pattern) if p.is_file())
    return sorted({p.resolve() for p in hits})


def assert_critical_globs_hit(policy: dict[str, Any], repo_root: Path = REPO_ROOT) -> None:
    """Critical coverage globs must resolve to at least one file (zero hits → fail)."""
    for side in ("backend", "frontend"):
        globs = policy["coverage"][side]["critical"]["globs"]
        hits = resolve_side_globs(repo_root / side, globs)
        if not hits:
            raise PolicyError(
                f"Critical coverage globs for {side} matched zero files: {globs}"
            )


def evaluate_line_branch(
    label: str,
    measured: dict[str, float],
    required: dict[str, float],
) -> list[str]:
    failures: list[str] = []
    for key in ("line", "branch"):
        if measured.get(key, 0) < required[key]:
            failures.append(
                f"{label} {key} coverage {measured.get(key, 0)} < required {required[key]}"
            )
    return failures


def evaluate_coverage_report(
    policy: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """
    Evaluate a coverage summary report against policy.

    report shape:
      {
        "backend": {"overall": {"line": n, "branch": n},
                    "critical": {"line": n, "branch": n},
                    "critical_files_hit": int},
        "frontend": {...},
        "diff_coverage": n
      }
    """
    failures: list[str] = []
    cov = policy["coverage"]

    for side in ("backend", "frontend"):
        measured_side = report.get(side) or {}
        overall_req = cov[side]["overall"]
        critical_req = {"line": cov[side]["critical"]["line"], "branch": cov[side]["critical"]["branch"]}
        failures.extend(
            evaluate_line_branch(
                f"{side}.overall",
                measured_side.get("overall") or {},
                overall_req,
            )
        )
        failures.extend(
            evaluate_line_branch(
                f"{side}.critical",
                measured_side.get("critical") or {},
                critical_req,
            )
        )
        if int(measured_side.get("critical_files_hit", 0)) <= 0:
            failures.append(f"{side}.critical globs produced zero file hits")

    diff_req = cov["diff_coverage"]["minimum"]
    diff_val = float(report.get("diff_coverage", 0))
    if diff_val < diff_req:
        failures.append(f"diff_coverage {diff_val} < required {diff_req}")

    if failures:
        raise PolicyError("Coverage policy failed:\n  - " + "\n  - ".join(failures))


def evaluate_flake(policy: dict[str, Any], pr_flake_count: int, infra_retries: int) -> None:
    if pr_flake_count > policy["flake"]["pr_max"]:
        raise PolicyError(
            f"PR flake count {pr_flake_count} exceeds pr_max={policy['flake']['pr_max']}"
        )
    max_retry = policy["flake"]["external_infra"]["max_retry"]
    if infra_retries > max_retry:
        raise PolicyError(
            f"external infra retries {infra_retries} exceed max_retry={max_retry}"
        )
    if not policy["flake"]["external_infra"]["save_first_failure_evidence"]:
        raise PolicyError("external_infra.save_first_failure_evidence must be true")


# ── Tests ──


class TestPolicySchemaAndLocks:
    def test_canonical_policy_exists_and_validates(self):
        policy = load_yaml(POLICY_PATH)
        validate_schema(policy)

    def test_locked_coverage_thresholds(self):
        policy = load_yaml(POLICY_PATH)
        be = policy["coverage"]["backend"]
        fe = policy["coverage"]["frontend"]
        assert be["overall"] == LOCKED_BACKEND_OVERALL
        assert be["critical"]["line"] == LOCKED_BACKEND_CRITICAL["line"]
        assert be["critical"]["branch"] == LOCKED_BACKEND_CRITICAL["branch"]
        assert fe["overall"] == LOCKED_FRONTEND_OVERALL
        assert fe["critical"]["line"] == LOCKED_FRONTEND_CRITICAL["line"]
        assert fe["critical"]["branch"] == LOCKED_FRONTEND_CRITICAL["branch"]
        assert policy["coverage"]["diff_coverage"]["minimum"] == LOCKED_DIFF

    def test_locked_timeouts(self):
        policy = load_yaml(POLICY_PATH)
        assert policy["timeouts"] == LOCKED_TIMEOUTS

    def test_locked_flake_policy(self):
        policy = load_yaml(POLICY_PATH)
        assert policy["flake"]["pr_max"] == 0
        assert policy["flake"]["external_infra"]["max_retry"] == 1
        assert policy["flake"]["external_infra"]["save_first_failure_evidence"] is True

    def test_locked_tool_versions(self):
        policy = load_yaml(POLICY_PATH)
        assert policy["tools"] == LOCKED_TOOLS

    def test_critical_globs_resolve(self):
        policy = load_yaml(POLICY_PATH)
        assert_critical_globs_hit(policy)


class TestPolicyNegativeCases:
    def test_invalid_policy_missing_fields_fails(self):
        invalid = load_yaml(FIXTURES / "coverage-policy-invalid.yml")
        with pytest.raises(PolicyError, match="schema validation failed"):
            validate_schema(invalid)

    def test_low_coverage_fails(self):
        policy = load_yaml(POLICY_PATH)
        low_report = {
            "backend": {
                "overall": {"line": 10, "branch": 5},
                "critical": {"line": 10, "branch": 5},
                "critical_files_hit": 1,
            },
            "frontend": {
                "overall": {"line": 10, "branch": 5},
                "critical": {"line": 10, "branch": 5},
                "critical_files_hit": 1,
            },
            "diff_coverage": 10,
        }
        with pytest.raises(PolicyError, match="Coverage policy failed"):
            evaluate_coverage_report(policy, low_report)

    def test_critical_glob_zero_hits_fails(self):
        policy = load_yaml(POLICY_PATH)
        report = {
            "backend": {
                "overall": {"line": 99, "branch": 99},
                "critical": {"line": 99, "branch": 99},
                "critical_files_hit": 0,
            },
            "frontend": {
                "overall": {"line": 99, "branch": 99},
                "critical": {"line": 99, "branch": 99},
                "critical_files_hit": 1,
            },
            "diff_coverage": 99,
        }
        with pytest.raises(PolicyError, match="zero file hits"):
            evaluate_coverage_report(policy, report)

    def test_diff_coverage_below_threshold_fails(self):
        policy = load_yaml(POLICY_PATH)
        report = {
            "backend": {
                "overall": {"line": 99, "branch": 99},
                "critical": {"line": 99, "branch": 99},
                "critical_files_hit": 1,
            },
            "frontend": {
                "overall": {"line": 99, "branch": 99},
                "critical": {"line": 99, "branch": 99},
                "critical_files_hit": 1,
            },
            "diff_coverage": 50,
        }
        with pytest.raises(PolicyError, match="diff_coverage"):
            evaluate_coverage_report(policy, report)

    def test_pr_flake_nonzero_fails(self):
        policy = load_yaml(POLICY_PATH)
        with pytest.raises(PolicyError, match="PR flake"):
            evaluate_flake(policy, pr_flake_count=1, infra_retries=0)

    def test_external_infra_retry_over_limit_fails(self):
        policy = load_yaml(POLICY_PATH)
        with pytest.raises(PolicyError, match="retries"):
            evaluate_flake(policy, pr_flake_count=0, infra_retries=2)

    def test_passing_report_succeeds(self):
        policy = load_yaml(POLICY_PATH)
        report = {
            "backend": {
                "overall": {"line": 85, "branch": 75},
                "critical": {"line": 95, "branch": 90},
                "critical_files_hit": 3,
            },
            "frontend": {
                "overall": {"line": 80, "branch": 70},
                "critical": {"line": 90, "branch": 80},
                "critical_files_hit": 2,
            },
            "diff_coverage": 92,
        }
        evaluate_coverage_report(policy, report)
        evaluate_flake(policy, pr_flake_count=0, infra_retries=1)

    def test_low_fixture_file_is_self_consistent(self):
        """Fixture documents deliberately low thresholds used in negative scenarios."""
        low = load_yaml(FIXTURES / "coverage-policy-low.yml")
        # structure is intentionally complete enough for schema, but values are low
        validate_schema(low)
        assert low["coverage"]["backend"]["overall"]["line"] < LOCKED_BACKEND_OVERALL["line"]


class TestMarkerAndTimeoutContract:
    def test_timeout_map_matches_policy(self):
        from tests.conftest import MARKER_TIMEOUTS

        policy = load_yaml(POLICY_PATH)
        for key in ("unit", "contract", "integration", "live"):
            assert MARKER_TIMEOUTS[key] == policy["timeouts"][key]

    def test_primary_markers_defined(self):
        from tests.conftest import PRIMARY_MARKERS

        assert PRIMARY_MARKERS == frozenset({"unit", "integration", "contract", "live"})
