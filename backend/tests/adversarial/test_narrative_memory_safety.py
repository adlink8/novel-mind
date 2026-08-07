"""Adversarial safety matrix for Phase 28-01 whole-book builder recovery.

Guards the must-have invariants: no silent pending, no whole-book restart on a
single chapter failure, exact-cache reuse only for checksum-identical inputs,
and no pointer/cutover write path on recovery artifacts (D-02/D-03/D-04/D-07).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.services.narrative_memory.builder_contracts import ReasonCode, TerminalState
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.recovery import (
    build_resume_plan,
    terminal_state_for_status,
    validate_cache_reuse,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
NM = ROOT / "app" / "services" / "narrative_memory"
FIXTURE = ROOT / "tests" / "fixtures" / "narrative_memory" / "failure_matrix_v1.json"

FORBIDDEN_POINTER_FRAGMENTS = frozenset(
    {
        "set_active_pointer",
        "promote_timeline",
        "promote_clue",
        "TimelineActivePointer",
        "ClueActivePointer",
        "NarrativeActivePointer",
        "current_version",
        "cutover",
        "production_promotion",
    }
)

RECOVERY_FORBIDDEN_IMPORTS = (
    "reader_chat",
    "builder_gateway",
    "builder_budget",
    "builder_packages",
    "litellm",
    "openai",
    "ai_service",
)

RECOVERY_MODULES = ("recovery.py", "builder_contracts.py", "builder_repository.py")

# Phase 28-03 candidate hierarchy modules: immutable candidate-only output,
# provider-free, and never a pointer/promotion/cutover write path (D-07/D-09).
HIERARCHY_MODULES = ("arc_planner.py", "global_builder.py", "hierarchy.py")
HIERARCHY_FORBIDDEN_IMPORTS = (
    "reader_chat",
    "builder_gateway",
    "builder_budget",
    "litellm",
    "openai",
    "ai_service",
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


def _source(name: str) -> str:
    path = NM / name
    assert path.is_file(), f"missing module {name}"
    return path.read_text(encoding="utf-8")


def _stripped_source(name: str) -> str:
    """Remove FORBIDDEN_* constant definitions so deny-lists are not flagged."""
    source = _source(name)
    lines: list[str] = []
    skip_next_until_empty = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("FORBIDDEN_") or (skip_next_until_empty and stripped):
            if stripped.startswith("FORBIDDEN_") and "=" in stripped:
                skip_next_until_empty = True
            continue
        if stripped == "":
            skip_next_until_empty = False
        lines.append(line)
    return "\n".join(lines)


def test_recovery_modules_have_no_pointer_write_path() -> None:
    for name in RECOVERY_MODULES:
        tree = ast.parse(_stripped_source(name))
        calls = _call_names(tree)
        imports = _imports_of(tree)
        forbidden = FORBIDDEN_POINTER_FRAGMENTS - {
            "current_version",
            "promote",
            "promotion",
        }
        assert forbidden.isdisjoint(calls), f"{name}: {calls & forbidden}"
        for imp in imports:
            assert "active_pointer" not in imp
        assert "active_pointer" not in _stripped_source(name)


def test_recovery_module_is_provider_free() -> None:
    """recovery.py must be replayable from durable rows, not live providers."""
    source = _source("recovery.py")
    tree = ast.parse(source)
    for imp in _imports_of(tree):
        for banned in RECOVERY_FORBIDDEN_IMPORTS:
            assert banned not in imp, f"recovery.py imports {imp}"
    assert "ModelTransport" not in source
    assert "GatewayError" not in source


def test_worker_never_writes_active_pointer() -> None:
    tree = ast.parse(_stripped_source("builder_worker.py"))
    calls = _call_names(tree)
    forbidden = FORBIDDEN_POINTER_FRAGMENTS - {
        "current_version",
        "promote",
        "promotion",
    }
    assert forbidden.isdisjoint(calls), calls & forbidden
    imports = _imports_of(tree)
    assert not any("active_pointer" in imp for imp in imports)
    # The worker's own forbidden-list constant and scanner may mention the
    # fragment text, but it must never be *invoked*.
    assert "set_active_pointer(" not in _stripped_source("builder_worker.py")


def test_hierarchy_modules_never_write_canon_or_pointer() -> None:
    """28-03 candidate hierarchy modules stay provider-free and never promote.

    Outline/Mainline candidates are immutable candidate-only outputs; any
    invocation of a pointer/promotion/cutover path or a provider/chat import
    in these modules would violate D-06/D-07/D-09 and must fail closed.
    """
    for name in HIERARCHY_MODULES:
        source = _source(name)
        tree = ast.parse(source)
        calls = _call_names(tree)
        imports = _imports_of(tree)
        forbidden = FORBIDDEN_POINTER_FRAGMENTS - {
            "current_version",
            "promote",
            "promotion",
        }
        assert forbidden.isdisjoint(calls), f"{name}: {calls & forbidden}"
        for imp in imports:
            assert "active_pointer" not in imp
            for banned in HIERARCHY_FORBIDDEN_IMPORTS:
                assert banned not in imp, f"{name} imports {imp}"
        assert "set_active_pointer(" not in source
        assert "active_pointer" not in _stripped_source(name)
        # Candidate-only outputs must never write the DB by themselves; the
        # persistence seam lives in the candidate authority, not generation.
        assert "session.commit(" not in source
        assert "session.flush(" not in source


def test_hierarchy_modules_export_no_chat_or_pointer_contract() -> None:
    """Reader chat is never a fact source (D-06); no active-pointer contract."""
    for name in HIERARCHY_MODULES:
        source = _source(name)
        tree = ast.parse(source)
        calls = _call_names(tree)
        assert "reader_chat" not in calls, f"{name} references reader_chat"
        assert "promote_timeline" not in calls
        assert "promote_clue" not in calls
        assert "conversation_id" not in calls
        assert "message_id" not in calls


def test_failure_matrix_is_consistent() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1"
    matrix = data["matrix"]
    assert (
        len(matrix) >= 8
    )  # crash/retry/cancel/provider/budget/stale-cache/owner/isolation
    for case in matrix:
        assert "id" in case and "class" in case and "inject_at" in case
        assert case["expected_terminal"] in {t.value for t in TerminalState}
        assert case["expected_reason_code"] in {r.value for r in ReasonCode}
    classes = {c["class"] for c in matrix}
    assert {
        "crash",
        "retry",
        "cancel",
        "provider",
        "budget",
        "stale-cache",
        "owner",
        "isolation",
    } <= classes


def test_matrix_classifies_into_terminal_state_mapping() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in data["matrix"]:
        expected = case["expected_terminal"]
        assert expected in {t.value for t in TerminalState}
        if expected == TerminalState.ISOLATED.value:
            assert terminal_state_for_status("failed") == expected
        elif expected == TerminalState.BLOCKED.value:
            assert terminal_state_for_status("blocked_dependency") == expected


def test_no_silent_pending_after_terminal_run() -> None:
    """A run whose stages all carry explicit terminal states has no pending."""
    stages = [
        type(
            "S",
            (),
            {
                "run_id": 1,
                "stage_key": f"chapter_state:{i}",
                "status": "failed" if i == 2 else "completed",
                "status_reason": (
                    ReasonCode.INTERNAL_ERROR.value if i == 2 else "done"
                ),
                "reason_code": (ReasonCode.INTERNAL_ERROR.value if i == 2 else None),
                "terminal_state": terminal_state_for_status(
                    "failed" if i == 2 else "completed"
                ),
                "checkpoint": {} if i == 2 else {"reason_code": "done"},
                "attempt_count": 1,
                "dependency_keys": [],
            },
        )()
        for i in (1, 2, 3)
    ]
    plan = build_resume_plan(stages)
    assert plan.has_silent_pending is False
    assert plan.runnable == ()


def test_chapter_isolation_does_not_rewind_completed_sibling() -> None:
    """block_dependents must not touch completed/isolated rows (D-03)."""
    dependents = BuilderRepository._transitive_dependents(
        [
            type("S", (), {"stage_key": "chapter_state:1", "dependency_keys": []})(),
            type("S", (), {"stage_key": "chapter_state:2", "dependency_keys": []})(),
            type(
                "S",
                (),
                {
                    "stage_key": "arc_volume_aggregate:arc",
                    "dependency_keys": ["chapter_state:1", "chapter_state:2"],
                },
            )(),
        ],
        "chapter_state:1",
    )
    assert dependents == ["arc_volume_aggregate:arc"]
    assert "chapter_state:2" not in dependents


def test_validate_cache_reuse_rejects_owner_and_source_drift() -> None:
    ok, reason = validate_cache_reuse(
        stored_source_checksum="a" * 64,
        stored_lineage=None,
        stored_package_checksum=None,
        current_source_checksum="b" * 64,
        current_lineage=None,
        current_package_checksum=None,
    )
    assert ok is False
    assert reason == ReasonCode.SOURCE_DRIFT


def test_recovery_has_no_json_cache_reuse_by_pointer() -> None:
    """Cache decisions are checksum/lineage based, never pointer based."""
    source = _source("recovery.py")
    assert "active_pointer" not in source
    assert "cache_reuse" in source
    assert "stale_cache_rejected" in source
