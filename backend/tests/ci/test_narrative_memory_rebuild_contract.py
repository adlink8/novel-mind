"""CI release contract for Phase 16 dependency-aware local rebuild."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
NM = ROOT / "app" / "services" / "narrative_memory"
MODELS = ROOT / "app" / "models"
MIGRATIONS = ROOT / "migrations" / "versions"
SCRIPTS = ROOT / "scripts"

PHASE16_MODULES = (
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
    "litellm",
    "openai",
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


def test_phase16_files_exist() -> None:
    for name in PHASE16_MODULES:
        assert (NM / name).is_file(), name
    assert (MODELS / "narrative_memory_rebuild.py").is_file()
    assert (MIGRATIONS / "16_narrative_memory_rebuild_authority.py").is_file()
    assert (SCRIPTS / "run_narrative_memory_rebuild.py").is_file()


def test_migration_revises_phase14_head() -> None:
    source = (
        MIGRATIONS / "16_narrative_memory_rebuild_authority.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "16memrebuild01"' in source
    assert 'down_revision = "14membuild01"' in source
    assert "narrative_memory_rebuild_plans" in source
    assert "narrative_memory_rebuild_items" in source
    assert "narrative_memory_reuse_reports" in source
    assert "active_pointer" not in source
    assert "from app.models" not in source


def test_provider_free_oracle_carry_report_static_scan() -> None:
    for name in PHASE16_MODULES:
        source = (NM / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for imp in _imports_of(tree):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                assert not imp.startswith(prefix), f"{name} -> {imp}"
        assert "def set_active_pointer" not in source
        assert "promote_timeline" not in source


def test_cli_fixed_commands_and_explicit_versions() -> None:
    source = (SCRIPTS / "run_narrative_memory_rebuild.py").read_text(encoding="utf-8")
    for cmd in ("plan", "status", "execute", "cancel", "resume", "report"):
        assert cmd in source
    assert "--parent-version-id" in source
    assert "--target-version-id" in source
    assert "--owner-id" in source
    assert "--novel-id" in source
    assert "FORBIDDEN_OPTIONS" in source
    assert "promote" in source  # listed as forbidden
    tree = ast.parse(source)
    imports = _imports_of(tree)
    for imp in imports:
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not imp.startswith(prefix), imp


def test_carried_decision_documented_stage_free() -> None:
    """decision=carried creates no Phase 14 stage rows (executor invariant)."""
    source = (NM / "rebuild_executor.py").read_text(encoding="utf-8")
    assert "RebuildDecision.CARRIED" in source
    assert "ensure_stages" in source
    assert "carried" in source.lower()
    # Must not invent a second stage status for carry
    assert "carried_forward" not in source


def test_no_phase16_fastapi_product_route() -> None:
    api_root = ROOT / "app" / "api"
    for path in api_root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "run_narrative_memory_rebuild" not in source
        assert "compute_rebuild_plan" not in source
        assert "carry_forward_from_plan" not in source


def test_model_registers_rebuild_authority() -> None:
    init_src = (MODELS / "__init__.py").read_text(encoding="utf-8")
    assert "NarrativeMemoryRebuildPlan" in init_src
    assert "NarrativeMemoryRebuildItem" in init_src
    assert "NarrativeMemoryReuseReport" in init_src
