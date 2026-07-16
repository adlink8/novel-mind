"""CI contract: Phase 17 candidate-only, no promotion/API/cutover surfaces."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2]
NM = BACKEND / "app" / "services" / "narrative_memory"
MODELS = BACKEND / "app" / "models" / "narrative_memory_qualification.py"
SCRIPT = BACKEND / "scripts" / "run_narrative_memory_qualification.py"
MIGRATION = (
    BACKEND / "migrations" / "versions" / "17_narrative_memory_qualification.py"
)
API_DIR = BACKEND / "app" / "api"


def test_migration_head_chain():
    text = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "17memqual01"' in text
    assert 'down_revision = "16memrebuild01"' in text
    assert "active_pointer" not in text
    assert "promotion" not in text


def test_schema_no_selector_columns():
    text = MODELS.read_text(encoding="utf-8")
    for frag in ("active_pointer", "current_version", "promoted", "is_active_candidate"):
        assert frag not in text


def test_no_fastapi_route_for_qualification():
    if not API_DIR.is_dir():
        return
    for path in API_DIR.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "narrative_memory_qualification" in src or "run_narrative_memory_qualification" in src:
            pytest.fail(f"API should not expose qualification: {path}")


def test_qualification_modules_exist():
    for name in (
        "qualification_contracts.py",
        "qualification_fixtures.py",
        "qualification_baseline.py",
        "qualification_runner.py",
        "qualification_metrics.py",
        "qualification_verdict.py",
        "qualification_repository.py",
        "qualification_verifier.py",
    ):
        assert (NM / name).is_file(), name
    assert SCRIPT.is_file()


def test_no_promotion_imports_in_qual_stack():
    banned = (
        "prepare_baseline",
        "commit_baseline",
        "ActiveBaseline",
        "promote_narrative",
        "ChunkActivePointer",
        "TimelineActivePointer",
        "ClueActivePointer",
    )
    for path in NM.glob("qualification_*.py"):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    assert alias.name not in banned, f"{path.name} imports {alias.name}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "reader_chat" not in alias.name


def test_verdict_vocabulary_closed():
    from app.services.narrative_memory.qualification_contracts import (
        QualificationVerdict,
        QUALIFICATION_KIND,
        SCOPE_DISCLAIMER,
    )

    assert set(QualificationVerdict) == {
        QualificationVerdict.QUALIFIED_CANDIDATE,
        QualificationVerdict.BLOCKED,
    }
    assert QUALIFICATION_KIND == "single_book_candidate"
    assert "v0.3" in SCOPE_DISCLAIMER
    assert "Does not promote" in SCOPE_DISCLAIMER
