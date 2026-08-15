#!/usr/bin/env python3
"""Phase 26+ execution preflight gate (fail-closed, read-only).

Phase 26-00 建立的唯一执行前置门：只有四组权威证据同时成立才返回 0。

1. Phase 22 validation ledger
   (.planning/phases/22-ci-nightly-gap-closure/22-VALIDATION.md)
   "## Consecutive Scheduled Green Runs" 段必须包含三条真实、连续、scheduled、green
   记录（# 1/2/3），每条携带 run / commit / artifact status / result 的完整 lineage。
2-4. Phase 25.1 / 25.2 / 25.3 各自的 VERIFICATION.md 必须存在、front-matter 可解析、
   phase id 前缀匹配、且 status == "passed"。

缺失 / pending / 非 scheduled / 非 green / 字段不全 / 解析异常 / lineage 不一致
→ 在 stderr 打印稳定 blocked reason，并以非零退出。

本脚本不读取 .planning/config.json；gate_overrides 与任何 planning authorization
一律无效（override inert）。CLI 只读：不写 QueryPlan、不写 Agent Artifact、
不写数据库、不改变状态、不创建后续阶段文件。

用法:
    python scripts/check_phase_execution_gate.py [--repo-root <repo>]

退出码: 0 = 通过（Phase 26+ 解锁）；1 = 阻断（失败即关闭，无默认放行）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GATE_TAG = "phase26"
EXPECTED_STATUS = "passed"
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
PLACEHOLDERS = {"", "pending", "-", "—", "n/a", "na", "none", "null", "tbd", "待定"}
ARTIFACT_GREEN = {"green"}
RESULT_GREEN = {"passed", "pass", "green", "success", "ok"}
LEDGER_SECTION = "## Consecutive Scheduled Green Runs"

# phase id -> (phase 子目录, VERIFICATION 文件名)
VERIFICATION_FILES = {
    "25.1": ("25.1-analysis-chat-workspace", "25.1-VERIFICATION.md"),
    "25.2": ("25.2-embedded-novel-agent-runtime", "25.2-VERIFICATION.md"),
    "25.3": ("25.3-pi-package-compatibility-governance", "25.3-VERIFICATION.md"),
}
VERIFICATION_PHASES = tuple(VERIFICATION_FILES)


def _fail(message: str) -> None:
    print(f"[gate:{GATE_TAG}] BLOCKED: {message}", file=sys.stderr)


def _ok(message: str) -> None:
    print(f"[gate:{GATE_TAG}] PASS: {message}", file=sys.stderr)


def parse_front_matter(text: str) -> dict[str, str]:
    """解析 --- 包裹且闭合的极简 YAML front-matter（key: value 对）。

    必须同时存在开/闭两行 ---；否则视为畸形（无法作为权威字段证据）。
    """
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    closed = False
    for raw in lines[1:]:
        stripped = raw.strip()
        if stripped == "---":
            closed = True
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip()] = value.strip()
    if not closed:
        return {}
    return fields


def extract_ledger_rows(text: str) -> list[list[str]] | None:
    """提取 Consecutive Scheduled Green Runs 段下的数据行；段缺失返回 None。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == LEDGER_SECTION:
            start = i
            break
    if start is None:
        return None

    rows: list[list[str]] = []
    separator_re = re.compile(r":?-+:?")
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        if cells[0].startswith("#"):
            continue  # 表头
        if all(separator_re.fullmatch(c) for c in cells if c != ""):
            continue  # 分隔行（---: 等）
        rows.append(cells)
    return rows


def check_phase22_ledger(ledger_path: Path) -> list[str]:
    """校验 Phase 22 ledger，返回错误清单（空 = 通过）。"""
    if not ledger_path.is_file():
        return [f"Phase 22 validation ledger 不存在: {ledger_path}"]

    text = ledger_path.read_text(encoding="utf-8")
    rows = extract_ledger_rows(text)
    if rows is None:
        return [f"Phase 22 ledger 缺少段 {LEDGER_SECTION}（scheduled green 记录未提供）"]
    if len(rows) < 3:
        return [f"Phase 22 scheduled green 记录不足: 仅 {len(rows)}/3 条"]

    errors: list[str] = []
    for idx, row in enumerate(rows[:3]):
        number = idx + 1
        label = f"Phase 22 scheduled green 记录 #{number}"
        if len(row) < 5:
            errors.append(f"{label} 字段不全（需 #/Run/Commit/Artifact status/Result）: {row}")
            continue
        run, commit, artifact, result = row[1], row[2], row[3], row[4]
        if row[0] != str(number):
            errors.append(f"{label} 序号不连续/错位: 期望 #{number}，实际 {row[0]!r}")
        if run.strip().lower() in PLACEHOLDERS:
            errors.append(f"{label} run 缺失或 pending: {run!r}")
        elif len(run.strip()) < 3:
            errors.append(f"{label} run id 不可信: {run!r}")
        if not COMMIT_RE.match(commit.strip()):
            errors.append(f"{label} commit 缺失或非 hex: {commit!r}")
        if artifact.strip().lower() not in ARTIFACT_GREEN:
            errors.append(f"{label} artifact status 非 green: {artifact!r}")
        if result.strip().lower() not in RESULT_GREEN:
            errors.append(f"{label} result 非 green/passed: {result!r}")
    return errors


def check_verification(repo_root: Path, phase: str) -> list[str]:
    """校验单个 VERIFICATION.md，返回错误清单（空 = 通过）。"""
    subdir, filename = VERIFICATION_FILES[phase]
    path = repo_root / ".planning" / "phases" / subdir / filename
    if not path.is_file():
        return [f"{phase} VERIFICATION.md 不存在: {path}"]

    text = path.read_text(encoding="utf-8")
    fm = parse_front_matter(text)
    if not fm:
        return [f"{phase} VERIFICATION.md front-matter 缺失或畸形（需 --- 包裹且闭合的 key: value 块）"]

    errors: list[str] = []
    phase_field = fm.get("phase", "")
    if phase_field.split("-", 1)[0] != phase:
        errors.append(f"{phase} phase id 不匹配: 实际 {phase_field!r}")
    if fm.get("status") != EXPECTED_STATUS:
        errors.append(f"{phase} status 非 passed: 实际 {fm.get('status', '<缺失>')!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 26+ 执行前置门（fail-closed，只读）"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="仓库根目录（默认取脚本上上级）",
    )
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).resolve()
    ledger_path = (
        repo_root / ".planning" / "phases" / "22-ci-nightly-gap-closure" / "22-VALIDATION.md"
    )

    errors = check_phase22_ledger(ledger_path)
    for phase in VERIFICATION_PHASES:
        errors.extend(check_verification(repo_root, phase))

    if errors:
        for message in errors:
            _fail(message)
        return 1
    _ok("Phase 22 3/3 scheduled green + 25.1/25.2/25.3 VERIFICATION passed，Phase 26+ 解锁")
    return 0


if __name__ == "__main__":
    sys.exit(main())
