"""Adversarial fail-closed gates: external_evidence boundary (REQ-AGENT-06 / D-08 / D-09).

镜像 backend/tests/adversarial/test_reader_chat_boundaries.py 的形状：
  - 静态扫描：核心检索 / 证据校验模块不导入任何 mcp 相关模块；
  - 运行时：mcp:// 外部引用注入 cited answer → finalizer 校验失败（failed_validation）；
  - 运行时：把 prohibited_from_canon 写成 false 的写入被 schema 拒绝；
  - 运行时：external_evidence 向 published 的迁移被服务门禁拒绝；
  - 运行时：客户端载荷省略 prohibited_from_canon 仍持久化为 true（服务端常量）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.schemas.agent_runtime import (
    ExternalEvidenceArtifact,
)
from app.schemas.reader_chat import (
    ReaderAnswerEnvelope,
    validate_answer_against_manifest,
)
from app.services.agent_runtime import artifacts as artifact_service
from app.services.agent_runtime import finalize

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]  # backend/
AGENT_SERVICE = ROOT.parent / "agent-service"
READER_CHAT_SERVICE = ROOT / "app" / "services" / "reader_chat"
AGENT_RUNTIME_SERVICE = ROOT / "app" / "services" / "agent_runtime"
READER_CHAT_API = ROOT / "app" / "api" / "reader_chat.py"


def _iter_py_files(directory: Path):
    for path in directory.rglob("*.py"):
        if path.name == "__pycache__":
            continue
        yield path


def _import_targets(path: Path) -> set[str]:
    """AST 提取一个 .py 文件的 import / import-from 模块目标。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.add(node.module)
    return targets


# ────────────────────────── 静态：核心模块不接触 MCP ──────────────────────────


def test_backend_core_retrieval_and_evidence_validation_import_no_mcp():
    """reader_chat（核心检索）与 agent_runtime（证据校验/finalize）不导入 mcp 相关模块。"""
    offenders: list[str] = []
    scanned_dirs = (READER_CHAT_SERVICE, AGENT_RUNTIME_SERVICE)
    for directory in scanned_dirs:
        for path in _iter_py_files(directory):
            for target in _import_targets(path):
                if "mcp" in target.lower():
                    offenders.append(f"{path.relative_to(ROOT)} imports {target}")
    for target in _import_targets(READER_CHAT_API):
        if "mcp" in target.lower():
            offenders.append(f"app/api/reader_chat.py imports {target}")
    assert not offenders, "核心检索/证据校验模块不得导入 mcp 相关模块:\n" + "\n".join(
        offenders
    )


def test_agent_service_core_sources_do_not_import_mcp():
    """agent-service 除 src/mcp/ 外的核心源码不得 import 任何 mcp 相关模块。

    （工具注册表/治理元数据中出现 "pi-mcp-adapter" 字符串——如 ToolRegistryManifest
    的 mcpProxyEntry provider_package——是 D-06 声明，不是导入，放行。）
    """
    mcp_dir = AGENT_SERVICE / "src" / "mcp"
    offenders: list[str] = []
    for path in AGENT_SERVICE.joinpath("src").rglob("*.ts"):
        if mcp_dir in path.parents:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped.startswith("import"):
                continue
            lowered = stripped.lower()
            if "pi-mcp-adapter" in lowered or "mcp" in lowered:
                offenders.append(
                    f"{path.relative_to(AGENT_SERVICE)}:{lineno}: {stripped}"
                )
    assert not offenders, "agent-service 核心源码不得导入 mcp 相关模块:\n" + "\n".join(
        offenders
    )


# ────────────────────────── 运行时：finalizer 拒绝外部引用 ──────────────────────────


def test_external_ref_in_cited_answer_fails_manifest_validation():
    """mcp:// 引用进入 cited answer → validate_answer_against_manifest 拒绝。"""
    envelope = ReaderAnswerEnvelope.model_validate(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "claim sourced from external research",
                    "evidence_refs": ["mcp://external-research/1"],
                }
            ],
        }
    )
    with pytest.raises(ValueError):
        validate_answer_against_manifest(envelope, {"selection:primary"})


def test_finalize_gate_rejects_external_ref_with_stable_code():
    """finalize 路径的 _validate_artifact_evidence 对 mcp:// 引用抛错；稳定错误码为 failed_validation。"""
    envelope = {
        "type": "cited_answer",
        "evidence_refs": ["mcp://external-research/1"],
        "answer": {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "external claim",
                    "evidence_refs": ["mcp://external-research/1"],
                }
            ],
        },
    }
    with pytest.raises(ValueError):
        finalize._validate_artifact_evidence(envelope, {"selection:primary"})
    # 稳定错误码：任何引证校验失败都映射到 failed_validation（25.2-03 契约）。
    assert finalize.ERROR_CODE_FAILED_VALIDATION == "failed_validation"


