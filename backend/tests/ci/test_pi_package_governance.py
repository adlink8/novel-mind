"""CI 政策门禁：Phase 25.3 包治理静态门（REQ-AGENT-05 / D-02..D-04）。

纯文本/AST 扫描，pytest.mark.unit，秒级完成，无 DB、无网络。复制
test_narrative_memory_qualification_contract.py 的模块契约形状。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
AGENT_SERVICE = REPO / "agent-service"
GOV_LOCK = AGENT_SERVICE / "packages.lock.json"
VERIFY_SCRIPT = AGENT_SERVICE / "scripts" / "verify-lockfile.mjs"
SCAN_SCRIPT = AGENT_SERVICE / "scripts" / "scan-packages.mjs"

# D-05 权限清单必填字段
D05_FIELDS = (
    "network",
    "network_allowlist",
    "filesystem",
    "shell",
    "env",
    "secrets",
    "tools",
    "artifact_writes",
)

# 禁止出现在 agent-service 源码中的动态包安装调用；仅注释中带否决语气的行可豁免
FORBIDDEN_PI_INVOCATIONS = ("pi install", "pi update")
DENY_MARKERS = ("deny", "forbid", "never", "禁止", "否决")

# pattern-only 裁决的包：不得进入任何 package.json 依赖
PATTERN_ONLY_PACKAGES = ("@earendil-works/pi-web-ui", "@gotgenes/pi-permission-system")

# MCP 外部 allowlist 中禁止出现内部地址（D-08：MCP 永不触碰内部服务）
INTERNAL_URLS = ("127.0.0.1:8000", "localhost", ":5432")


def _iter_source_files(root: Path, skip_parts: frozenset[str]) -> list[Path]:
    """os.walk 逐层剪枝 node_modules/vendor/.git，仅返回白名单后缀文件。

    相比 rglob + 事后过滤，避免遍历数万个 node_modules 文件（秒级 → 毫秒级）。
    """
    files: list[Path] = []
    skip_dirs = {"node_modules", "vendor", ".git", "venv", "__pycache__"}
    for base, dirs, names in root.walk(on_error=lambda e: None):
        rel = base.relative_to(root)
        for part in rel.parts:
            if part in skip_dirs or part in skip_parts:
                dirs[:] = []
                break
        else:
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for name in names:
                path = base / name
                if path.suffix in {
                    ".ts",
                    ".mjs",
                    ".json",
                    ".md",
                    ".py",
                    ".tsx",
                    ".yaml",
                    ".yml",
                }:
                    files.append(path)
    return files


def test_governance_lock_manifest_shape() -> None:
    """packages.lock.json 可解析；installed=true 条目携带 verdict、permission_manifest
    与存在的 qualification_report 文件。"""
    assert GOV_LOCK.is_file(), "packages.lock.json 必须存在"
    doc = json.loads(GOV_LOCK.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert doc["generated_by"] == "25.3-01"
    assert isinstance(doc["packages"], list)
    for entry in doc["packages"]:
        if not entry.get("installed"):
            continue
        assert entry.get("verdict"), f"{entry['name']} installed=true 缺少 verdict"
        pm = entry.get("permission_manifest")
        assert pm, f"{entry['name']} installed=true 缺少 permission_manifest"
        for field in D05_FIELDS:
            assert field in pm, f"{entry['name']} permission_manifest 缺少 {field}"
        report = entry.get("qualification_report")
        assert report, f"{entry['name']} installed=true 缺少 qualification_report"
        assert (AGENT_SERVICE / report).is_file(), (
            f"{entry['name']} qualification_report 文件不存在: {report}"
        )


def test_no_dynamic_pi_install_update_in_sources() -> None:
    """agent-service 源码禁止动态 pi install / pi update；仅注释中带否决语气的行可豁免。"""
    hits: list[str] = []
    for path in _iter_source_files(AGENT_SERVICE, frozenset()):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            lowered = line.lower()
            if not any(frag in lowered for frag in FORBIDDEN_PI_INVOCATIONS):
                continue
            if any(marker in lowered for marker in DENY_MARKERS):
                continue  # 注释/说明中明确禁止该调用
            hits.append(f"{path.relative_to(AGENT_SERVICE)}:{lineno}: {line.strip()}")
    assert not hits, "发现动态 pi install/pi update 可执行调用:\n" + "\n".join(hits)


def test_pattern_only_packages_not_in_any_package_json() -> None:
    """pattern-only 裁决的包（D-03）不得出现在仓库任何 package.json 的依赖中。"""
    offenders: list[str] = []
    skip_dirs = {
        "node_modules",
        ".git",
        "venv",
        "__pycache__",
        ".claude",
        ".venv",
        ".pytest_cache",
        "coverage",
    }
    for base, dirs, names in REPO.walk(on_error=lambda e: None):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        if "package.json" not in names:
            continue
        text = (base / "package.json").read_text(encoding="utf-8", errors="ignore")
        for pkg in PATTERN_ONLY_PACKAGES:
            if pkg in text:
                offenders.append(
                    f"{(base / 'package.json').relative_to(REPO)} 引用 {pkg}"
                )
    assert not offenders, "\n".join(offenders)


def test_mcp_allowlist_has_no_internal_urls() -> None:
    """packages.lock.json 的 network_allowlist 值禁止内部地址（D-08）。"""
    doc = json.loads(GOV_LOCK.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for entry in doc["packages"]:
        pm = entry.get("permission_manifest") or {}
        for host in pm.get("network_allowlist", []):
            for internal in INTERNAL_URLS:
                if internal in str(host):
                    offenders.append(f"{entry['name']} allowlist 含内部地址 {host!r}")
    assert not offenders, "\n".join(offenders)


def test_prohibited_from_canon_false_forbidden() -> None:
    """agent-service 与 backend 源树中禁止 `prohibited_from_canon: false`（D-09 恒定 true）。"""
    needle = re.compile(r"prohibited_from_canon\s*[:=]\s*(false|False)")
    offenders: list[str] = []
    # 仅扫描源树；排除本测试文件自身（其源码含该正则字面量）、venv/node_modules 等
    self_file = Path(__file__).resolve()
    for root in (
        AGENT_SERVICE,
        REPO / "backend" / "app",
        REPO / "backend" / "tests",
        REPO / "backend" / "scripts",
    ):
        for path in _iter_source_files(root, frozenset({"tests"})):
            if path.resolve() == self_file:
                continue
            if needle.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(REPO)))
    assert not offenders, "禁止 prohibited_from_canon=false:\n" + "\n".join(offenders)


def test_governance_scripts_exist() -> None:
    """两个治理脚本必须存在，供 CI workflow 直接执行。"""
    assert VERIFY_SCRIPT.is_file()
    assert SCAN_SCRIPT.is_file()
    assert "node" in VERIFY_SCRIPT.read_text(encoding="utf-8", errors="ignore")
