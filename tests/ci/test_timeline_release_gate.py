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


def test_release_gate_requires_phase08_migration_api_frontend_fixture_and_report(tmp_path):
    q = _module()
    report = q.run_offline_qualification()
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path)
    assert verdict["status"] == "qualified", verdict
    assert verdict["quality_comparable"] is True
    assert all(verdict["checks"].values())


@pytest.mark.parametrize("status", ["blocked_dependency", "paused_budget", "failed_policy"])
def test_release_gate_rejects_non_success_live_status(tmp_path, status):
    q = _module()
    report = q.run_offline_qualification()
    report["live"] = {"status": status, "quality_comparable": False, "metrics": None}
    report["report_sha256"] = q.report_digest(report)
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path, require_live=True)
    assert verdict["status"] == "blocked_release"
    assert verdict["quality_comparable"] is False


def test_release_gate_rejects_tampered_or_spoiler_leaking_report(tmp_path):
    q = _module()
    report = q.run_offline_qualification()
    report["gates"]["spoiler_safety"] = False
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    verdict = q.verify_release_evidence(REPO, path)
    assert verdict["status"] == "blocked_release"
    assert verdict["checks"]["report_signature"] is False
    assert verdict["checks"]["spoiler_safety"] is False

