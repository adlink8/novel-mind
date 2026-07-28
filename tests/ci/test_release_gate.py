"""Phase 06 release gate verifier tests (06-07)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.contract


def _load_release_gate():
    path = REPO_ROOT / "scripts" / "ci" / "verify-release-gate.py"
    spec = importlib.util.spec_from_file_location("verify_release_gate", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rg = _load_release_gate()


def test_summaries_01_through_06_present() -> None:
    checks = rg.check_summaries()
    by_name = {c.name: c for c in checks}
    for plan in ("06-01", "06-02", "06-03", "06-04", "06-05", "06-06"):
        assert plan in by_name
        assert by_name[plan].ok is True, by_name[plan].detail


def test_required_artifacts_01_through_06() -> None:
    checks = rg.check_required_artifacts()
    by_name = {c.name: c for c in checks}
    for plan in ("06-01", "06-02", "06-03", "06-04", "06-05", "06-06"):
        key = f"artifacts:{plan}"
        assert by_name[key].ok is True, by_name[key].detail


def test_06_07_artifacts_present() -> None:
    checks = rg.check_required_artifacts()
    by_name = {c.name: c for c in checks}
    assert by_name["artifacts:06-07"].ok is True, by_name["artifacts:06-07"].detail


def test_signed_and_policy_files() -> None:
    checks = rg.check_signed_and_policy_files()
    failures = [c for c in checks if not c.ok]
    assert failures == [], failures


def test_workflow_has_ci_gate() -> None:
    checks = rg.check_workflow_ci_gate()
    by_name = {c.name: c for c in checks}
    assert by_name["workflow:ci-gate"].ok is True, by_name["workflow:ci-gate"].detail
    assert by_name["policy:flake"].ok is True
    assert by_name["policy:retention"].ok is True
    assert by_name["policy:timeouts"].ok is True


def test_release_gate_fails_on_blocked_external() -> None:
    verdict = rg.run_release_gate(
        protection_status="blocked_external_configuration",
        require_06_07_summary=False,
    )
    assert verdict.ok is False
    assert verdict.blocked_external is True
    assert any("blocked_external" in f for f in verdict.failures)


def test_release_gate_fails_on_wrong_contexts() -> None:
    verdict = rg.run_release_gate(
        protection_json={
            "required_status_checks": {"contexts": ["unit", "integration"]},
        },
        require_06_07_summary=False,
    )
    assert verdict.ok is False
    assert verdict.blocked_external is False
    assert any("readback" in f or "contexts" in f for f in verdict.failures)


def test_release_gate_passes_with_injected_protection() -> None:
    """All local evidence + injected remote contexts == [ci-gate]."""
    # 06-07 SUMMARY may not exist yet during authoring; allow soft for this unit.
    # Once SUMMARY is written, full gate includes it.
    summary = (
        REPO_ROOT
        / ".planning"
        / "phases"
        / "06-automated-quality-ci"
        / "06-07-SUMMARY.md"
    )
    require = summary.is_file()
    verdict = rg.run_release_gate(
        protection_json={
            "required_status_checks": {
                "strict": True,
                "contexts": ["ci-gate"],
            }
        },
        require_06_07_summary=require,
    )
    if require:
        assert verdict.ok is True, verdict.failures
    else:
        # Without SUMMARY, 06-07 summary check fails — filter that case
        non_summary_failures = [
            f for f in verdict.failures if not f.startswith("06-07:")
        ]
        assert non_summary_failures == [], non_summary_failures


def test_cli_blocked_external_exit_code(tmp_path: Path) -> None:
    rc = rg.main(
        [
            "--protection-status",
            "blocked_external_configuration",
            "--out",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert rc == 1
    blob = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert blob["ok"] is False
    assert blob["blocked_external_configuration"] is True


def test_cli_pass_with_injected_protection(tmp_path: Path) -> None:
    summary = (
        REPO_ROOT
        / ".planning"
        / "phases"
        / "06-automated-quality-ci"
        / "06-07-SUMMARY.md"
    )
    if not summary.is_file():
        pytest.skip("06-07-SUMMARY.md not written yet")
    prot = tmp_path / "protection.json"
    prot.write_text(
        json.dumps(
            {"required_status_checks": {"contexts": ["ci-gate"]}}
        ),
        encoding="utf-8",
    )
    rc = rg.main(
        [
            "--protection-json",
            str(prot),
            "--out",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert rc == 0
    blob = json.loads((tmp_path / "verdict.json").read_text(encoding="utf-8"))
    assert blob["ok"] is True


def test_missing_artifact_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point REQUIRED check at a fake missing relative path via monkeypatch
    original = dict(rg.REQUIRED_ARTIFACTS)
    monkeypatch.setattr(
        rg,
        "REQUIRED_ARTIFACTS",
        {
            **original,
            "06-01": original["06-01"] + ("does-not-exist-06-07-probe.txt",),
        },
    )
    checks = rg.check_required_artifacts()
    art = [c for c in checks if c.name == "artifacts:06-01"][0]
    assert art.ok is False
    assert "does-not-exist-06-07-probe.txt" in art.detail
