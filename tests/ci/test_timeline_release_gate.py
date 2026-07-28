"""Phase 08 release evidence gate."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "backend" / "scripts" / "run_timeline_qualification.py"

pytestmark = pytest.mark.contract


def _module():
    spec = importlib.util.spec_from_file_location("timeline_qualification", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_report(q):
    evidence = [
        {"id": 10, "event_id": 20, "chapter_id": 30, "evidence_id": "a", "source_start": 0, "source_end": 4, "content_hash": "b" * 64},
        {"id": 11, "event_id": 21, "chapter_id": 31, "evidence_id": "b", "source_start": 50, "source_end": 54, "content_hash": "c" * 64},
    ]
    authority = {
        "run_id": 1,
        "run_status": "completed",
        "version_id": 2,
        "active_version_id": 2,
        "manifest_checksum": "a" * 64,
        "call_audit_ids": [40, 41, 42],
        "call_audit_states": [
            {"id": item, "status": "succeeded", "request_hash": "d" * 64, "response_hash": "e" * 64}
            for item in (40, 41, 42)
        ],
        "evidence_ref_ids": [10, 11],
        "raw_evidence_sha256": q._sha256(evidence),
    }
    artifact = {
        "database_dialect": "postgresql",
        "authority": authority,
        "run": {"id": 1, "status": "completed", "version_id": 2},
        "active_pointer": {"version_id": 2, "revision": 1, "manifest_checksum": "a" * 64},
        "counts": {"events": 2, "evidence_refs": 2, "model_attempts": 3, "completed_stages": 3},
        "events": [{"logical_event_id": "a"}, {"logical_event_id": "b"}],
        "attempts": [{"status": "succeeded"}] * 3,
        "evidence_refs": evidence,
        "visible_default_event_ids": ["a"],
        "visible_full_event_ids": ["a", "b"],
    }
    report = {
        "report_version": "timeline-production-qualification.v2",
        "status": "qualified",
        "quality_comparable": True,
        "artifact": artifact,
        "artifact_sha256": q._sha256(artifact),
        "gates": {"production_artifacts": True, "spoiler_safety": True, "quality_thresholds": True},
        "metrics": {"event_precision": 1.0, "event_recall": 1.0, "spoiler_leaks": 0, "provider_calls": 3},
        "test_commands": q.REQUIRED_TEST_COMMANDS,
    }
    report["report_sha256"] = q.report_digest(report)
    return report


def _forged_command_results(q):
    return [
        {"command": command, "exit_code": 0, "output_sha256": "f" * 64}
        for command in q.REQUIRED_TEST_COMMANDS
    ]


def test_release_gate_rejects_copied_authority_and_forged_digests(tmp_path):
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
    assert verdict["quality_comparable"] is False
    assert verdict["checks"]["database_authority"] is True
    assert verdict["checks"]["command_output_attestation"] is False


def test_command_collector_hashes_exact_output_and_records_failure(tmp_path):
    q = _module()
    success = q.CommandSpec(
        display=q.REQUIRED_TEST_COMMANDS[0],
        cwd=tmp_path,
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'exact-output')"),
    )
    changed = q.CommandSpec(
        display=q.REQUIRED_TEST_COMMANDS[1],
        cwd=tmp_path,
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'changed-output')"),
    )
    failed = q.CommandSpec(
        display=q.REQUIRED_TEST_COMMANDS[2],
        cwd=tmp_path,
        argv=(sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'failed-output'); raise SystemExit(7)"),
    )

    results = q.collect_command_results((success, changed, failed))

    assert results[0]["exit_code"] == 0
    assert results[0]["output"] == b"exact-output"
    assert results[0]["output_sha256"] == hashlib.sha256(b"exact-output").hexdigest()
    assert results[1]["output_sha256"] != results[0]["output_sha256"]
    assert results[2]["exit_code"] == 7
    assert results[2]["output"] == b"failed-output"


def test_cli_release_mode_exposes_no_observation_or_digest_injection():
    q = _module()

    help_text = q.build_parser().format_help()

    assert "--verify-release" in help_text
    assert "--report" in help_text
    for forbidden in ("observed-authority", "command-results", "output-digest", "command-list"):
        assert forbidden not in help_text


def test_real_browser_spec_does_not_mock_timeline_api():
    source = (REPO / "frontend" / "e2e" / "timeline-real.spec.ts").read_text(encoding="utf-8")
    assert "page.route" not in source
    assert "route.fulfill" not in source
    assert "--e2e-seed-user" in source and "--e2e-resume-run" in source


@pytest.mark.parametrize("status", ["blocked_dependency", "paused_budget", "failed_policy"])
def test_release_gate_rejects_non_success_live_status(tmp_path, status):
    q = _module()
    report = _production_report(q)
    report["live"] = {"status": status, "quality_comparable": False, "metrics": None}
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO, path, observed_authority=report["artifact"]["authority"],
        command_results=_forged_command_results(q), require_live=True,
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["quality_comparable"] is False


def test_release_gate_rejects_tampered_or_spoiler_leaking_report(tmp_path):
    q = _module()
    report = _production_report(q)
    report["gates"]["spoiler_safety"] = False
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(
        REPO, path, observed_authority=report["artifact"]["authority"],
        command_results=_forged_command_results(q),
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["report_signature"] is False
    assert verdict["checks"]["spoiler_safety"] is False


def test_release_gate_rejects_self_claimed_report_without_raw_artifact(tmp_path):
    q = _module()
    report = {
        "report_version": "timeline-qualification.v1",
        "status": "qualified", "quality_comparable": True,
        "metrics": {"event_precision": 1.0, "spoiler_leaks": 0},
        "gates": {"spoiler_safety": True},
    }
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    verdict = q.verify_release_evidence(REPO, path)

    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["production_artifact_signature"] is False
    assert verdict["checks"]["test_commands"] is False


def test_self_hashed_synthetic_report_cannot_pass_without_external_observations(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "synthetic.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    verdict = q.verify_release_evidence(REPO, path)

    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["production_artifact_signature"] is True
    assert verdict["checks"]["report_signature"] is True
    assert verdict["checks"]["database_authority"] is False
    assert verdict["checks"]["command_output_attestation"] is False
