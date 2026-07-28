"""Phase 11 clue release evidence gate (secretless contract tests)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "backend" / "scripts" / "run_clue_qualification.py"

pytestmark = pytest.mark.contract


def _module():
    spec = importlib.util.spec_from_file_location("clue_qualification", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Ensure backend is importable for eval/gates when script loads helpers.
    backend = str(REPO / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    spec.loader.exec_module(module)
    return module


def _production_report(q):
    authority = {
        "owner_id": 1,
        "novel_id": 2,
        "version_id": 3,
        "run_id": 4,
        "run_status": "completed",
        "active_version_id": 3,
        "manifest_checksum": "1" * 64,
        "machine_clue_count": 2,
        "lifecycle_count": 4,
        "evidence_count": 4,
        "override_count": 1,
        "attempt_count": 1,
        "spoiler_safe": True,
        "spoiler_leaks": 0,
    }
    artifact = {
        "database_dialect": "postgresql",
        "authority": authority,
        "counts": {
            "machine_clues": 2,
            "lifecycle_events": 4,
            "evidence_refs": 4,
            "overrides": 1,
            "model_attempts": 1,
            "default_clues": 1,
            "full_clues": 2,
        },
        "scope": {
            "scope_clean": True,
            "chat_reject_present": True,
            "source_unavailable_protocol": True,
            "forbidden_hits": [],
        },
        "fixture": {
            "domain": "fiction",
            "case_count": 24,
            "policy_hash_expected": "x",
            "policy_hash_runtime": "x",
        },
        "spoiler_observation": {"spoiler_leaks": 0},
    }
    report = {
        "report_version": "clue-production-qualification.v1",
        "status": "qualified",
        "quality_comparable": True,
        "artifact": artifact,
        "artifact_sha256": q._sha256(artifact),
        "gates": {
            "spoiler_safety": True,
            "offline_qualified": True,
            "scope_clean": True,
        },
        "metrics": {
            "spoiler_leaks": 0,
            "paid_off_precision": 1.0,
            "active_reinforced_macro_f1": 1.0,
            "critical": {
                "false_active": 0,
                "false_paid_off": 0,
                "spoiler_leak": 0,
                "cross_scope_link": 0,
                "override_overwrite": 0,
                "chat_as_fact": 0,
                "unsupported_acceptance": 0,
            },
        },
        "browser": {
            "real_stack": True,
            "desktop": True,
            "mobile_390": True,
            "mocks_clue_api": False,
            "provider_only_control": True,
        },
        "requirements_covered": q.REQ_CLUE_IDS,
        "test_commands": q.REQUIRED_TEST_COMMANDS,
    }
    report["report_sha256"] = q.report_digest(report)
    return report


def _forged_command_results(q):
    return [
        {
            "command": command,
            "exit_code": 0,
            "output_sha256": "f" * 64,
            "output": b"forged",
        }
        for command in q.REQUIRED_TEST_COMMANDS
    ]


def _valid_command_results(q):
    return [
        {
            "command": command,
            "exit_code": 0,
            "output": b"ok",
            "output_sha256": hashlib.sha256(b"ok").hexdigest(),
        }
        for command in q.REQUIRED_TEST_COMMANDS
    ]


def test_release_gate_rejects_forged_digests(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=_forged_command_results(q),
    )
    assert verdict["status"] == "blocked_release", verdict
    assert verdict["checks"]["database_authority"] is True
    assert verdict["checks"]["command_output_attestation"] is False


def test_command_collector_hashes_exact_output_and_records_failure(tmp_path):
    q = _module()
    success = q.CommandSpec(
        display=q.REQUIRED_TEST_COMMANDS[0],
        cwd=tmp_path,
        argv=(
            sys.executable,
            "-c",
            "import sys; sys.stdout.buffer.write(b'exact-output')",
        ),
    )
    failed = q.CommandSpec(
        display=q.REQUIRED_TEST_COMMANDS[1],
        cwd=tmp_path,
        argv=(
            sys.executable,
            "-c",
            "import sys; sys.stderr.buffer.write(b'failed-output'); raise SystemExit(7)",
        ),
    )
    results = q.collect_command_results((success, failed))
    assert results[0]["exit_code"] == 0
    assert results[0]["output"] == b"exact-output"
    assert results[0]["output_sha256"] == hashlib.sha256(b"exact-output").hexdigest()
    assert results[1]["exit_code"] == 7


def test_cli_release_mode_exposes_no_observation_injection():
    q = _module()
    help_text = q.build_parser().format_help()
    assert "--verify-release" in help_text
    assert "--report" in help_text
    for forbidden in (
        "observed-authority",
        "command-results",
        "output-digest",
        "command-list",
    ):
        assert forbidden not in help_text


def test_real_browser_spec_does_not_mock_clue_api():
    source = (REPO / "frontend" / "e2e" / "clue-real.spec.ts").read_text(
        encoding="utf-8"
    )
    assert "page.route" not in source
    assert "route.fulfill" not in source
    assert "--e2e-seed-user" in source


def test_release_gate_rejects_self_claimed_report_without_observations(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path)
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["database_authority"] is False
    assert verdict["checks"]["command_output_attestation"] is False


def test_release_gate_rejects_missing_browser_evidence(tmp_path):
    q = _module()
    report = _production_report(q)
    report["browser"] = {}
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "nobrowser.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=_valid_command_results(q),
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["browser_real_stack"] is False


def test_release_gate_rejects_spoiler_leak_and_missing_metrics(tmp_path):
    q = _module()
    report = _production_report(q)
    report["gates"]["spoiler_safety"] = False
    report["metrics"]["spoiler_leaks"] = 1
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "spoiler.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=_valid_command_results(q),
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["spoiler_safety"] is False


def test_release_gate_rejects_critical_false_positive(tmp_path):
    q = _module()
    report = _production_report(q)
    report["metrics"]["critical"]["false_paid_off"] = 1
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "critical.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=_valid_command_results(q),
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["critical_zero"] is False


def test_release_gate_accepts_matching_db_and_commands(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    # Create minimal required path files if missing? e2e file must exist.
    e2e = REPO / "frontend" / "e2e" / "clue-real.spec.ts"
    assert e2e.is_file(), "clue-real.spec.ts must exist for release path checks"
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=_valid_command_results(q),
    )
    assert verdict["status"] == "qualified", verdict
    assert verdict["quality_comparable"] is True
