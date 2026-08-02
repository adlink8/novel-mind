#!/usr/bin/env python3
"""Agent Runtime 执行门禁（Phase 25.3 模式）——共享 fail-closed 预检。

Phase 25.3 包治理工作（25.3-01..05）在写入任何 package/lockfile/runtime 文件前，
必须先通过本门禁：校验 Phase 25.2 的权威验证工件 25.2-VERIFICATION.md 真实存在、
front-matter 的 phase/slug/status/source_commit 与期望一致、且必填 validation 段落齐全。

- 缺失 / 过期 / 畸形 / status 非 passed / phase id 不匹配 → exit 1，并在 stderr 点名原因。
- source_commit 校验：显式 --expected-commit 时严格匹配；默认要求 source_commit 是
  当前 git HEAD 的祖先（已验证并并入当前历史的提交）。伪造或未合并的旧提交被拒绝，
  但验证之后的 docs/后续 plan 提交推进 HEAD 不会误判为过期。
- planning override（.planning/STATE.md）与任何 SUMMARY 都不能替代 VERIFICATION：
  本脚本只读取 VERIFICATION 工件，其余文档一概不作为证据。
- Phase 22 门禁已由用户授权执行跳过（STATE.md "Execution Override (2026-08-02)"），
  本门禁不代偿该跳过，仅验证 25.2 验证工件本身。

用法:
    python scripts/check_agent_runtime_execution_gate.py \
        --phase 25.3 [--repo-root <repo>] [--verification <path>] [--expected-commit <commit>]

退出码: 0 = 通过；1 = 阻断（失败即关闭，无默认放行）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_PHASE = "25.2"
EXPECTED_SLUG = "embedded-novel-agent-runtime"
EXPECTED_STATUS = "passed"
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
# 必填 validation 段落：逐 plan 的结果表 + 结论段
REQUIRED_SECTIONS = ("## Verification Results", "## Conclusion")
PASSED_TOKEN_RE = re.compile(r"(?i)passed|PASS")
PLAN_ROW_RE = re.compile(r"^\s*\|")


def _fail(message: str) -> int:
    print(f"[gate:25.3] BLOCKED: {message}", file=sys.stderr)
    return 1


def _ok(message: str) -> None:
    print(f"[gate:25.3] PASS: {message}", file=sys.stderr)


def parse_front_matter(text: str) -> dict[str, str]:
    """解析 --- 包裹的极简 YAML front-matter（key: value 对）。"""
    lines = text.splitlines()
    if len(lines) < 3 or not lines[0].strip() == "---":
        return {}
    fields: dict[str, str] = {}
    for raw in lines[1:]:
        stripped = raw.strip()
        if stripped == "---":
            break
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def git_head(repo_root: Path) -> tuple[str, str] | None:
    """解析当前仓库 HEAD。无提交（fresh repo）时返回 None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        full = proc.stdout.strip()
        short = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return full, short
    except (OSError, subprocess.SubprocessError):
        return None


def commit_is_ancestor(repo_root: Path, source_commit: str) -> bool:
    """source_commit 是否已是 HEAD 的祖先（已并入当前历史）。

    语义：验证证据必须来自当前仓库历史中可审计的提交。相比"严格等于 HEAD"，
    祖先检查允许验证后的 docs/后续 plan 提交推进 HEAD，而不会把合法证据
    误判为过期；伪造/未合并分支上的旧提交仍被拒绝。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", source_commit, "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def check_verification(
    verification_path: Path,
    repo_root: Path,
    expected_commit: str | None,
) -> list[str]:
    """校验 25.2-VERIFICATION.md，返回错误清单（空 = 通过）。"""
    errors: list[str] = []

    if not verification_path.is_file():
        errors.append(
            f"25.2-VERIFICATION.md 不存在: {verification_path}"
        )
        return errors

    text = verification_path.read_text(encoding="utf-8")
    fm = parse_front_matter(text)

    if not fm:
        errors.append("front-matter 缺失或畸形（需 --- 包裹的 key: value 块）")
        return errors

    # phase id 匹配
    if fm.get("phase") != EXPECTED_PHASE:
        errors.append(
            f"phase id 不匹配: 期望 {EXPECTED_PHASE}，实际 {fm.get('phase', '<缺失>')}"
        )
    if fm.get("slug") != EXPECTED_SLUG:
        errors.append(
            f"slug 不匹配: 期望 {EXPECTED_SLUG}，实际 {fm.get('slug', '<缺失>')}"
        )

    # status 必须为 passed
    if fm.get("status") != EXPECTED_STATUS:
        errors.append(
            f"status 非 passed: 实际 {fm.get('status', '<缺失>')}"
        )

    # source commit 匹配 / 过期检测
    source_commit = fm.get("source_commit")
    if not source_commit or not COMMIT_RE.match(source_commit):
        errors.append(f"source_commit 缺失或畸形: {source_commit!r}")
    else:
        if expected_commit:
            # 显式期望：严格匹配（用于 fixture 与审计）
            if source_commit != expected_commit:
                errors.append(
                    f"source_commit 过期/不匹配: VERIFICATION={source_commit}，"
                    f"期望={expected_commit}"
                )
        elif repo_root.is_dir() and (repo_root / ".git").exists():
            # 默认（无 --expected-commit）：必须是当前仓库 HEAD 的祖先——
            # 验证证据来自已并入历史、可审计的提交。伪造/未合并提交被拒绝。
            if not commit_is_ancestor(repo_root, source_commit):
                head = git_head(repo_root)
                errors.append(
                    f"source_commit 不在当前历史: VERIFICATION={source_commit}，"
                    f"HEAD={head[1] if head else '<无>'}"
                )

    # 必填 validation 段落齐全
    body = text
    for section in REQUIRED_SECTIONS:
        if section not in body:
            errors.append(f"缺少必填段落: {section}")
    if "## Verification Results" in body:
        rows = [
            line for line in body.splitlines()
            if PLAN_ROW_RE.match(line) and "|" in line
        ]
        if not any(PASSED_TOKEN_RE.search(line) for line in rows):
            errors.append("Verification Results 表内没有任何 passed/PASS 结果行")
    if "## Conclusion" in body:
        conclusion = body.split("## Conclusion", 1)[1]
        if not PASSED_TOKEN_RE.search(conclusion):
            errors.append("Conclusion 段落没有 passed 结论")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Runtime 执行门禁")
    parser.add_argument("--phase", choices=("25.3",), default="25.3",
                        help="门禁模式（当前支持 25.3）")
    parser.add_argument("--repo-root", type=Path, default=None,
                        help="仓库根目录（默认取脚本上上级）")
    parser.add_argument("--verification", type=Path, default=None,
                        help="25.2-VERIFICATION.md 路径（默认按仓库约定）")
    parser.add_argument("--expected-commit", default=None,
                        help="期望的 source commit（默认取 git HEAD）")
    args = parser.parse_args(argv)

    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).resolve()
    verification = (
        args.verification
        or repo_root
        / ".planning"
        / "phases"
        / "25.2-embedded-novel-agent-runtime"
        / "25.2-VERIFICATION.md"
    ).resolve()

    errors = check_verification(verification, repo_root, args.expected_commit)
    if errors:
        for message in errors:
            _fail(message)
        return 1
    _ok("Phase 25.2 VERIFICATION 存在且 passed，phase/slug/source_commit/段落均匹配")
    return 0


if __name__ == "__main__":
    sys.exit(main())
