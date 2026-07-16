"""Static capability scan: Phase 15 modules deny chat/provider/pointer/promotion."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import settings


pytestmark = pytest.mark.unit

PHASE15_MODULES = (
    "retrieval_contracts.py",
    "routing.py",
    "candidate_reader.py",
    "descent.py",
    "citations.py",
    "retrieval_manifests.py",
    "experiments.py",
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
}


def _nm_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "app"
        / "services"
        / "narrative_memory"
    )


def _cli_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_hierarchical_retrieval_experiment.py"
    )


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


def test_phase15_modules_exist():
    root = _nm_root()
    for name in PHASE15_MODULES:
        assert (root / name).is_file(), name
    assert _cli_path().is_file()


def test_experiment_setting_defaults_false():
    assert settings.narrative_memory_retrieval_experiment_enabled is False


def test_phase15_static_import_and_call_scan():
    root = _nm_root()
    for name in PHASE15_MODULES:
        source = (root / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = _imports_of(tree)
        for imp in imports:
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                assert not imp.startswith(prefix), f"{name} imports {imp}"
        calls = _call_names(tree)
        assert FORBIDDEN_CALL_NAMES.isdisjoint(calls), f"{name} has {calls & FORBIDDEN_CALL_NAMES}"
        assert "from app.services.reader_chat" not in source
        assert "from app.models.reader_chat" not in source
        assert "import reader_chat" not in source
        assert "set_active_pointer" not in source
        assert "promote_timeline" not in source
        # pointer/promotion may appear only as denial language, never as API
        assert "def set_active_pointer" not in source
        assert "def promote" not in source


def test_cli_has_no_promote_or_current_options():
    source = _cli_path().read_text(encoding="utf-8")
    assert "--promote" not in source
    assert "--current" not in source
    assert "--active" not in source
    assert "current_version" not in source
    tree = ast.parse(source)
    imports = _imports_of(tree)
    for imp in imports:
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not imp.startswith(prefix), imp


def test_no_phase15_fastapi_product_route():
    api_root = Path(__file__).resolve().parents[2] / "app" / "api"
    for path in api_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "run_retrieval_experiment" not in source
        assert "hierarchical_retrieval" not in source
