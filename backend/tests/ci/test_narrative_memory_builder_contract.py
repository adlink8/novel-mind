"""CI contract checks for Phase 14 narrative-memory builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.narrative_memory.builder_worker import (
    scan_builder_package_for_forbidden_capabilities,
)


pytestmark = pytest.mark.unit


def test_builder_files_exist() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    required = [
        root / "models" / "narrative_memory_builder.py",
        root / "services" / "narrative_memory" / "builder_worker.py",
        root / "services" / "narrative_memory" / "builder_gateway.py",
        root / "services" / "narrative_memory" / "builder_budget.py",
        root / "services" / "narrative_memory" / "arc_planner.py",
        root / "services" / "narrative_memory" / "optional_sources.py",
        root / "services" / "narrative_memory" / "builder_report.py",
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_narrative_memory_build.py",
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "14_narrative_memory_builder_control.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    assert missing == []


def test_migration_revises_phase13_head() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "14_narrative_memory_builder_control.py"
    )
    source = path.read_text(encoding="utf-8")
    assert 'revision = "14membuild01"' in source
    assert 'down_revision = "13memoryauth01"' in source
    assert "narrative_memory_build_runs" in source
    assert "active_pointer" not in source


def test_cli_has_no_promote_flags() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "run_narrative_memory_build.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "start" in source and "resume" in source and "cancel" in source
    assert "--promote" not in source or "forbidden" in source
    assert "all-books" in source  # only as forbidden set entry


def test_forbidden_capability_scan_clean_of_chat_imports() -> None:
    hits = scan_builder_package_for_forbidden_capabilities()
    assert not any("reader_chat" in h and "import" in h for h in hits)
