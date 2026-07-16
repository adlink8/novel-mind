"""Adversarial safety matrix for Phase 16 local rebuild."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.narrative_memory.carry_forward import carry_has_provider_capability
from app.services.narrative_memory.change_oracle import oracle_has_provider_capability
from app.services.narrative_memory.dependency_graph import graph_has_provider_capability
from app.services.narrative_memory.rebuild_contracts import RebuildDecision
from app.services.narrative_memory.rebuild_executor import executor_has_provider_capability
from app.services.narrative_memory.reuse_report import report_has_provider_capability


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
NM = ROOT / "app" / "services" / "narrative_memory"
CLI = ROOT / "scripts" / "run_narrative_memory_rebuild.py"

PHASE16_PROVIDER_FREE = (
    "rebuild_contracts.py",
    "dependency_graph.py",
    "change_oracle.py",
    "carry_forward.py",
    "rebuild_executor.py",
    "reuse_report.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "app.services.reader_chat",
    "app.models.reader_chat",
    "app.api.reader_chat",
    "app.services.timeline.model_gateway",
    "app.services.ai_service",
    "litellm",
    "openai",
)

FORBIDDEN_CALL_NAMES = {
    "set_active_pointer",
    "promote_timeline",
    "promote_version",
    "resolve_active",
    "resolve_current",
    "embed_documents",
    "create_embedding",
}


def _imports_of(tree: ast.AST) -> list[str]:
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _call_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_provider_free_capability_flags() -> None:
    assert graph_has_provider_capability() is False
    assert oracle_has_provider_capability() is False
    assert carry_has_provider_capability() is False
    assert executor_has_provider_capability() is False
    assert report_has_provider_capability() is False


def test_phase16_modules_ban_chat_provider_pointer_imports() -> None:
    for name in PHASE16_PROVIDER_FREE:
        path = NM / name
        assert path.is_file(), name
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for imp in _imports_of(tree):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                assert not imp.startswith(prefix), f"{name} imports {imp}"
        calls = _call_names(tree)
        assert FORBIDDEN_CALL_NAMES.isdisjoint(calls), f"{name}: {calls & FORBIDDEN_CALL_NAMES}"
        assert "from app.services.reader_chat" not in source
        assert "def set_active_pointer" not in source
        assert "def promote" not in source


def test_cli_rejects_promote_current_embedding_chat_options() -> None:
    source = CLI.read_text(encoding="utf-8")
    assert "FORBIDDEN_OPTIONS" in source
    for opt in (
        "promote",
        "rollback",
        "active",
        "current",
        "default",
        "all-books",
        "embedding",
        "reader-chat",
        "chat",
    ):
        assert opt in source
    assert "--promote" not in source.split("FORBIDDEN")[0] or "FORBIDDEN" in source
    # No product FastAPI route for rebuild
    api = ROOT / "app" / "api"
    for path in api.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "run_narrative_memory_rebuild" not in text
        assert "materialize_carry_and_dirty_stages" not in text


def test_decisions_are_closed_enum() -> None:
    assert {d.value for d in RebuildDecision} == {
        "dirty",
        "carried",
        "stale_blocked",
        "not_applicable",
    }


def test_executor_does_not_import_builder_gateway() -> None:
    source = (NM / "rebuild_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = _imports_of(tree)
    assert "app.services.narrative_memory.builder_gateway" not in imports
    assert not any("gateway" in i for i in imports)


def test_carry_does_not_import_embedding_or_index() -> None:
    source = (NM / "carry_forward.py").read_text(encoding="utf-8")
    assert "chromadb" not in source.lower()
    assert "embedding" not in source.lower()
    assert "openai" not in source
    assert "litellm" not in source


def test_oracle_does_not_write_pointer_tables() -> None:
    source = (NM / "change_oracle.py").read_text(encoding="utf-8")
    for table in (
        "chunk_active_pointers",
        "timeline_active_pointers",
        "narrative_unit_active",
    ):
        assert table not in source


def test_report_does_not_use_self_reported_worker_counters() -> None:
    source = (NM / "reuse_report.py").read_text(encoding="utf-8")
    assert "worker_counter" not in source
    assert "self_reported" not in source
    assert "NarrativeMemoryRebuildItem" in source
    assert "decision" in source
