"""Artifact retention, sensitivity, and flake policy tests (06-06 / D-10, D-17)."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

POLICY_PATH = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.contract


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vw = _load("validate_workflow", REPO_ROOT / "scripts" / "ci" / "validate-workflow.py")
pb = _load("promote_baseline", REPO_ROOT / "scripts" / "ci" / "promote-baseline.py")


LOCKED_RETENTION = {
    "pr_junit_coverage_openapi": 14,
    "playwright_failure": 7,
    "main_integration_logs": 30,
    "nightly_signed_reports_baselines": 180,
}


@pytest.fixture(scope="module")
def policy() -> dict:
    return vw.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return vw.load_yaml(CI_PATH)


def test_policy_retention_locked(policy: dict) -> None:
    days = policy["artifacts"]["retention_days"]
    for key, val in LOCKED_RETENTION.items():
        assert days[key] == val, f"{key}: expected {val}, got {days[key]}"


def test_forbidden_globs_cover_uploads(policy: dict) -> None:
    globs = policy["artifacts"]["forbidden_globs"]
    joined = " ".join(globs)
    assert "uploads" in joined
    assert "fulltext" in joined


def test_workflow_respects_retention(workflow: dict) -> None:
    found = vw.assert_artifact_retention_in_workflow(workflow)
    assert 7 in found["playwright_failure"]
    assert 180 in found["nightly"]
    assert 14 in found["junit_coverage_openapi"]


def test_no_sensitive_paths_in_uploads(workflow: dict, policy: dict) -> None:
    vw.assert_no_sensitive_artifact_paths(workflow, policy)


def test_negative_sensitive_upload_path_fails(workflow: dict, policy: dict) -> None:
    bad = copy.deepcopy(workflow)
    bad["jobs"]["unit"]["steps"].append(
        {
            "uses": "actions/upload-artifact@v4",
            "with": {
                "name": "leaked-novel",
                "path": "backend/uploads/**",
                "retention-days": 14,
            },
        }
    )
    with pytest.raises(vw.WorkflowPolicyError, match="sensitive"):
        vw.assert_no_sensitive_artifact_paths(bad, policy)


def test_negative_playwright_retention_drift(workflow: dict) -> None:
    bad = copy.deepcopy(workflow)
    for step in bad["jobs"]["browser"]["steps"]:
        if "upload-artifact" in str(step.get("uses") or ""):
            step["with"]["retention-days"] = 90
    with pytest.raises(vw.WorkflowPolicyError, match="playwright"):
        vw.assert_artifact_retention_in_workflow(bad)


def test_fulltext_marker_detection() -> None:
    assert pb.contains_sensitive_fulltext({"body": "NOVEL_FULLTEXT_BEGIN\nchapter..."})
    assert pb.contains_sensitive_fulltext({"x": "<<<NOVEL_BODY>>>"})
    assert not pb.contains_sensitive_fulltext(
        {"status": "passed", "metrics": {"context_precision_mean": 0.9}}
    )


def test_flake_gate_pr_zero(policy: dict) -> None:
    pb.evaluate_flake_gates(
        pr_flake_count=0,
        required_failure_rate_30d=0.0,
        policy=policy,
    )
    with pytest.raises(pb.BaselinePromotionError, match="flake"):
        pb.evaluate_flake_gates(
            pr_flake_count=1,
            required_failure_rate_30d=0.0,
            policy=policy,
        )


def test_flake_gate_30d_failure_rate(policy: dict) -> None:
    # 0.1% max
    pb.evaluate_flake_gates(
        pr_flake_count=0,
        required_failure_rate_30d=0.0009,
        policy=policy,
    )
    with pytest.raises(pb.BaselinePromotionError, match="failure rate"):
        pb.evaluate_flake_gates(
            pr_flake_count=0,
            required_failure_rate_30d=0.002,
            policy=policy,
        )


def test_policy_matches_coverage_flake_pr_max(policy: dict) -> None:
    # Cross-lock with D-10
    assert policy["flake"]["pr_max"] == 0
    assert policy["flake"]["required_check_30d_failure_rate_max"] == 0.001
