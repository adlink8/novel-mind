"""Phase 10 reader-chat release evidence gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "backend" / "scripts" / "run_reader_chat_qualification.py"

pytestmark = pytest.mark.contract


def _module():
    spec = importlib.util.spec_from_file_location("reader_chat_qualification", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_report(q):
    authority = {
        "conversation_count": 2,
        "message_count": 4,
        "manifest_count": 2,
        "citation_count": 2,
        "job_terminal_ok": True,
        "no_domain_writes": True,
        "spoiler_leaks": 0,
    }
    artifact = {
        "database_dialect": "postgresql",
        "authority": authority,
        "counts": authority,
    }
    artifact["artifact_sha256"] = q._sha256(
        {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    )
    report = {
        "report_version": "reader-chat-qualification.v1",
        "status": "qualified",
        "quality_comparable": True,
        "artifact": artifact,
        "gates": {
            "spoiler_safety": True,
            "no_domain_writes": True,
            "no_apply_routes": True,
        },
        "scope": {"scope_clean": True, "forbidden_hits": []},
        "browser": {
            "real_stack": True,
            "desktop": True,
            "mobile_390": True,
            "mocks_conversation_api": False,
            "provider_only_control": True,
        },
        "requirements_covered": q.REQ_CHAT_IDS,
        "test_commands": q.REQUIRED_TEST_COMMANDS,
    }
    report["report_sha256"] = q.report_digest(report)
    return report


def _forged_command_results(q):
    return [
        {"command": command, "exit_code": 0, "output_sha256": "f" * 64, "output": b"forged"}
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
        argv=(sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'exact-output')"),
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
    for forbidden in ("observed-authority", "command-results", "output-digest"):
        assert forbidden not in help_text


def test_real_browser_spec_does_not_mock_conversation_api():
    source = (REPO / "frontend" / "e2e" / "reader-chat-real.spec.ts").read_text(
        encoding="utf-8"
    )
    assert "page.route" not in source
    assert "route.fulfill" not in source
    assert "--e2e-seed-user" in source and "--e2e-complete-job" in source


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
    # Valid matching command results
    cmd = [
        {
            "command": c,
            "exit_code": 0,
            "output": b"ok",
            "output_sha256": hashlib.sha256(b"ok").hexdigest(),
        }
        for c in q.REQUIRED_TEST_COMMANDS
    ]
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=cmd,
        require_browser=True,
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["browser_real_stack"] is False


def test_release_gate_rejects_spoiler_leak_and_domain_write_flags(tmp_path):
    q = _module()
    report = _production_report(q)
    report["gates"]["spoiler_safety"] = False
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "spoiler.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    cmd = [
        {
            "command": c,
            "exit_code": 0,
            "output": b"ok",
            "output_sha256": hashlib.sha256(b"ok").hexdigest(),
        }
        for c in q.REQUIRED_TEST_COMMANDS
    ]
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=cmd,
    )
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["spoiler_safety"] is False


def test_release_gate_accepts_matching_db_and_commands(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    cmd = [
        {
            "command": c,
            "exit_code": 0,
            "output": b"green",
            "output_sha256": hashlib.sha256(b"green").hexdigest(),
        }
        for c in q.REQUIRED_TEST_COMMANDS
    ]
    verdict = q.verify_release_evidence(
        REPO,
        path,
        observed_authority=report["artifact"]["authority"],
        command_results=cmd,
    )
    assert verdict["status"] == "passed", verdict
    assert verdict["quality_comparable"] is True
    assert set(verdict["requirements"]) == set(q.REQ_CHAT_IDS)


def test_scope_scan_clean_for_reader_chat_tree():
    q = _module()
    scope = q._scope_scan(REPO)
    assert scope["scope_clean"] is True, scope


def test_api_has_no_apply_or_accept_suggestion_routes():
    api = (REPO / "backend" / "app" / "api" / "reader_chat.py").read_text(encoding="utf-8")
    assert "apply_suggestion" not in api
    assert "accept_suggestion" not in api
    assert "clue" not in api.lower() or "reader" in api.lower()
