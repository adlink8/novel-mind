"""Workflow security & event-matrix contract tests (06-06 / D-13, D-16, D-18)."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
POLICY_PATH = REPO_ROOT / ".github" / "quality" / "baseline-policy.yml"

pytestmark = pytest.mark.contract


def _load_validate_module():
    path = REPO_ROOT / "scripts" / "ci" / "validate-workflow.py"
    spec = importlib.util.spec_from_file_location("validate_workflow", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vw = _load_validate_module()


@pytest.fixture(scope="module")
def policy() -> dict:
    return vw.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def workflow() -> dict:
    return vw.load_yaml(CI_PATH)


def test_validate_workflow_passes() -> None:
    summary = vw.validate_workflow(CI_PATH, POLICY_PATH)
    assert summary["ok"] is True
    assert "nightly" in summary["jobs"]
    assert "alert" in summary["jobs"]
    assert summary["actionlint"] == "v1.7.12"


def test_no_pull_request_target(workflow: dict) -> None:
    vw.assert_no_pull_request_target(workflow)
    on = workflow.get("on") or workflow.get(True)
    assert "pull_request_target" not in on


def test_scheduled_ci_is_paused(workflow: dict) -> None:
    on = workflow.get("on") or workflow.get(True)
    assert "schedule" not in on
    assert "workflow_dispatch" in on
    assert "run_nightly" in on["workflow_dispatch"]["inputs"]


def test_job_timeouts_locked(workflow: dict, policy: dict) -> None:
    vw.assert_job_timeouts(workflow, policy)
    jobs = workflow["jobs"]
    assert jobs["static"]["timeout-minutes"] == 5
    assert jobs["unit"]["timeout-minutes"] == 20
    assert jobs["integration"]["timeout-minutes"] == 15
    assert jobs["browser"]["timeout-minutes"] == 30
    assert jobs["live"]["timeout-minutes"] == 45
    assert jobs["nightly-preflight"]["timeout-minutes"] == 5
    assert jobs["nightly"]["timeout-minutes"] == 60
    assert jobs["nightly-finalize"]["timeout-minutes"] == 5


def test_concurrency_pr_cancel_only(workflow: dict, policy: dict) -> None:
    vw.assert_concurrency(workflow, policy)
    cancel = str(workflow["concurrency"]["cancel-in-progress"])
    assert "pull_request" in cancel


def test_secret_jobs_gated(workflow: dict) -> None:
    vw.assert_secret_jobs_not_on_pr(workflow)
    for name in ("live", "nightly", "nightly-finalize", "promote-baseline", "alert"):
        assert "if" in workflow["jobs"][name]


def test_self_hosted_nightly_only(workflow: dict) -> None:
    vw.assert_self_hosted_isolation(workflow)
    runs = workflow["jobs"]["nightly"]["runs-on"]
    assert "self-hosted" in str(runs)


def test_nightly_control_plane_is_always_hosted_and_emits_terminal_artifact(
    workflow: dict,
) -> None:
    jobs = workflow["jobs"]
    preflight = jobs["nightly-preflight"]
    finalize = jobs["nightly-finalize"]
    assert preflight["runs-on"] == "ubuntu-latest"
    assert finalize["runs-on"] == "ubuntu-latest"
    assert preflight["environment"] == "quality-benchmark"
    assert "provider_ready" in preflight["outputs"]
    assert "nightly-preflight.outputs.provider_ready == 'true'" in jobs["nightly"]["if"]
    assert "promotable" in finalize["outputs"]
    assert (
        "nightly-finalize.outputs.promotable == 'true'"
        in jobs["promote-baseline"]["if"]
    )
    finalize_blob = yaml.dump(finalize, sort_keys=False)
    preflight_blob = yaml.dump(preflight, sort_keys=False)
    assert "NIGHTLY_RUNNER_READ_TOKEN" in preflight_blob
    assert "finalize-nightly-report.py" in finalize_blob
    assert "nightly-rag-report" in finalize_blob
    assert "if-no-files-found: error" in finalize_blob


def test_alert_isolation_d18(workflow: dict, policy: dict) -> None:
    vw.assert_alert_isolation(workflow, policy)
    alert = workflow["jobs"]["alert"]
    assert alert["permissions"] == {"contents": "read", "issues": "write"}
    assert alert["environment"] == "quality-alerts"
    for step in alert["steps"]:
        assert not str(step.get("uses") or "").startswith("actions/checkout")


def test_alert_lifecycle_uses_stable_root_fingerprint(workflow: dict) -> None:
    alert = workflow["jobs"]["alert"]
    blob = yaml.dump(alert, sort_keys=False)
    assert "NIGHTLY_RESULT" in alert["env"]
    assert "NIGHTLY_STATUS" in alert["env"]
    assert "PROMOTE_RESULT" in alert["env"]
    assert "rootClass" in blob
    assert "runner-or-environment-unavailable" in blob
    assert "nightly-quality-regression" in blob
    assert "nightly-policy-failure" in blob
    assert "state_reason" in blob
    assert "completed" in blob
    assert "nightly-fail:${context.runId}" not in blob


def test_dispatch_requires_fixed_commit(workflow: dict) -> None:
    vw.assert_dispatch_guard(workflow)
    on = workflow.get("on") or workflow.get(True)
    assert "benchmark_commit" in on["workflow_dispatch"]["inputs"]


def test_dispatch_uses_fixed_commit_for_authority_and_final_report(
    workflow: dict,
) -> None:
    jobs = workflow["jobs"]
    for job_name in ("workflow-lint", "codeql", "promote-baseline", "ci-gate"):
        checkout = next(
            step
            for step in jobs[job_name]["steps"]
            if str(step.get("uses") or "").startswith("actions/checkout")
        )
        assert "inputs.benchmark_commit" in str(checkout["with"]["ref"])
    preflight_blob = yaml.dump(jobs["nightly-preflight"], sort_keys=False)
    finalize_blob = yaml.dump(jobs["nightly-finalize"], sort_keys=False)
    assert "BENCHMARK_SHA" in preflight_blob
    assert "benchmark_sha || github.sha" in preflight_blob
    assert "--commit-sha" in finalize_blob
    assert "benchmark_sha || github.sha" in finalize_blob


def test_baseline_materialization_has_no_unused_write_permission(
    workflow: dict,
) -> None:
    assert workflow["jobs"]["promote-baseline"]["permissions"] == {"contents": "read"}


def test_browser_gates_production_build_before_playwright(workflow: dict) -> None:
    steps = workflow["jobs"]["browser"]["steps"]
    build_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Frontend production build"
    )
    browser_index = next(
        i
        for i, step in enumerate(steps)
        if step.get("name") == "Playwright (desktop + 390px, retries=0)"
    )
    assert steps[build_index]["run"] == "npm run build"
    assert build_index < browser_index


def test_fork_pr_cannot_escalate(workflow: dict) -> None:
    vw.assert_fork_cannot_reach_secret_jobs(workflow)


def test_legacy_workflows_disabled() -> None:
    vw.assert_legacy_workflows_disabled()
    for name in ("backend-ci.yml", "frontend-ci.yml", "full-ci.yml"):
        path = REPO_ROOT / ".github" / "workflows" / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        on = data.get("on") or data.get(True) or {}
        assert "push" not in on
        assert "pull_request" not in on


def test_service_lock_present() -> None:
    lock = vw.assert_service_lock()
    assert "postgres" in lock and "chroma" in lock


def test_codeql_languages(workflow: dict, policy: dict) -> None:
    vw.assert_codeql_once(workflow, policy)


def test_negative_timeout_drift_fails(workflow: dict, policy: dict) -> None:
    bad = copy.deepcopy(workflow)
    bad["jobs"]["unit"]["timeout-minutes"] = 99
    with pytest.raises(vw.WorkflowPolicyError, match="timeout-minutes"):
        vw.assert_job_timeouts(bad, policy)


def test_negative_alert_with_checkout_fails(workflow: dict, policy: dict) -> None:
    bad = copy.deepcopy(workflow)
    bad["jobs"]["alert"]["steps"] = [
        {
            "uses": "actions/checkout@v4",
            "with": {"ref": "github.event.pull_request.head.sha"},
        }
    ]
    with pytest.raises(vw.WorkflowPolicyError, match="checkout"):
        vw.assert_alert_isolation(bad, policy)


def test_negative_secret_job_without_if_fails(workflow: dict) -> None:
    bad = copy.deepcopy(workflow)
    bad["jobs"]["live"].pop("if", None)
    with pytest.raises(vw.WorkflowPolicyError, match="gate"):
        vw.assert_secret_jobs_not_on_pr(bad)


def test_negative_pull_request_target_fails(workflow: dict) -> None:
    bad = copy.deepcopy(workflow)
    on = bad.get("on") or bad.get(True)
    on["pull_request_target"] = {"branches": ["main"]}
    with pytest.raises(vw.WorkflowPolicyError, match="pull_request_target"):
        vw.assert_no_pull_request_target(bad)


def test_negative_legacy_reenable_fails(tmp_path: Path) -> None:
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    (wf_dir / "backend-ci.yml").write_text(
        "name: bad\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: true\n",
        encoding="utf-8",
    )
    with pytest.raises(vw.WorkflowPolicyError, match="legacy"):
        vw.assert_legacy_workflows_disabled(wf_dir)


def test_cli_main_ok() -> None:
    assert vw.main(["--ci", str(CI_PATH), "--policy", str(POLICY_PATH)]) == 0


def test_artifact_retention_scan(workflow: dict) -> None:
    found = vw.assert_artifact_retention_in_workflow(workflow)
    assert found["playwright_failure"]
    assert found["nightly"]
    assert found["junit_coverage_openapi"]