# ────────────────────────── 运行时：schema 拒绝 false 旗标 ──────────────────────────


def test_write_with_prohibited_from_canon_false_rejected_by_schema():
    """wire 载荷试图把 prohibited_from_canon 写成 false → schema 拒绝（Literal[True]）。"""
    forged_flag = False
    payload = {
        "type": "external_evidence",
        "schema_version": 1,
        "sources": [
            {
                "server": "stub-external-research",
                "tool": "external_search",
                "uri": "https://stub.example/research/1",
                "title": "Stub Finding",
                "retrieved_from": "mcp",
            }
        ],
        "retrieval_time": "2026-08-02T00:00:00Z",
        "claims": [{"text": "finding", "source_index": 0}],
        "confidence": "low",
        "prohibited_from_canon": forged_flag,
        "release_status": "external",
    }
    with pytest.raises(ValidationError):
        ExternalEvidenceArtifact.model_validate(payload)


def test_omitted_prohibited_from_canon_still_persists_true():
    """客户端载荷省略 prohibited_from_canon → 服务端常量 true（服务端恒定，非客户端标志）。"""
    payload = {
        "type": "external_evidence",
        "schema_version": 1,
        "sources": [
            {
                "server": "stub-external-research",
                "tool": "external_search",
                "uri": "https://stub.example/research/1",
                "title": "Stub Finding",
                "retrieved_from": "mcp",
            }
        ],
        "retrieval_time": "2026-08-02T00:00:00Z",
        "claims": [{"text": "finding", "source_index": 0}],
        "confidence": "low",
        "release_status": "external",
    }
    artifact = ExternalEvidenceArtifact.model_validate(payload)
    assert artifact.prohibited_from_canon is True


def test_external_evidence_envelope_carries_all_d09_fields():
    """D-09 信封字段齐全：type / schema_version / sources / retrieval_time / claims /
    confidence / prohibited_from_canon / release_status。"""
    payload = {
        "type": "external_evidence",
        "schema_version": 1,
        "sources": [
            {
                "server": "srv",
                "tool": "tool",
                "uri": "https://example.com/a",
                "title": "Title",
                "retrieved_from": "mcp",
            }
        ],
        "retrieval_time": "2026-08-02T00:00:00Z",
        "claims": [
            {"text": "c1", "source_index": 0},
            {"text": "c2", "source_index": 1},
        ],
        "confidence": "medium",
        "prohibited_from_canon": True,
        "release_status": "external",
    }
    artifact = ExternalEvidenceArtifact.model_validate(payload)
    assert artifact.type == "external_evidence"
    assert artifact.schema_version == 1
    assert artifact.sources[0].retrieved_from == "mcp"
    assert [c.source_index for c in artifact.claims] == [0, 1]
    assert artifact.confidence == "medium"
    assert artifact.release_status == "external"


# ────────────────────────── 运行时：服务门禁拒绝发布 ──────────────────────────


class _FakeArtifact:
    """transition_artifact_status 所需的伪 artifact 行（status/type 属性）。"""

    def __init__(self, artifact_type: str, status: str) -> None:
        self.type = artifact_type
        self.status = status


async def _transition(fake: _FakeArtifact, to_status: str):
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=fake)
    await artifact_service.transition_artifact_status(
        db, artifact_id=1, owner_id=1, to_status=to_status
    )
    return fake


def test_external_evidence_transition_to_published_refused():
    """external_evidence 向 published 迁移被服务门禁拒绝（candidate → 永不发布）。"""
    fake = _FakeArtifact("external_evidence", "candidate")
    with pytest.raises(artifact_service.ArtifactStateError):
        _run(fake, "published")
    # 前向台阶同样拒绝
    for step in ("validated", "approved"):
        with pytest.raises(artifact_service.ArtifactStateError):
            _run(_FakeArtifact("external_evidence", "candidate"), step)
    # rejected 分支仍然可用（唯一合法迁移）
    rejected = _run(_FakeArtifact("external_evidence", "candidate"), "rejected")
    assert rejected.status == "rejected"


def test_cited_answer_transitions_unaffected_by_external_gate():
    """普通 cited_answer 产物的 candidate→validated 迁移不受 external_evidence 门禁影响。"""
    fake = _run(_FakeArtifact("cited_answer", "candidate"), "validated")
    assert fake.status == "validated"


def _run(fake: _FakeArtifact, to_status: str) -> _FakeArtifact:
    import asyncio

    return asyncio.run(_transition(fake, to_status))
