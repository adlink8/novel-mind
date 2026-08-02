"""Phase 26-00 execution preflight 集成测试。

覆盖当前仓库 blocked、Phase 22 <3/3、非 scheduled/非 green、25.1/25.2/25.3 缺失或非
passed、malformed YAML/字段、planning override inert，以及完整证据返回 0。

约定：测试只读权威证据文件，不写入 QueryPlan、Agent Artifact、数据库或 active
pointer；全部通过 subprocess 调用 scripts/check_phase_execution_gate.py。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[4]
GATE = REPO / "scripts" / "check_phase_execution_gate.py"

LEDGER_REL = Path(".planning") / "phases" / "22-ci-nightly-gap-closure" / "22-VALIDATION.md"

GREEN_ROWS = "\n".join(
    [
        "| 1 | 30623438107 | 912ca6b | green | passed |",
        "| 2 | 30623438108 | a904d64 | green | passed |",
        "| 3 | 30623438109 | 2c4b4bb | green | passed |",
    ]
)

LEDGER_TEMPLATE = """# Phase 22 Validation Ledger

## Gap Status

| Gap | Status |
|---|---|
| 22-G3 | BLOCKED_OBSERVATION |

## Consecutive Scheduled Green Runs

| # | Run | Commit | Artifact status | Result |
|---:|---|---|---|---|
{rows}

## Verification Verdict

