"""Phase 08 release evidence gate."""

from __future__ import annotations

import importlib.util
import json
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
    artifact = {
        "database_dialect": "postgresql",
        "run": {"id": 1, "status": "completed", "version_id": 2},
        "active_pointer": {"version_id": 2, "revision": 1, "manifest_checksum": "a" * 64},
        "counts": {"events": 2, "evidence_refs": 2, "model_attempts": 3, "completed_stages": 3},
        "events": [{"logical_event_id": "a"}, {"logical_event_id": "b"}],
        "attempts": [{"status": "succeeded"}] * 3,
        "visible_default_event_ids": ["a"],
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


def test_release_gate_requires_signed_production_artifacts_and_test_commands(tmp_path):
    q = _module()
    report = _production_report(q)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path)
    assert verdict["status"] == "qualified", verdict
    assert verdict["quality_comparable"] is True
    assert all(verdict["checks"].values()), verdict


@pytest.mark.parametrize("status", ["blocked_dependency", "paused_budget", "failed_policy"])
def test_release_gate_rejects_non_success_live_status(tmp_path, status):
    q = _module()
    report = _production_report(q)
    report["live"] = {"status": status, "quality_comparable": False, "metrics": None}
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path, require_live=True)
    assert verdict["status"] == "blocked_release"
    assert verdict["quality_comparable"] is False


def test_release_gate_rejects_tampered_or_spoiler_leaking_report(tmp_path):
    q = _module()
    report = _production_report(q)
    report["gates"]["spoiler_safety"] = False
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path)
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
