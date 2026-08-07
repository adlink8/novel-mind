"""CI 门禁：Phase 25.3 上游验证（25.2-VERIFICATION）必须真实存在且 passed。

只有权威的、匹配的、已 passed 的 25.2-VERIFICATION.md 才返回 0；缺失、畸形、
过期（commit 不匹配）、wrong-phase、非 passed、planning override 与 SUMMARY
都不能替代 VERIFICATION（fail-closed）。无 DB、无网络、纯文件/子进程断言。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
GATE = REPO / "scripts" / "check_agent_runtime_execution_gate.py"
REAL_VERIFICATION = (
    REPO
    / ".planning"
    / "phases"
    / "25.2-embedded-novel-agent-runtime"
    / "25.2-VERIFICATION.md"
)

GOOD_BODY = """\
# Phase 25.2 — Verification

> Independent evidence that Phase 25.2 implementation satisfies its plans.

## Verification Results

| Plan | What was verified | Command | Result |
|---|---|---|---|
| 25.2-05 agent service | registry/session-factory/loader/server | `cd agent-service && npx vitest run` | 66 passed |

## Known Non-Blocking Items

- none.

## Conclusion

Phase 25.2 implementation is verified against its plans. Verdict: **passed**.
"""


def make_verification(
    directory: Path,
    *,
    phase: str = "25.2",
    slug: str = "embedded-novel-agent-runtime",
    status: str = "passed",
    source_commit: str = "abc1234",
    body: str | None = None,
) -> Path:
    front_matter = (
        "---\n"
        f"phase: {phase}\n"
        f"slug: {slug}\n"
        f"status: {status}\n"
        "verified_at: 2026-08-02\n"
        f"source_commit: {source_commit}\n"
        "---\n\n"
    )
    path = directory / "25.2-VERIFICATION.md"
    path.write_text(
        front_matter + (body if body is not None else GOOD_BODY), encoding="utf-8"
    )
    return path


def run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--phase", "25.3", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_current_repo_verification_passes() -> None:
    """当前仓库 25.2-VERIFICATION 存在且 passed → gate 返回 0。"""
    assert GATE.is_file(), "gate 脚本必须存在"
    assert REAL_VERIFICATION.is_file(), "25.2-VERIFICATION.md 必须存在"
    result = run_gate()
    assert result.returncode == 0, result.stderr


def test_synthetic_complete_evidence_passes(tmp_path: Path) -> None:
    """合成完整证据（phase/slug/status/commit/段落齐全）→ 返回 0。"""
    path = make_verification(tmp_path, source_commit="f00b4a1")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "f00b4a1",
    )
    assert result.returncode == 0, result.stderr


def test_missing_verification_fails(tmp_path: Path) -> None:
    """VERIFICATION 缺失 → 稳定非零。"""
    result = run_gate(
        "--verification",
        str(tmp_path / "25.2-VERIFICATION.md"),
        "--repo-root",
        str(tmp_path),
    )
    assert result.returncode != 0
    assert "不存在" in result.stderr


def test_malformed_front_matter_fails(tmp_path: Path) -> None:
    """front-matter 畸形（无 --- 包裹/无字段）→ 非零。"""
    path = tmp_path / "25.2-VERIFICATION.md"
    path.write_text("status: passed\nphase: 25.2\n" + GOOD_BODY, encoding="utf-8")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "abc1234",
    )
    assert result.returncode != 0
    assert "front-matter" in result.stderr


def test_non_passed_status_fails(tmp_path: Path) -> None:
    """status 非 passed → 非零。"""
    path = make_verification(tmp_path, status="blocked")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "abc1234",
    )
    assert result.returncode != 0
    assert "非 passed" in result.stderr


def test_wrong_phase_fails(tmp_path: Path) -> None:
    """phase id 不匹配（25.1）→ 非零。"""
    path = make_verification(tmp_path, phase="25.1", slug="analysis-chat-workspace")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "abc1234",
    )
    assert result.returncode != 0
    assert "phase id 不匹配" in result.stderr


def test_stale_commit_fails(tmp_path: Path) -> None:
    """source_commit 与期望提交不一致（过期证据）→ 非零并点名。"""
    path = make_verification(tmp_path, source_commit="deadbee")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "f00b4a1",
    )
    assert result.returncode != 0
    assert "deadbee" in result.stderr and "f00b4a1" in result.stderr


def test_missing_required_sections_fails(tmp_path: Path) -> None:
    """必填 validation 段落缺失（无 Verification Results/Conclusion）→ 非零。"""
    path = make_verification(tmp_path, body="# 只有标题\n没有表格也没有结论。\n")
    result = run_gate(
        "--verification",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--expected-commit",
        "abc1234",
    )
    assert result.returncode != 0
    assert "必填段落" in result.stderr


def test_planning_override_and_summary_cannot_substitute(tmp_path: Path) -> None:
    """planning override（STATE.md）与 SUMMARY 不能替代 VERIFICATION。"""
    planning = tmp_path / ".planning"
    phases = planning / "phases" / "25.2-embedded-novel-agent-runtime"
    phases.mkdir(parents=True)
    (planning / "STATE.md").write_text(
        "Execution Override: user authorized skipping Phase 22 gate 2026-08-02.\n",
        encoding="utf-8",
    )
    (phases / "25.2-00-SUMMARY.md").write_text(
        "Phase 25.2-00 completed.\n",
        encoding="utf-8",
    )
    # 只有 override 与 SUMMARY，没有 VERIFICATION 工件
    result = run_gate(
        "--verification",
        str(phases / "25.2-VERIFICATION.md"),
        "--repo-root",
        str(tmp_path),
    )
    assert result.returncode != 0
    assert "不存在" in result.stderr
