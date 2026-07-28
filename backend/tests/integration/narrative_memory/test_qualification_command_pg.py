"""Fixed-command qualification CLI tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.services.narrative_memory.qualification_fixtures import FIXTURES_DIR

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[3]
SCRIPT = BACKEND / "scripts" / "run_narrative_memory_qualification.py"
PY = BACKEND / ".venv" / "Scripts" / "python.exe"


def test_dry_run_qualified_exit_0():
    proc = subprocess.run(
        [
            str(PY),
            str(SCRIPT),
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--version-id",
            "1",
            "--fixture",
            str(FIXTURES_DIR / "single_book_v1.json"),
            "--policy",
            str(FIXTURES_DIR / "policy_v1.json"),
            "--acknowledge-budget",
            "--dry-run",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 2), proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["qualification_kind"] == "single_book_candidate"
    assert payload["verdict"] in ("qualified_candidate", "blocked")
    assert "output_digest" in payload
    assert len(payload["output_digest"]) == 64
    assert (
        "Does not promote" in payload["disclaimer"]
        or "promote" in payload["disclaimer"].lower()
    )
    # self-excluding digest
    body = {k: v for k, v in payload.items() if k != "output_digest"}
    from app.services.narrative_memory.qualification_contracts import stable_checksum

    assert payload["output_digest"] == stable_checksum(body)
    if payload["verdict"] == "qualified_candidate":
        assert proc.returncode == 0
    else:
        assert proc.returncode == 2


def test_missing_budget_ack_fails():
    proc = subprocess.run(
        [
            str(PY),
            str(SCRIPT),
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--version-id",
            "1",
            "--fixture",
            str(FIXTURES_DIR / "single_book_v1.json"),
            "--policy",
            str(FIXTURES_DIR / "policy_v1.json"),
            "--dry-run",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_forbidden_promote_option_rejected():
    proc = subprocess.run(
        [
            str(PY),
            str(SCRIPT),
            "--owner-id",
            "1",
            "--novel-id",
            "1",
            "--version-id",
            "1",
            "--fixture",
            str(FIXTURES_DIR / "single_book_v1.json"),
            "--policy",
            str(FIXTURES_DIR / "policy_v1.json"),
            "--acknowledge-budget",
            "--promote",
        ],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
