"""Subprocess smoke coverage for every Phase 05 production CLI entrypoint."""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).parents[1]
SCRIPTS = (
    "build_narrative_units.py",
    "build_narrative_unit_index.py",
    "run_narrative_unit_eval.py",
    "promote_narrative_unit_index.py",
    "refresh_narrative_units.py",
    "reconcile_narrative_unit_index.py",
    "rollback_narrative_unit_index.py",
)


@pytest.mark.parametrize("script", SCRIPTS)
def test_documented_cli_entrypoint_executes(script):
    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / script), "--help"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()


def test_documented_build_and_rollback_dry_runs_execute():
    commands = (
        [
            sys.executable,
            "scripts/build_narrative_unit_index.py",
            "--build-id",
            "1",
            "--dry-run",
        ],
        [
            sys.executable,
            "scripts/rollback_narrative_unit_index.py",
            "--journal-id",
            "TEST",
            "--dry-run",
        ],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=BACKEND,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
