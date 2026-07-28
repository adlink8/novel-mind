"""ci-gate aggregate contract tests (06-07 / D-13)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.contract


def _load_ci_gate():
    path = REPO_ROOT / "scripts" / "ci" / "ci-gate.py"
    spec = importlib.util.spec_from_file_location("ci_gate", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses requires the module to be present in sys.modules during exec
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cg = _load_ci_gate()


def _success_producers(names: list[str]) -> dict:
    return {
        n: cg.ProducerResult(name=n, result="success") for n in names
    }


def _pr_required() -> list[str]:
    return list(cg.REQUIRED_BY_EVENT["pull_request"])


def _all_known() -> list[str]:
    return [
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
        "live",
        "nightly",
        "promote-baseline",
        "alert",
    ]


def test_workflow_defines_ci_gate_job() -> None:
    wf = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert "ci-gate" in jobs
    job = jobs["ci-gate"]
    assert job.get("name") == "ci-gate"
    assert str(job.get("if")).strip() in ("always()", "${{ always() }}")
    needs = job.get("needs") or []
    for producer in (
        "guard",
        "static",
        "unit",
        "openapi",
        "integration",
        "browser",
        "codeql",
        "workflow-lint",
        "live",
        "nightly",
        "promote-baseline",
    ):
        assert producer in needs
    # Must not re-run full test suites in the gate job
    blob = yaml.dump(job)
    assert "pytest -m" not in blob
    assert "npm run test" not in blob
    assert "playwright test" not in blob
    assert "scripts/ci/ci-gate.py" in blob


def test_pr_success_matrix() -> None:
    producers = _success_producers(_pr_required())
    for optional in ("live", "nightly", "promote-baseline", "alert"):
        producers[optional] = cg.ProducerResult(name=optional, result="skipped")
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is True
    assert verdict.failures == []


def test_pr_failed_required() -> None:
    producers = _success_producers(_pr_required())
    producers["unit"] = cg.ProducerResult(name="unit", result="failure")
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is False
    assert any("unit: failure" in f for f in verdict.failures)


def test_pr_cancelled_required() -> None:
    producers = _success_producers(_pr_required())
    producers["integration"] = cg.ProducerResult(
        name="integration", result="cancelled"
    )
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is False
    assert any("cancelled" in f for f in verdict.failures)


def test_pr_unexpected_skipped() -> None:
    producers = _success_producers(_pr_required())
    producers["browser"] = cg.ProducerResult(name="browser", result="skipped")
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is False
    assert any("unexpected skipped" in f for f in verdict.failures)


def test_pr_missing_producer() -> None:
    producers = _success_producers([n for n in _pr_required() if n != "static"])
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is False
    assert any("static: missing" in f for f in verdict.failures)


def test_timeout_fails_gate() -> None:
    producers = _success_producers(_pr_required())
    producers["unit"] = cg.ProducerResult(name="unit", result="timed_out")
    verdict = cg.evaluate_gate(event="pull_request", producers=producers)
    assert verdict.ok is False
    assert any("timeout" in f for f in verdict.failures)


def test_main_requires_live() -> None:
    producers = _success_producers(list(cg.REQUIRED_BY_EVENT["push"]))
    for optional in ("nightly", "promote-baseline", "alert"):
        producers[optional] = cg.ProducerResult(name=optional, result="skipped")
    verdict = cg.evaluate_gate(event="push", producers=producers)
    assert verdict.ok is True

    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    verdict2 = cg.evaluate_gate(event="push", producers=producers)
    assert verdict2.ok is False
    assert any("live" in f and "skipped" in f for f in verdict2.failures)


def test_schedule_requires_nightly_and_promote() -> None:
    producers = _success_producers(list(cg.REQUIRED_BY_EVENT["schedule"]))
    producers["alert"] = cg.ProducerResult(name="alert", result="skipped")
    verdict = cg.evaluate_gate(event="schedule", producers=producers)
    assert verdict.ok is True

    producers["nightly"] = cg.ProducerResult(name="nightly", result="failure")
    producers["promote-baseline"] = cg.ProducerResult(
        name="promote-baseline", result="skipped"
    )
    verdict2 = cg.evaluate_gate(event="schedule", producers=producers)
    assert verdict2.ok is False
    assert any("nightly" in f for f in verdict2.failures)


def test_missing_artifact_fails_when_required() -> None:
    producers = _success_producers(_pr_required())
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    verdict = cg.evaluate_gate(
        event="pull_request",
        producers=producers,
        artifacts={},
        require_artifacts=True,
    )
    assert verdict.ok is False
    assert any("missing/stale artifact" in f for f in verdict.failures)


def test_hash_mismatch_fails() -> None:
    producers = _success_producers(_pr_required())
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    now = time.time()
    arts = {
        "unit": {
            "schema_version": "ci-artifact.v1",
            "artifact_name": "unit-junit-coverage",
            "status": "valid",
            "content_hash": "aaa",
            "expected_content_hash": "bbb",
            "produced_at": now,
        },
        "openapi": {
            "schema_version": "ci-artifact.v1",
            "artifact_name": "openapi-export",
            "status": "valid",
            "content_hash": "ok",
            "expected_content_hash": "ok",
            "produced_at": now,
        },
        "integration": {
            "schema_version": "ci-artifact.v1",
            "artifact_name": "integration-results",
            "status": "valid",
            "content_hash": "ok",
            "expected_content_hash": "ok",
            "produced_at": now,
        },
    }
    verdict = cg.evaluate_gate(
        event="pull_request",
        producers=producers,
        artifacts=arts,
        now=now,
    )
    assert verdict.ok is False
    assert any("hash mismatch" in f for f in verdict.failures)


def test_schema_mismatch_fails() -> None:
    now = time.time()
    producers = _success_producers(_pr_required())
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    arts = {
        "unit": {
            "schema_version": "wrong.v0",
            "artifact_name": "unit-junit-coverage",
            "status": "valid",
            "content_hash": "h",
            "produced_at": now,
        },
        "openapi": {
            "schema_version": "ci-artifact.v1",
            "status": "valid",
            "content_hash": "h",
            "produced_at": now,
        },
        "integration": {
            "schema_version": "ci-artifact.v1",
            "status": "valid",
            "content_hash": "h",
            "produced_at": now,
        },
    }
    verdict = cg.evaluate_gate(
        event="pull_request",
        producers=producers,
        artifacts=arts,
        now=now,
    )
    assert verdict.ok is False
    assert any("schema mismatch" in f for f in verdict.failures)


def test_stale_artifact_fails() -> None:
    now = time.time()
    producers = _success_producers(_pr_required())
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    arts = {
        name: {
            "schema_version": "ci-artifact.v1",
            "status": "valid",
            "content_hash": "h",
            "produced_at": now - (7 * 60 * 60),
        }
        for name in ("unit", "openapi", "integration")
    }
    verdict = cg.evaluate_gate(
        event="pull_request",
        producers=producers,
        artifacts=arts,
        now=now,
        max_artifact_age_seconds=6 * 60 * 60,
    )
    assert verdict.ok is False
    assert any("stale artifact" in f for f in verdict.failures)


def test_valid_artifacts_pass() -> None:
    now = time.time()
    producers = _success_producers(_pr_required())
    producers["live"] = cg.ProducerResult(name="live", result="skipped")
    arts = {
        name: {
            "schema_version": "ci-artifact.v1",
            "status": "valid",
            "content_hash": "h",
            "expected_content_hash": "h",
            "produced_at": now,
        }
        for name in ("unit", "openapi", "integration")
    }
    verdict = cg.evaluate_gate(
        event="pull_request",
        producers=producers,
        artifacts=arts,
        now=now,
        require_artifacts=True,
    )
    assert verdict.ok is True


def test_cli_success_and_failure(tmp_path: Path) -> None:
    payload = {
        "event": "pull_request",
        "producers": {
            n: {"result": "success"} for n in _pr_required()
        },
    }
    payload["producers"]["live"] = {"result": "skipped"}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cg.main(["--results-json", str(path)]) == 0

    payload["producers"]["unit"] = {"result": "failure"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert cg.main(["--results-json", str(path)]) == 1


def test_cli_producer_flags() -> None:
    args = ["--event", "pull_request"]
    for n in _pr_required():
        args.extend(["--producer", f"{n}=success"])
    args.extend(["--producer", "live=skipped"])
    assert cg.main(args) == 0


def test_results_json_load_missing(tmp_path: Path) -> None:
    with pytest.raises(cg.CiGateError):
        cg.load_results_json(tmp_path / "nope.json")


def test_dispatch_with_nightly_requires_promote() -> None:
    producers = _success_producers(
        list(cg.required_producers("workflow_dispatch", run_nightly=True))
    )
    verdict = cg.evaluate_gate(
        event="workflow_dispatch",
        producers=producers,
        run_nightly=True,
    )
    assert verdict.ok is True
    assert "nightly" in verdict.required
    assert "promote-baseline" in verdict.required

    producers["promote-baseline"] = cg.ProducerResult(
        name="promote-baseline", result="skipped"
    )
    verdict2 = cg.evaluate_gate(
        event="workflow_dispatch",
        producers=producers,
        run_nightly=True,
    )
    assert verdict2.ok is False
