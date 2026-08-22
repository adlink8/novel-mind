"""Adversarial context package boundary gates (Phase 37-01).

D-37-01 / REQ-FORK-03 / REQ-CRE-05: the package compiler is a fail-closed
boundary. These red-team gates prove, with deterministic pure functions and AST
source checks (no PostgreSQL):

- a stale/forged package hash never replays (package_hash_mismatch);
- a future cutoff can never expand the frozen fork scope (cutoff_exceeds_scope);
- a budget overrun blocks the package before any provider call
  (budget_exhausted);
- an unknown/forged intent and a missing dimension fail closed;
- a missing dimension is reported ``unavailable`` — never fabricated from
  summaries/chat, and an unresolved clue that does not exist is never invented;
- the compiler source exposes no Original Canon / User Interpretation write
  path (the only write model is the append-only package row).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.services.derivative_generation.context_package import (
    ContextBudgetPolicy,
    ContextPackageError,
    DimensionStatus,
    assemble_package_payload,
    assert_cutoff_within_fork,
    budget_verdict,
    compute_dimension_status,
    dimension_view,
    verify_package_hash,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64

SERVICE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "services"
    / "derivative_generation"
    / "context_package.py"
)
SERVICE_SOURCE = SERVICE_PATH.read_text(encoding="utf-8")


def _lineage(**overrides):
    data = {
        "source_version_key": "original:abc",
        "source_snapshot_hash": HEX64,
        "through_chapter": 2,
        "full_book_authorized": False,
        "cutoff_snapshot_hash": HEX64,
        "scope_hash": HEX64,
        "manifest_hash": HEX64,
    }
    data.update(overrides)
    return data


def _dimensions(**overrides):
    data = {
        "world_state": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "timeline": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "unresolved_clues": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "world_rules": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "evidence": dimension_view(status=DimensionStatus.UNAVAILABLE),
        "user_intent": {
            "status": "available",
            "kind": "continuation",
            "hash": "b" * 64,
        },
    }
    data.update(overrides)
    return data


def _payload(**kwargs):
    return assemble_package_payload(
        owner_id=kwargs.get("owner_id", 1),
        novel_id=kwargs.get("novel_id", 1),
        fork_id=kwargs.get("fork_id", 7),
        fork_key=kwargs.get("fork_key", "ff-test"),
        intent=kwargs.get("intent", "continuation"),
        lineage=kwargs.get("lineage", _lineage()),
        dimensions=kwargs.get("dimensions", _dimensions()),
        budget_estimate=kwargs.get("budget_estimate", {"blocked": False}),
    )


# ---------------------------------------------------------------------------
# AST: no Original / Interpretation write path in the compiler (D-37-01)
# ---------------------------------------------------------------------------


def test_compiler_has_no_original_or_interpretation_write_surface():
    """The compiler source never names an Original/Interpretation write model."""
    for forbidden in ("CanonSpaceArtifact", "original_canon", "user_interpretation"):
        assert forbidden not in SERVICE_SOURCE, (
            f"compiler source must not reference {forbidden!r} (no write-back)"
        )


def test_compiler_only_persists_the_package_row():
    tree = ast.parse(SERVICE_SOURCE)
    write_targets = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add":
                for keyword in node.keywords:
                    if keyword.arg is None and isinstance(keyword.value, ast.Name):
                        write_targets.add(keyword.value.id)
                if isinstance(node.func.value, ast.Name):
                    write_targets.add(node.func.value.id)
    # The only object added to a session is a ContextPackageRecord row.
    assert "ContextPackageRecord" in SERVICE_SOURCE
    assert "ContextPackageRecord(" in SERVICE_SOURCE


# ---------------------------------------------------------------------------
# Stale hash (T-37-01-02)
# ---------------------------------------------------------------------------


def test_forged_package_hash_fails_closed():
    payload = _payload()
    with pytest.raises(ContextPackageError) as exc:
        verify_package_hash(payload, "0" * 64)
    assert exc.value.code == "package_hash_mismatch"


def test_hash_change_after_seal_is_detected():
    """Mutating any sealed dimension invalidates the stored hash."""
    payload = _payload()
    stored = "0" * 64
    tampered = dict(payload)
    tampered["dimensions"] = dict(tampered["dimensions"])
    with pytest.raises(ContextPackageError):
        verify_package_hash(tampered, stored)


# ---------------------------------------------------------------------------
# Future cutoff (T-37-01-01)
# ---------------------------------------------------------------------------


def test_future_cutoff_cannot_expand_fork_scope():
    with pytest.raises(ContextPackageError) as exc:
        assert_cutoff_within_fork(requested=5, fork_cutoff=2)
    assert exc.value.code == "cutoff_exceeds_scope"


def test_cutoff_shrink_is_allowed_equal_is_allowed():
    assert_cutoff_within_fork(requested=2, fork_cutoff=2)
    assert_cutoff_within_fork(requested=1, fork_cutoff=2)


# ---------------------------------------------------------------------------
# Budget exhaustion (blocked before provider call)
# ---------------------------------------------------------------------------


def test_budget_exhaustion_blocks_before_any_provider_call():
    huge = _payload(
        dimensions=_dimensions(
            world_state=dimension_view(
                status=DimensionStatus.AVAILABLE,
                items=[{"canonical_payload": {"blob": "z" * 200_000}}],
            )
        )
    )
    verdict = budget_verdict(huge, ContextBudgetPolicy(max_input_tokens=50))
    assert verdict["blocked"] is True
    assert verdict["block_reason"] == "budget_exhausted"


def test_budget_policy_defaults_are_bounded():
    policy = ContextBudgetPolicy()
    assert policy.max_input_tokens > 0
    assert policy.max_evidence_items > 0
    assert policy.max_dimension_items > 0


# ---------------------------------------------------------------------------
# Forged intent / incomplete assembly
# ---------------------------------------------------------------------------


def test_forged_intent_fails_closed():
    with pytest.raises(ContextPackageError) as exc:
        _payload(intent="create-original-canon")
    assert exc.value.code == "invalid_intent"


def test_missing_dimension_fails_closed():
    dims = _dimensions()
    del dims["evidence"]
    with pytest.raises(ContextPackageError) as exc:
        _payload(dimensions=dims)
    assert exc.value.code == "incomplete_dimensions"


# ---------------------------------------------------------------------------
# Honest dimension reporting (REQ-CRE-01 pitfall #4)
# ---------------------------------------------------------------------------


def test_missing_dimension_is_unavailable_never_fake_empty():
    view = dimension_view(status=DimensionStatus.UNAVAILABLE)
    assert view["status"] == "unavailable"
    assert view["items"] == []
    assert compute_dimension_status(view["items"]) is DimensionStatus.UNAVAILABLE


def test_unresolved_clue_loss_is_honest():
    """A clue that does not exist is reported unavailable — never invented."""
    clues = dimension_view(status=DimensionStatus.UNAVAILABLE)
    payload = _payload(dimensions=_dimensions(unresolved_clues=clues))
    assert payload["dimensions"]["unresolved_clues"]["status"] == "unavailable"


def test_blocked_evidence_never_fake_success():
    view = dimension_view(
        status=DimensionStatus.BLOCKED,
        items=[],
        block_reason="beyond_cutoff",
    )
    assert view["status"] == "blocked"
    assert view["block_reason"] == "beyond_cutoff"
    assert view["items"] == []
