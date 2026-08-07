"""Coverage gate CLI tests (D-09): real report parsing is a CI gate, not just a contract test."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

POLICY_PATH = REPO_ROOT / ".quality" / "coverage-policy.yml"

pytestmark = pytest.mark.contract


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load("coverage_policy", REPO_ROOT / "scripts" / "ci" / "coverage_policy.py")


# ── Fixture builders ─────────────────────────────────────────────────────────


def _cobertura_xml(
    tmp_path: Path,
    entries: list[tuple[str, list[int]]],
    branches: dict[str, tuple[int, int]] | None = None,
) -> Path:
    """Build a Cobertura XML with one class per entry.

    entries: [(filename, [line_hits...])], hits 0 means uncovered.
    branches: {filename: (hit, total)} to append branch lines.
    """
    branches = branches or {}
    classes = []
    for filename, hits in entries:
        lines = []
        for i, h in enumerate(hits):
            lines.append(f'<line number="{i + 1}" hits="{h}"/>')
        bhit, btotal = branches.get(filename, (0, 0))
        # Append branch-covered lines after statement lines.
        for b in range(btotal):
            hit = 1 if b < bhit else 0
            lines.append(
                f'<line number="{len(hits) + b + 1}" hits="{hit}" branch="true"/>'
            )
        classes.append(
            f'<class name="{Path(filename).name}" filename="{filename}">'
            f"<lines>{''.join(lines)}</lines></class>"
        )
    xml = (
        '<?xml version="1.0" ?><coverage line-rate="0" branch-rate="0" version="2.0">'
        f'<packages><package name="p"><classes>{"".join(classes)}</classes></package>'
        "</packages></coverage>"
    )
    path = tmp_path / "coverage.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def _lcov(
    tmp_path: Path,
    entries: list[tuple[str, list[int]]],
    branches: dict[str, tuple[int, int]] | None = None,
) -> Path:
    """Build an LCOV file with one record per entry."""
    branches = branches or {}
    records = []
    for filename, hits in entries:
        lines = "".join(f"DA:{i + 1},{h}\n" for i, h in enumerate(hits))
        brf, brh = branches.get(filename, (0, 0))
        records.append(
            f"SF:{filename}\n{lines}BRF:{brf}\nBRH:{brh}\nend_of_record\n"
        )
    path = tmp_path / "lcov.info"
    path.write_text("".join(records), encoding="utf-8")
    return path


# ── Parsers ──────────────────────────────────────────────────────────────────


def test_parse_cobertura_line_and_branch(tmp_path: Path):
    # Two classes: one fully covered, one fully uncovered, plus a branch line.
    xml = (
        '<?xml version="1.0" ?><coverage version="2.0"><packages><package>'
        '<classes>'
        '<class name="a" filename="app/a.py"><lines>'
        '<line number="1" hits="1"/><line number="2" hits="1"/>'
        '<line number="3" hits="0" branch="true"/>'
        "</lines></class>"
        '<class name="b" filename="app/b.py"><lines>'
        '<line number="1" hits="0"/><line number="2" hits="0"/>'
        "</lines></class>"
        "</classes></package></packages></coverage>"
    )
    path = tmp_path / "c.xml"
    path.write_text(xml, encoding="utf-8")
    files = cp.parse_cobertura(path)
    assert files["app/a.py"].lines_total == 2
    assert files["app/a.py"].lines_hit == 2
    assert files["app/a.py"].branches_total == 1
    assert files["app/a.py"].branches_hit == 0
    assert files["app/b.py"].lines_hit == 0
    agg = cp._aggregate(files)
    assert agg["line"] == 50.0  # 2/4 lines hit
    assert agg["branch"] == 0.0


def test_parse_lcov(tmp_path: Path):
    path = _lcov(
        tmp_path,
        [("src/x.ts", [1, 0, 1]), ("src/y.ts", [0, 0])],
    )
    files = cp.parse_lcov(path)
    assert files["src/x.ts"].lines_total == 3
    assert files["src/x.ts"].lines_hit == 2
    agg = cp._aggregate(files)
    assert agg["line"] == 40.0  # 2/5
    assert agg["branch"] == 0.0


# ── Changed-line diff parsing ────────────────────────────────────────────────


def test_parse_changed_lines(tmp_path: Path):
    diff = """diff --git a/app/a.py b/app/a.py
index 111..222 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1,3 +1,4 @@
 line1
+added
 line2
-removed
+newline
"""
    changed = cp.parse_changed_lines(diff)
    # hunk starts at new-line 1; context advances the counter, '+' emits a line.
    # New-file positions: context line1->1, +added->2, context line2->3,
    # -removed none, +newline->4.  Changed (added/modified) lines = {2, 4}.
    assert changed == {"app/a.py": {2, 4}}
    assert 1 not in changed["app/a.py"]
    assert 3 not in changed["app/a.py"]


def test_diff_coverage_counts_only_hit_changed_lines(tmp_path: Path):
    path = _cobertura_xml(tmp_path, [("app/a.py", [1, 0, 1])])
    files = cp.parse_cobertura(path)
    # lines 1 and 3 hit, line 2 not; changed = {1, 2} → 50%
    changed = {"app/a.py": {1, 2}}
    pct, covered, total = cp.diff_coverage(files, changed)
    assert (pct, covered, total) == (50.0, 1, 2)


# ── CLI gate ─────────────────────────────────────────────────────────────────


def test_cli_fails_on_low_coverage(tmp_path: Path):
    xml = _cobertura_xml(tmp_path, [("app/a.py", [0, 0, 0])])
    lcov = _lcov(tmp_path, [("src/x.ts", [0, 0])])
    code = cp.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--backend-xml",
            str(xml),
            "--frontend-lcov",
            str(lcov),
        ]
    )
    assert code == 1


def test_cli_passes_on_high_coverage(tmp_path: Path):
    # backend: 10 lines all hit + 10/10 branches hit on app/a.py
    #          critical app/core/security.py: 20 lines hit + 18/20 branches
    xml = _cobertura_xml(
        tmp_path,
        [
            ("app/a.py", [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]),
            ("app/core/security.py", [1] * 20),
        ],
        branches={
            "app/a.py": (10, 10),
            "app/core/security.py": (18, 20),
        },
    )
    # frontend: 15 lines hit + 12/15 branches; critical src/lib/api.ts 10 hit + 9/10
    lcov = _lcov(
        tmp_path,
        [("src/x.ts", [1] * 15), ("src/lib/api.ts", [1] * 10)],
        branches={
            "src/x.ts": (12, 15),
            "src/lib/api.ts": (9, 10),
        },
    )
    # diff: only added lines in files that are fully hit → 100%
    diff = tmp_path / "changed.diff"
    diff.write_text(
        "diff --git a/app/a.py b/app/a.py\n"
        "--- a/app/a.py\n+++ b/app/a.py\n"
        "@@ -1,3 +1,4 @@\n+new1\n+new2\n",
        encoding="utf-8",
    )
    code = cp.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--backend-xml",
            str(xml),
            "--frontend-lcov",
            str(lcov),
            "--changed-lines",
            str(diff),
        ]
    )
    assert code == 0


def test_cli_fails_when_report_missing(tmp_path: Path):
    code = cp.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--backend-xml",
            str(tmp_path / "missing.xml"),
            "--frontend-lcov",
            str(tmp_path / "missing.info"),
        ]
    )
    assert code == 1
