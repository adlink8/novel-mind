"""Phase 37-01 derivative generation boundary gates (Wave 0 fixtures).

REQ-FORK-03 / REQ-CRE-05 / REQ-CRE-06 / D-37-01: the generator consumes a
frozen, evidence-grounded context package and may only suggest candidates —
deterministic code owns gates and publication. These red-team gates prove, with
deterministic pure functions and AST source checks (no PostgreSQL):

- the "valid continuation" package is byte-replayable and structurally complete;
- wrong-character-knowledge / impossible-causal-order / invented-clue-payoff /
  missing-world-rule can never be smuggled in: dimensions are sourced only from
  structured passed rows, and empty dimensions are ``unavailable`` — never
  fabricated from AI summaries or chat content;
- a citation/evidence ref forged *after* sealing is detected (hash mismatch);
- allowed divergence never writes to Original Canon (the compiler's only write
  model is the append-only Fanfiction Canon package row, and it never creates
  forks or active pointers).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.derivative_generation.context_package import (
    ContextBudgetPolicy,
    ContextPackageError,
    DimensionStatus,
    assemble_package_payload,
    budget_verdict,
    compute_dimension_status,
    dimension_view,
    package_hash,
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
        "world_state": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"entity_key": "hero", "state": "alive"}],
        ),
        "timeline": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"edge_key": "e1", "source": "a", "target": "b"}],
        ),
        "unresolved_clues": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"logical_clue_id": "clue-1", "status": "active"}],
        ),
        "world_rules": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"rule_key": "rule-1"}],
        ),
        "evidence": dimension_view(
            status=DimensionStatus.AVAILABLE,
            items=[{"leaf_key": "chapter:1", "content_hash": HEX64}],
        ),
        "user_intent": {
            "status": "available",
            "kind": "continuation",
            "hash": "b" * 64,
        },
    }
    data.update(overrides)
    return data


def _valid_continuation_payload() -> dict:
    return assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=7,
        fork_key="ff-fixtures",
        intent="continuation",
        lineage=_lineage(),
        dimensions=_dimensions(),
        budget_estimate={"blocked": False},
    )


# ---------------------------------------------------------------------------
# AST: the compiler is a fact compiler, not a chat/summary summarizer
# ---------------------------------------------------------------------------


def test_compiler_never_sources_facts_from_summaries_or_chat():
    """No summary/chat reader may feed facts into the package (REQ-CRE-05)."""
    for forbidden in (
        "AnalysisResult",
        "FanFictionChapter",
        "ReaderConversation",
        "ReaderMessage",
    ):
        assert forbidden not in SERVICE_SOURCE, (
            f"compiler must not source facts from {forbidden!r}"
        )


def test_compiler_never_writes_original_or_interpretation():
    """No Original / Interpretation write model is reachable (D-37-01)."""
    for forbidden in ("CanonSpaceArtifact", "original_canon", "user_interpretation"):
        assert forbidden not in SERVICE_SOURCE


def test_compiler_only_creates_package_rows_not_forks_or_pointers():
    """The compiler never auto-forks and never moves an active pointer."""
    # The fork is only queried (select), never constructed; no pointer is created.
    assert "CanonFork(" not in SERVICE_SOURCE
    assert "ClueActivePointer(" not in SERVICE_SOURCE
    assert "CluePointerJournal(" not in SERVICE_SOURCE
    # The only object added to a session is a sealed package row.
    assert "ContextPackageRecord(" in SERVICE_SOURCE


# ---------------------------------------------------------------------------
# Valid continuation fixture (REQ-CRE-05)
# ---------------------------------------------------------------------------


def test_valid_continuation_package_is_byte_replayable():
    a = _valid_continuation_payload()
    b = _valid_continuation_payload()
    assert a == b
    assert package_hash(a) == package_hash(b)
    assert len(package_hash(a)) == 64
    assert set(a["dimensions"]) == {
        "world_state",
        "timeline",
        "unresolved_clues",
        "world_rules",
        "evidence",
        "user_intent",
    }
    assert a["dimensions"]["user_intent"]["kind"] == "continuation"
    assert a["budget_estimate"]["blocked"] is False


# ---------------------------------------------------------------------------
# Wrong-character-knowledge can never be smuggled in (REQ-CRE-06)
# ---------------------------------------------------------------------------


def test_wrong_character_knowledge_cannot_be_injected_after_sealing():
    """Any post-seal dimension change invalidates the stored hash."""
    payload = _valid_continuation_payload()
    forged = json.loads(json.dumps(payload))
    forged["dimensions"]["world_state"]["items"].append(
        {"entity_key": "hero", "state": "knows_the_truth"}  # not in the rows
    )
    stored = package_hash(payload)
    with pytest.raises(ContextPackageError) as exc:
        verify_package_hash(forged, stored)
    assert exc.value.code == "package_hash_mismatch"


def test_impossible_causal_order_changes_the_sealed_hash():
    """A timeline edge re-ordered/forged after sealing is detectable."""
    payload = _valid_continuation_payload()
    tampered = json.loads(json.dumps(payload))
    tampered["dimensions"]["timeline"]["items"] = [
        {"edge_key": "e1", "source": "b", "target": "a"}  # reversed causality
    ]
    assert package_hash(tampered) != package_hash(payload)


def test_invented_clue_payoff_is_not_invented_as_resolved():
    """A clue dimension without data is unavailable — never silently resolved."""
    clues = dimension_view(status=DimensionStatus.UNAVAILABLE)
    assert compute_dimension_status(clues["items"]) is DimensionStatus.UNAVAILABLE
    payload = assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=7,
        fork_key="ff-fixtures",
        intent="continuation",
        lineage=_lineage(),
        dimensions=_dimensions(unresolved_clues=clues),
        budget_estimate={"blocked": False},
    )
    assert payload["dimensions"]["unresolved_clues"]["status"] == "unavailable"
    assert payload["dimensions"]["unresolved_clues"]["items"] == []


def test_missing_world_rule_is_reported_not_fabricated():
    rules = dimension_view(status=DimensionStatus.UNAVAILABLE)
    payload = assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=7,
        fork_key="ff-fixtures",
        intent="continuation",
        lineage=_lineage(),
        dimensions=_dimensions(world_rules=rules),
        budget_estimate={"blocked": False},
    )
    assert payload["dimensions"]["world_rules"]["status"] == "unavailable"


# ---------------------------------------------------------------------------
# Citation outside package / allowed divergence (REQ-FORK-03, REQ-CRE-06)
# ---------------------------------------------------------------------------


def test_citation_outside_package_is_detectable():
    """An evidence ref forged after sealing never replays the stored hash."""
    payload = _valid_continuation_payload()
    forged = json.loads(json.dumps(payload))
    forged["dimensions"]["evidence"]["items"].append(
        {"leaf_key": "chapter:99", "content_hash": "f" * 64}  # outside package
    )
    with pytest.raises(ContextPackageError):
        verify_package_hash(forged, package_hash(payload))


def test_allowed_divergence_never_enters_the_package_space():
    """The package is Fanfiction Canon only; divergence is an explicit later step."""
    payload = _valid_continuation_payload()
    assert payload["space"] == "fanfiction_canon"
    assert payload["schema_version"].startswith("derivative-context.v1")


def test_budget_gate_blocks_before_provider_even_for_valid_continuation():
    """A valid continuation that exceeds the budget is still blocked pre-call."""
    oversized = assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=7,
        fork_key="ff-fixtures",
        intent="continuation",
        lineage=_lineage(),
        dimensions=_dimensions(
            world_state=dimension_view(
                status=DimensionStatus.AVAILABLE,
                items=[{"blob": "y" * 300_000}],
            )
        ),
        budget_estimate={"blocked": False},
    )
    verdict = budget_verdict(oversized, ContextBudgetPolicy(max_input_tokens=50))
    assert verdict["blocked"] is True
    assert verdict["block_reason"] == "budget_exhausted"
