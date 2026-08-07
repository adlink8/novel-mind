"""
Contract tests for automated quality policy (Phase 06-01).

Locks D-09 coverage gates, D-10 flake policy, D-16 timeouts, and tool versions.
Fail closed on missing policy fields, critical glob zero hits, and low coverage.

Implementation lives in `scripts/ci/coverage_policy.py` (shared with the real
CI coverage gate); these tests pin the policy contract against it so the
synthetic fail-closed cases and the executed CI gate cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / ".quality" / "coverage-policy.yml"
SCHEMA_PATH = REPO_ROOT / ".quality" / "coverage-policy.schema.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Load the shared evaluator implementation (single source of truth for D-09).
_SCRIPT = REPO_ROOT / "scripts" / "ci" / "coverage_policy.py"
_spec = importlib.util.spec_from_file_location("coverage_policy_impl", _SCRIPT)
assert _spec and _spec.loader
_impl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_impl)
sys.modules["coverage_policy_impl"] = _impl

PolicyError = _impl.PolicyError
load_yaml = _impl.load_yaml
load_schema = _impl.load_schema
validate_schema = _impl.validate_schema
resolve_side_globs = _impl.resolve_side_globs
assert_critical_globs_hit = _impl.assert_critical_globs_hit
evaluate_line_branch = _impl.evaluate_line_branch
evaluate_coverage_report = _impl.evaluate_coverage_report
evaluate_flake = _impl.evaluate_flake

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
        assert (
            low["coverage"]["backend"]["overall"]["line"]
            < LOCKED_BACKEND_OVERALL["line"]
        )


class TestMarkerAndTimeoutContract:
    def test_timeout_map_matches_policy(self):
        from tests.conftest import MARKER_TIMEOUTS

        policy = load_yaml(POLICY_PATH)
        for key in ("unit", "contract", "integration", "live"):
            assert MARKER_TIMEOUTS[key] == policy["timeouts"][key]

    def test_primary_markers_defined(self):
        from tests.conftest import PRIMARY_MARKERS

        assert PRIMARY_MARKERS == frozenset({"unit", "integration", "contract", "live"})
