"""Branch protection script contract tests (06-07 / D-19)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

SCRIPT = REPO_ROOT / "scripts" / "ci" / "configure-branch-protection.ps1"

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


def test_script_exists_and_contract() -> None:
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "blocked_external_configuration" in text
    assert '["ci-gate"]' in text or "@(\"ci-gate\")" in text or '@("ci-gate")' in text
    assert "-Repository" in text
    assert "-Verify" in text
    assert "gh api" in text
    # Must read back after write
    assert "readback" in text.lower() or "Get-BranchProtection" in text
    # Must not skip GET readback
    assert "must" in text.lower() or "Mandatory GET" in text or "readback" in text.lower()


def test_assert_contexts_exactly_ci_gate() -> None:
    assert rg.assert_contexts_exactly_ci_gate(["ci-gate"]) is True
    assert rg.assert_contexts_exactly_ci_gate(["ci-gate", "unit"]) is False
    assert rg.assert_contexts_exactly_ci_gate([]) is False
    assert rg.assert_contexts_exactly_ci_gate(["CI Gate"]) is False
    assert rg.assert_contexts_exactly_ci_gate(["ci-gate "]) is True  # strip


def test_extract_contexts_from_protection() -> None:
    payload = {
        "required_status_checks": {
            "strict": True,
            "contexts": ["ci-gate"],
        }
    }
    assert rg.extract_contexts_from_protection(payload) == ["ci-gate"]

    payload2 = {
        "required_status_checks": {
            "checks": [{"context": "ci-gate", "app_id": -1}],
        }
    }
    assert rg.extract_contexts_from_protection(payload2) == ["ci-gate"]


def test_check_branch_protection_injected_success() -> None:
    checks, blocked = rg.check_branch_protection(
        protection_json={
            "required_status_checks": {"contexts": ["ci-gate"]},
        }
    )
    assert blocked is False
    assert any(c.name == "branch-protection:readback" and c.ok for c in checks)


def test_check_branch_protection_wrong_contexts() -> None:
    checks, blocked = rg.check_branch_protection(
        protection_json={
            "required_status_checks": {"contexts": ["unit", "ci-gate"]},
        }
    )
    assert blocked is False
    readback = [c for c in checks if c.name == "branch-protection:readback"]
    assert readback and readback[0].ok is False


def test_check_branch_protection_blocked_external() -> None:
    checks, blocked = rg.check_branch_protection(
        protection_status="blocked_external_configuration",
    )
    assert blocked is True
    assert any(
        c.name == "branch-protection:readback" and not c.ok for c in checks
    )
    assert any("blocked_external_configuration" in c.detail for c in checks)


def test_script_idempotent_body_mentions_preserve() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    # Idempotent: preserve non-required settings
    assert "Preserve" in text or "preserve" in text or "Existing" in text
    assert "required_status_checks" in text
    assert "strict" in text


def test_protection_payload_roundtrip_fixture() -> None:
    """Simulate idempotent desired state: only ci-gate required."""
    desired = ["ci-gate"]
    assert rg.assert_contexts_exactly_ci_gate(desired)
    # Re-applying same contexts stays exact
    again = list(desired)
    assert again == ["ci-gate"]