`BLOCKED / NOT_VERIFIED`.
"""


def make_ledger(
    root: Path,
    *,
    rows: str = GREEN_ROWS,
    section_present: bool = True,
) -> Path:
    ledger = root / LEDGER_REL
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if section_present:
        ledger.write_text(LEDGER_TEMPLATE.format(rows=rows), encoding="utf-8")
    else:
        ledger.write_text(
            "# Phase 22 Validation Ledger\n\n## Gap Status\n\n| Gap | Status |\n|---|---|\n",
            encoding="utf-8",
        )
    return ledger


def make_verification(
    root: Path,
    phase: str,
    subdir: str,
    *,
    status: str = "passed",
    phase_field: str | None = None,
    malformed: bool = False,
) -> Path:
    path = root / ".planning" / "phases" / subdir / f"{phase}-VERIFICATION.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if malformed:
        # 无 --- 包裹，front-matter 无法解析
        path.write_text(
            f"phase: {phase_field or phase}\nstatus: {status}\n# not a front-matter\n",
            encoding="utf-8",
        )
        return path
    front_matter = (
        "---\n"
        f"phase: {phase_field or phase}\n"
        f"slug: {subdir}\n"
        f"status: {status}\n"
        "verified_at: 2026-08-02\n"
        "source_commit: abc1234\n"
        "---\n\n"
        f"# Phase {phase} — Verification\n\n"
        "## Verification Results\n\n"
        "| Plan | Result |\n"
        "|---|---|\n"
        f"| {phase}-fixture | passed |\n\n"
        "## Conclusion\n\n"
        f"Verdict: **{status}**.\n"
    )
    path.write_text(front_matter, encoding="utf-8")
    return path


def make_complete_evidence(
    root: Path,
    *,
    ledger_rows: str = GREEN_ROWS,
    section_present: bool = True,
    **verification_kwargs,
) -> None:
    """构建四组证据完整且 lineage 一致的 fixture。"""
    make_ledger(root, rows=ledger_rows, section_present=section_present)
    verifications = {
        "25.1": "25.1-analysis-chat-workspace",
        "25.2": "25.2-embedded-novel-agent-runtime",
        "25.3": "25.3-pi-package-compatibility-governance",
    }
    for phase, subdir in verifications.items():
        kwargs = verification_kwargs.get(phase, {})
        make_verification(root, phase, subdir, **kwargs)


def write_config_override(root: Path) -> None:
    cfg = root / ".planning" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "gate_overrides": {
                    "phase_22_to_26": {
                        "authorized": True,
                        "scope": "planning_only_phase_25.2_to_39",
                        "preserve_phase_22_verdict": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def run_gate(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_current_repo_blocked() -> None:
    """当前仓库：Phase 22 0/3 + 25.1 非 passed → 稳定非零 blocked。"""
    assert GATE.is_file(), "gate 脚本必须存在"
    result = run_gate(REPO)
    assert result.returncode != 0, result.stderr
    assert "BLOCKED" in result.stderr
    assert "Phase 22" in result.stderr


def test_synthetic_complete_evidence_passes(tmp_path: Path) -> None:
    """四组证据完整且 lineage 一致 → 返回 0。"""
    root = tmp_path / "repo"
    make_complete_evidence(root)
    result = run_gate(root)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stderr


@pytest.mark.parametrize("rows", [
    # 仅 2 条
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | 30623438108 | a904d64 | green | passed |",
        ]
    ),
    # run pending
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | pending | a904d64 | green | passed |",
            "| 3 | 30623438109 | 2c4b4bb | green | passed |",
        ]
    ),
    # artifact status 非 green
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | 30623438108 | a904d64 | red | passed |",
            "| 3 | 30623438109 | 2c4b4bb | green | passed |",
        ]
    ),
    # result 非 green/passed
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | 30623438108 | a904d64 | green | failed |",
            "| 3 | 30623438109 | 2c4b4bb | green | passed |",
        ]
    ),
    # commit 畸形
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | 30623438108 | not-a-commit | green | passed |",
            "| 3 | 30623438109 | 2c4b4bb | green | passed |",
        ]
    ),
    # 字段不全（缺 Result 列）
    "\n".join(
        [
            "| 1 | 30623438107 | 912ca6b | green | passed |",
            "| 2 | 30623438108 | a904d64 | green |",
            "| 3 | 30623438109 | 2c4b4bb | green | passed |",
        ]
    ),
])
def test_phase22_less_than_3_or_non_green_fails(tmp_path: Path, rows: str) -> None:
    root = tmp_path / "repo"
    make_complete_evidence(root, ledger_rows=rows)
    result = run_gate(root)
    assert result.returncode != 0, result.stderr
    assert "Phase 22" in result.stderr


def test_phase22_section_missing_fails(tmp_path: Path) -> None:
    """无 Consecutive Scheduled Green Runs 段（非 scheduled 证据）→ 非零。"""
    root = tmp_path / "repo"
    make_complete_evidence(root, section_present=False)
    result = run_gate(root)
    assert result.returncode != 0
    assert "Consecutive Scheduled Green Runs" in result.stderr


@pytest.mark.parametrize(
    "phase,subdir",
    [
        ("25.1", "25.1-analysis-chat-workspace"),
        ("25.2", "25.2-embedded-novel-agent-runtime"),
        ("25.3", "25.3-pi-package-compatibility-governance"),
    ],
)
def test_verification_missing_fails(tmp_path: Path, phase: str, subdir: str) -> None:
    """任一 VERIFICATION 缺失 → 非零并点名。"""
    root = tmp_path / "repo"
    make_ledger(root)
    verifications = {
        "25.1": "25.1-analysis-chat-workspace",
        "25.2": "25.2-embedded-novel-agent-runtime",
        "25.3": "25.3-pi-package-compatibility-governance",
    }
    for other_phase, other_subdir in verifications.items():
        if other_phase != phase:
            make_verification(root, other_phase, other_subdir)
    result = run_gate(root)
    assert result.returncode != 0
    assert f"{phase} VERIFICATION.md 不存在" in result.stderr


@pytest.mark.parametrize(
    "phase,subdir",
    [
        ("25.1", "25.1-analysis-chat-workspace"),
        ("25.2", "25.2-embedded-novel-agent-runtime"),
        ("25.3", "25.3-pi-package-compatibility-governance"),
    ],
)
def test_verification_not_passed_fails(tmp_path: Path, phase: str, subdir: str) -> None:
    """任一 VERIFICATION status 非 passed → 非零并点名。"""
    root = tmp_path / "repo"
    make_ledger(root)
    verifications = {
        "25.1": "25.1-analysis-chat-workspace",
        "25.2": "25.2-embedded-novel-agent-runtime",
        "25.3": "25.3-pi-package-compatibility-governance",
    }
    for other_phase, other_subdir in verifications.items():
        make_verification(
            root,
            other_phase,
            other_subdir,
            status="blocked" if other_phase == phase else "passed",
        )
    result = run_gate(root)
    assert result.returncode != 0
    assert f"{phase} status 非 passed" in result.stderr


def test_verification_malformed_front_matter_fails(tmp_path: Path) -> None:
    """VERIFICATION front-matter 畸形（无 --- 包裹）→ 非零。"""
    root = tmp_path / "repo"
    make_ledger(root)
    verifications = {
        "25.1": "25.1-analysis-chat-workspace",
        "25.2": "25.2-embedded-novel-agent-runtime",
        "25.3": "25.3-pi-package-compatibility-governance",
    }
    for phase, subdir in verifications.items():
        make_verification(
            root,
            phase,
            subdir,
            malformed=(phase == "25.2"),
        )
    result = run_gate(root)
    assert result.returncode != 0
    assert "front-matter" in result.stderr


def test_verification_phase_id_mismatch_fails(tmp_path: Path) -> None:
    """VERIFICATION phase id 前缀不匹配（25.2 目录放了 25.3 的 phase）→ 非零。"""
    root = tmp_path / "repo"
    make_ledger(root)
    make_verification(root, "25.1", "25.1-analysis-chat-workspace")
    make_verification(root, "25.2", "25.2-embedded-novel-agent-runtime", phase_field="25.2")
    make_verification(root, "25.3", "25.3-pi-package-compatibility-governance", phase_field="25.3")
    # 篡改 25.3 的 phase id 为 25.2 → lineage 不一致
    path = (
        root
        / ".planning"
        / "phases"
        / "25.3-pi-package-compatibility-governance"
        / "25.3-VERIFICATION.md"
    )
    text = path.read_text(encoding="utf-8").replace("phase: 25.3", "phase: 25.2")
    path.write_text(text, encoding="utf-8")
    result = run_gate(root)
    assert result.returncode != 0
    assert "25.3 phase id 不匹配" in result.stderr


def test_planning_override_inert_when_blocked(tmp_path: Path) -> None:
    """config.json override 存在但证据不足 → 仍非零（override 无法放行）。"""
    root = tmp_path / "repo"
    make_ledger(root)  # 完整 3/3
    write_config_override(root)
    # 但 25.3 缺失
    make_verification(root, "25.1", "25.1-analysis-chat-workspace")
    make_verification(root, "25.2", "25.2-embedded-novel-agent-runtime")
    result = run_gate(root)
    assert result.returncode != 0
    assert "25.3 VERIFICATION.md 不存在" in result.stderr


def test_planning_override_inert_when_pass(tmp_path: Path) -> None:
    """config.json override 存在 + 证据完整 → 仍返回 0（override 不影响 verdict）。"""
    root = tmp_path / "repo"
    make_complete_evidence(root)
    write_config_override(root)
    result = run_gate(root)
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stderr
