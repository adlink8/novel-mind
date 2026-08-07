#!/usr/bin/env python3
"""
Coverage policy evaluator shared by CI and contract tests (D-09).

Parses Cobertura XML (backend) and LCOV (frontend) into per-file line/branch
coverage, computes overall / critical / changed-line coverage, and evaluates
against `.quality/coverage-policy.yml` fail-closed.

Reusable entry points:
  - `evaluate_coverage_report(policy, report)` — shared with
    `backend/tests/test_test_policy.py` (single implementation source).
  - CLI `main()` — wired into `.github/workflows/ci.yml` as the real gate.

Exit code 0 on pass, 1 on policy violation, 2 on usage/parse error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import yaml

# Repository root: scripts/ci/coverage_policy.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / ".quality" / "coverage-policy.yml"
SCHEMA_PATH = REPO_ROOT / ".quality" / "coverage-policy.schema.json"


class PolicyError(Exception):
    """Fail-closed quality policy violation."""


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise PolicyError(f"Policy must be a mapping: {path}")
    return data


def load_schema() -> dict[str, Any]:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def validate_schema(
    policy: dict[str, Any], schema: dict[str, Any] | None = None
) -> None:
    from jsonschema import Draft202012Validator

    schema = schema or load_schema()
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(policy), key=lambda e: list(e.path))
    if errors:
        messages = "; ".join(e.message for e in errors[:5])
        raise PolicyError(f"Policy schema validation failed: {messages}")


def resolve_side_globs(side_root: Path, globs: list[str]) -> list[Path]:
    """Resolve coverage globs relative to backend/ or frontend/ package root."""
    hits: list[Path] = []
    for pattern in globs:
        direct = side_root / pattern
        if direct.is_file():
            hits.append(direct)
            continue
        hits.extend(p for p in side_root.glob(pattern) if p.is_file())
    return sorted({p.resolve() for p in hits})


def assert_critical_globs_hit(
    policy: dict[str, Any], repo_root: Path = REPO_ROOT
) -> None:
    """Critical coverage globs must resolve to at least one file (zero hits → fail)."""
    for side in ("backend", "frontend"):
        globs = policy["coverage"][side]["critical"]["globs"]
        hits = resolve_side_globs(repo_root / side, globs)
        if not hits:
            raise PolicyError(
                f"Critical coverage globs for {side} matched zero files: {globs}"
            )


def evaluate_line_branch(
    label: str,
    measured: dict[str, float],
    required: dict[str, float],
) -> list[str]:
    failures: list[str] = []
    for key in ("line", "branch"):
        if measured.get(key, 0) < required[key]:
            failures.append(
                f"{label} {key} coverage {measured.get(key, 0)} < required {required[key]}"
            )
    return failures


def evaluate_coverage_report(
    policy: dict[str, Any],
    report: dict[str, Any],
) -> None:
    """
    Evaluate a coverage summary report against policy.

    report shape:
      {
        "backend": {"overall": {"line": n, "branch": n},
                    "critical": {"line": n, "branch": n},
                    "critical_files_hit": int},
        "frontend": {...},
        "diff_coverage": n
      }
    """
    failures: list[str] = []
    cov = policy["coverage"]

    for side in ("backend", "frontend"):
        measured_side = report.get(side) or {}
        overall_req = cov[side]["overall"]
        critical_req = {
            "line": cov[side]["critical"]["line"],
            "branch": cov[side]["critical"]["branch"],
        }
        failures.extend(
            evaluate_line_branch(
                f"{side}.overall",
                measured_side.get("overall") or {},
                overall_req,
            )
        )
        failures.extend(
            evaluate_line_branch(
                f"{side}.critical",
                measured_side.get("critical") or {},
                critical_req,
            )
        )
        if int(measured_side.get("critical_files_hit", 0)) <= 0:
            failures.append(f"{side}.critical globs produced zero file hits")

    diff_req = cov["diff_coverage"]["minimum"]
    diff_val = report.get("diff_coverage")
    # diff coverage is only evaluable when a change baseline is provided
    # (PR/push against a base); otherwise it is skipped, not treated as 0.
    if diff_val is not None and float(diff_val) < diff_req:
        failures.append(f"diff_coverage {diff_val} < required {diff_req}")

    if failures:
        raise PolicyError("Coverage policy failed:\n  - " + "\n  - ".join(failures))


def evaluate_flake(
    policy: dict[str, Any], pr_flake_count: int, infra_retries: int
) -> None:
    if pr_flake_count > policy["flake"]["pr_max"]:
        raise PolicyError(
            f"PR flake count {pr_flake_count} exceeds pr_max={policy['flake']['pr_max']}"
        )
    max_retry = policy["flake"]["external_infra"]["max_retry"]
    if infra_retries > max_retry:
        raise PolicyError(
            f"external infra retries {infra_retries} exceed max_retry={max_retry}"
        )
    if not policy["flake"]["external_infra"]["save_first_failure_evidence"]:
        raise PolicyError("external_infra.save_first_failure_evidence must be true")


# ── Parsers ─────────────────────────────────────────────────────────────────


class FileCoverage:
    """Per-file coverage with line-level hit map."""

    __slots__ = ("line_hits", "branches_total", "branches_hit")

    def __init__(
        self,
        branches_total: int = 0,
        branches_hit: int = 0,
    ) -> None:
        self.line_hits: dict[int, int] = {}
        self.branches_total = branches_total
        self.branches_hit = branches_hit

    def add_line(self, number: int, hits: int) -> None:
        self.line_hits[number] = self.line_hits.get(number, 0) + max(hits, 0)

    def add_branch(self, hits: int) -> None:
        self.branches_total += 1
        if hits > 0:
            self.branches_hit += 1

    @property
    def lines_total(self) -> int:
        return len(self.line_hits)

    @property
    def lines_hit(self) -> int:
        return sum(1 for v in self.line_hits.values() if v > 0)


def _pct(hit: int, total: int) -> float:
    return round(100.0 * hit / total, 2) if total else 0.0


def _norm_side(name: str, prefix: str) -> str:
    """Normalize report file paths to repo-root-relative keys.

    Cobertura XML records filenames relative to the measured source root
    (`--cov=app` → `core/security.py`); LCOV records them relative to the
    frontend root (`src/lib/api.ts`). Policy globs and `git diff` paths are
    repo-root-relative (`app/core/security.py`, `frontend/src/lib/api.ts`),
    so we prefix when missing to keep one consistent key space.
    """
    if name.startswith(prefix):
        return name
    return f"{prefix}{name}"


def parse_cobertura(path: Path) -> dict[str, FileCoverage]:
    """Parse pytest-cov Cobertura XML into {repo-root-relative: FileCoverage}."""
    root = ET.parse(path).getroot()
    out: dict[str, FileCoverage] = {}
    for cls in root.iter("class"):
        filename = cls.attrib.get("filename") or ""
        if not filename:
            continue
        fc = FileCoverage()
        for line in cls.iter("line"):
            number = int(line.attrib.get("number", 0))
            hits = int(line.attrib.get("hits", 0))
            if line.attrib.get("branch") == "true":
                fc.add_branch(hits)
            elif number > 0:
                fc.add_line(number, hits)
        out[_norm_side(filename, "app/")] = fc
    return out


_LCOV_DA = re.compile(r"^DA:(\d+),(\d+)$")


def parse_lcov(path: Path) -> dict[str, FileCoverage]:
    """Parse LCOV (frontend v8) into {repo-root-relative: FileCoverage}."""
    out: dict[str, FileCoverage] = {}
    current: str | None = None
    fc: FileCoverage | None = None
    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if line.startswith("SF:"):
                current = line[3:]
                fc = FileCoverage()
            elif line.startswith("DA:") and fc is not None:
                m = _LCOV_DA.match(line)
                if m:
                    fc.add_line(int(m.group(1)), int(m.group(2)))
            elif line.startswith("BRF:") and fc is not None:
                fc.branches_total = int(line[4:])
            elif line.startswith("BRH:") and fc is not None:
                fc.branches_hit = int(line[4:])
            elif line == "end_of_record" and current and fc is not None:
                out[current] = fc
                current = None
                fc = None
    return out


def _aggregate(files: dict[str, FileCoverage]) -> dict[str, float]:
    lines_total = sum(fc.lines_total for fc in files.values())
    lines_hit = sum(fc.lines_hit for fc in files.values())
    branches_total = sum(fc.branches_total for fc in files.values())
    branches_hit = sum(fc.branches_hit for fc in files.values())
    return {
        "line": _pct(lines_hit, lines_total),
        "branch": _pct(branches_hit, branches_total),
    }


def _norm_git_path(name: str) -> str:
    """Strip repo-root prefixes from git diff paths to report key space.

    `git diff` emits `backend/app/foo.py` / `frontend/src/foo.ts`; report keys
    are `app/foo.py` (Cobertura, prefixed in parse) and `src/foo.ts` (LCOV).
    """
    for prefix in ("backend/", "frontend/"):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _critical_subset(
    files: dict[str, FileCoverage],
    policy: dict[str, Any],
    side: str,
) -> tuple[dict[str, float], int]:
    globs = policy["coverage"][side]["critical"]["globs"]
    side_root = REPO_ROOT / side
    critical_paths = {p.resolve() for p in resolve_side_globs(side_root, globs)}
    subset = {
        name: fc for name, fc in files.items() if (side_root / name).resolve() in critical_paths
    }
    return _aggregate(subset), len(subset)


def parse_changed_lines(diff_text: str) -> dict[str, set[int]]:
    """
    Parse `git diff --unified=0` output into {filename: {new_line_numbers}}.
    Only added/modified lines (the `+` side) are considered changed.
    """
    changed: dict[str, set[int]] = {}
    current_file: str | None = None
    hunk_line = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    new_start = 0
    new_offset = 0
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git"):
            current_file = None
            continue
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
            changed.setdefault(current_file, set())
            continue
        if current_file is None:
            continue
        m = hunk_line.match(raw)
        if m:
            new_start = int(m.group(1))
            new_offset = 0
            continue
        if raw.startswith("+"):
            changed[current_file].add(new_start + new_offset)
            new_offset += 1
        elif raw.startswith("-"):
            pass  # removed line: no new-file number
        elif raw.startswith("\\"):
            pass  # "\ No newline at end of file" marker
        else:
            new_offset += 1  # context line occupies a new-file line number
    return changed


def diff_coverage(
    files: dict[str, FileCoverage],
    changed: dict[str, set[int]],
) -> tuple[float, int, int]:
    """Compute changed-line coverage across the given file map."""
    covered = 0
    total = 0
    for name, lines in changed.items():
        fc = files.get(_norm_git_path(name))
        if fc is None:
            continue  # unreported/uncovered file: not counted
        for n in lines:
            if n <= 0:
                continue
            total += 1
            if fc.line_hits.get(n, 0) > 0:
                covered += 1
    return _pct(covered, total), covered, total


def build_report(
    policy: dict[str, Any],
    backend_files: dict[str, FileCoverage],
    frontend_files: dict[str, FileCoverage],
    changed: dict[str, set[int]] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for side, files in (("backend", backend_files), ("frontend", frontend_files)):
        critical, hits = _critical_subset(files, policy, side)
        report[side] = {
            "overall": _aggregate(files),
            "critical": critical,
            "critical_files_hit": hits,
        }
    if changed:
        # Merge changed lines across backend+frontend into one overall metric.
        merged: dict[str, FileCoverage] = {**backend_files, **frontend_files}
        report["diff_coverage"], _, _ = diff_coverage(merged, changed)
    else:
        report["diff_coverage"] = None
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate real coverage reports against .quality/coverage-policy.yml"
    )
    parser.add_argument("--policy", default=str(POLICY_PATH))
    parser.add_argument("--backend-xml", default="backend/artifacts/backend-coverage.xml")
    parser.add_argument("--frontend-lcov", default="frontend/coverage/lcov.info")
    parser.add_argument(
        "--changed-lines",
        default=None,
        help="Path to `git diff --unified=0` output for changed-line coverage; optional",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print PASS/FAIL summary and gaps but always exit 0. Use to surface "
        "real coverage while thresholds are not yet met; switch to hard-gate "
        "mode by removing this flag once coverage reaches policy levels.",
    )
    args = parser.parse_args(argv)

    try:
        policy = load_yaml(Path(args.policy))
        validate_schema(policy)

        backend_xml = Path(args.backend_xml)
        if not backend_xml.is_file():
            raise PolicyError(f"Backend coverage XML not found: {backend_xml}")
        backend_files = parse_cobertura(backend_xml)

        frontend_lcov = Path(args.frontend_lcov)
        if not frontend_lcov.is_file():
            raise PolicyError(f"Frontend LCOV not found: {frontend_lcov}")
        frontend_files = parse_lcov(frontend_lcov)

        changed: dict[str, set[int]] | None = None
        if args.changed_lines:
            diff_path = Path(args.changed_lines)
            if diff_path.is_file():
                changed = parse_changed_lines(
                    diff_path.read_text(encoding="utf-8", errors="replace")
                )

        report = build_report(policy, backend_files, frontend_files, changed)
        try:
            evaluate_coverage_report(policy, report)
            status = "PASS"
        except PolicyError as exc:
            status = "FAIL (report-only)" if args.report_only else "FAIL"
            print(f"Coverage gaps: {exc}", file=sys.stderr)
            if not args.report_only:
                return 1

        print(
            f"Coverage gate {status}: "
            f"backend line {report['backend']['overall']['line']}% "
            f"branch {report['backend']['overall']['branch']}%; "
            f"frontend line {report['frontend']['overall']['line']}% "
            f"branch {report['frontend']['overall']['branch']}%; "
            f"critical files backend {report['backend']['critical_files_hit']} "
            f"frontend {report['frontend']['critical_files_hit']}"
        )
        if changed is not None:
            print(
                f"diff_coverage {report['diff_coverage']}% "
                f"(required {policy['coverage']['diff_coverage']['minimum']}%)"
            )
        return 0
    except PolicyError as exc:
        print(f"Coverage gate FAIL: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Coverage gate ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
