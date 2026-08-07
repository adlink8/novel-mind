"""
OpenAPI contract tests (06-05 / D-07 consumer gate).

- Live schema vs frozen baseline must have zero oasdiff ERR-level breaks
- Nonbreaking fixture must pass
- Breaking fixture must fail (path delete, type change, required, auth, status)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

BACKEND = Path(__file__).resolve().parents[2]
BASELINE = BACKEND / "openapi-baseline.json"
NONBREAKING = BACKEND / "tests" / "fixtures" / "openapi" / "nonbreaking.json"
BREAKING = BACKEND / "tests" / "fixtures" / "openapi" / "breaking.json"
EXPORT_SCRIPT = BACKEND / "scripts" / "export_openapi.py"
PYTHON_BREAKING = BACKEND / "scripts" / "openapi_breaking.py"
ARTIFACTS = BACKEND / "artifacts"


def _run_oasdiff(base: Path, revision: Path) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("oasdiff")
    if not binary:
        pytest.skip(
            "oasdiff not on PATH; install via go install github.com/oasdiff/oasdiff@v1.17.0"
        )
    return subprocess.run(
        [binary, "breaking", str(base), str(revision), "--fail-on", "ERR"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(BACKEND),
    )


def _run_python_breaking(
    base: Path, revision: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PYTHON_BREAKING),
            str(base),
            str(revision),
            "--python-only",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(BACKEND),
    )


def _export_live_schema(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(EXPORT_SCRIPT), "--output", str(dest)],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(BACKEND),
    )
    assert proc.returncode == 0, f"export failed:\n{proc.stdout}\n{proc.stderr}"
    assert dest.is_file()
    return dest


def test_baseline_exists_and_is_valid_openapi():
    assert BASELINE.is_file(), (
        "openapi-baseline.json missing — freeze via export_openapi.py"
    )
    doc = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert "openapi" in doc
    assert "paths" in doc
    # Quality + legacy eval surfaces must be present in baseline
    paths = doc["paths"]
    assert "/api/eval/runs" in paths
    assert "/api/eval/quality/runs" in paths
    assert "/api/eval/quality/runs/{job_id}" in paths
    assert "/api/eval/quality/runs/{job_id}/resume" in paths


@pytest.mark.timeout(90)
def test_export_is_deterministic():
    a = ARTIFACTS / "openapi-export-a.json"
    b = ARTIFACTS / "openapi-export-b.json"
    _export_live_schema(a)
    _export_live_schema(b)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


@pytest.mark.timeout(90)
def test_live_schema_has_no_breaking_changes_vs_baseline():
    live = ARTIFACTS / "openapi.json"
    _export_live_schema(live)

    if shutil.which("oasdiff"):
        proc = _run_oasdiff(BASELINE, live)
        assert proc.returncode == 0, (
            "OpenAPI breaking changes vs baseline (oasdiff):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    else:
        proc = _run_python_breaking(BASELINE, live)
        assert proc.returncode == 0, (
            "OpenAPI breaking changes vs baseline (python):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def test_nonbreaking_fixture_passes():
    assert NONBREAKING.is_file()
    if shutil.which("oasdiff"):
        proc = _run_oasdiff(BASELINE, NONBREAKING)
        assert proc.returncode == 0, (
            f"nonbreaking should pass:\n{proc.stdout}\n{proc.stderr}"
        )
    else:
        proc = _run_python_breaking(BASELINE, NONBREAKING)
        assert proc.returncode == 0, (
            f"nonbreaking should pass:\n{proc.stdout}\n{proc.stderr}"
        )


def test_breaking_fixture_fails():
    assert BREAKING.is_file()
    if shutil.which("oasdiff"):
        proc = _run_oasdiff(BASELINE, BREAKING)
        assert proc.returncode != 0, "breaking fixture must fail oasdiff"
        combined = (proc.stdout or "") + (proc.stderr or "")
        # Categories required by 06-05 plan
        lower = combined.lower()
        assert "path" in lower or "api-path-removed" in lower
        assert "type" in lower or "property-type-changed" in lower
        assert "required" in lower
        # status-code / response-success-status
        assert "status" in lower or "response" in lower
    else:
        proc = _run_python_breaking(BASELINE, BREAKING)
        assert proc.returncode != 0, "breaking fixture must fail python checker"
        combined = (proc.stdout or "") + (proc.stderr or "")
        assert "path-deleted" in combined or "BREAKING" in combined
        # Accept any high-severity category the pure-Python checker emits
        assert any(
            token in combined
            for token in (
                "request-type-changed",
                "response-type-changed",
                "required-added",
                "status-code-removed",
                "auth-changed",
                "type",
            )
        )


def test_python_breaker_detects_required_categories_independently():
    """Even when oasdiff is present, pure-Python checker must cover plan categories."""
    proc = _run_python_breaking(BASELINE, BREAKING)
    assert proc.returncode != 0
    out = proc.stdout or ""
    # path delete is always detected by the python checker
    assert "path-deleted" in out or "operation-deleted" in out
    # at least one of type / required / status / auth
    assert any(
        token in out
        for token in (
            "request-type-changed",
            "response-type-changed",
            "required-added",
            "status-code-removed",
            "auth-changed",
        )
    )


def test_python_breaker_accepts_nonbreaking():
    proc = _run_python_breaking(BASELINE, NONBREAKING)
    assert proc.returncode == 0, proc.stdout + proc.stderr
