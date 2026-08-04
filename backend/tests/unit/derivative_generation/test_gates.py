"""Unit tests for the deterministic consistency gates (Phase 37-03).

Covers the D-37-03 / REQ-FORK-03 / REQ-CRE-06 / REQ-FORK-06 contract layer of
``services/derivative_generation/gates.py`` against the frozen continuation
fixtures (``services/derivative_generation/fixtures.py``):

- the frozen sample set is the qualification gate and every fixture reproduces
  its expected deterministic verdict;
- character / canon-fact / timeline-causality / unresolved-clue / world-rule /
  evidence / scope / budget violations fail closed and are locatable to a
  package/candidate field;
- an explicit CanonDelta is ``needs_override`` — never auto-accepted;
- BranchSuggestion is a disabled-by-default candidate output bound to conflict,
  canon delta and evidence; it never auto-forks and never grants/reuses any
  approval (REQ-FORK-06).
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from app.services.derivative_generation.candidate import (
    GateVerdict,
    parse_candidate,
)
from app.services.derivative_generation.context_package import (
    DimensionStatus,
    dimension_view,
    package_hash,
)
from app.services.derivative_generation.fixtures import (
    CANDIDATE_EVIDENCE_1,
    CANDIDATE_EVIDENCE_2,
    FIXTURE_KEYS,
    OUTSIDE_EVIDENCE,
    build_candidate_json,
    build_fixture,
    build_package,
    fixture_hash,
    qualify_fixture,
    run_frozen_qualification,
    verify_fixture_hash,
)
from app.services.derivative_generation.gates import (
    CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE,
    CODE_BUDGET_EXHAUSTED,
    CODE_CHARACTER_CONTRADICTION,
    CODE_CHARACTER_OUTSIDE_PACKAGE,
    CODE_CLUE_CONTRADICTION,
    CODE_CLUE_OUTSIDE_PACKAGE,
    CODE_CROSS_FORK,
    CODE_CUTOFF_EXCEEDED,
    CODE_DIVERGENCE_EVIDENCE_OUTSIDE_PACKAGE,
    CODE_DIVERGENCE_REQUIRES_OVERRIDE,
    CODE_EVIDENCE_OUTSIDE_PACKAGE,
    CODE_FACT_CONTRADICTION,
    CODE_INTENT_MISMATCH,
    CODE_MISSING_EVIDENCE,
    CODE_PACKAGE_HASH_MISMATCH,
    CODE_SCOPE_DENIED,
    CODE_TIMELINE_CONTRADICTION,
    CODE_TIMELINE_CYCLE,
    CODE_TIMELINE_MISSING_EVENT,
    CODE_UNCERTAIN,
    ContinuityClaim,
    GateViolationSeverity,
    check_continuity_claims,
    evaluate_consistency,
    package_evidence_allowlist,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64

GATES_PATH = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "services"
    / "derivative_generation"
    / "gates.py"
)
GATES_SOURCE = GATES_PATH.read_text(encoding="utf-8")


def _evaluate(draft_json, package, *, claims=None, **overrides):
    """Run the full deterministic verdict for one candidate/package pair."""
    draft = parse_candidate(draft_json)
    base = dict(
        owner_id=package["owner_id"],
        novel_id=package["novel_id"],
        fork_id=package["fork_id"],
        expected_package_hash=package_hash(package),
        package_intent=package["intent"],
    )
    base.update(overrides)
    return evaluate_consistency(draft, package, claims=claims or [], **base)


def _claim(**fields):
    return ContinuityClaim.model_validate(fields)


# ---------------------------------------------------------------------------
# Frozen sample set is the qualification gate (REQ-CRE-06)
# ---------------------------------------------------------------------------


def test_all_frozen_fixtures_qualify():
    results = run_frozen_qualification()
    assert len(results) == len(FIXTURE_KEYS)
    failed = [(key, verdict, missing) for key, ok, verdict, missing in results if not ok]
    assert failed == [], f"frozen qualification fixtures failed: {failed}"


@pytest.mark.parametrize("fixture_key", FIXTURE_KEYS)
def test_every_fixture_reproduces_expected_verdict(fixture_key):
    payload = build_fixture(fixture_key)
    ok, result, missing = qualify_fixture(payload)
    assert ok, f"verdict={result.verdict.value} missing={missing}"


def test_fixture_hashes_are_stable():
    a = build_fixture("valid-continuation")
    b = build_fixture("valid-continuation")
    assert a["fixture_hash"] == b["fixture_hash"]
    assert len(a["fixture_hash"]) == 64


def test_fixture_hash_is_sensitive_to_mutation():
    payload = build_fixture("valid-continuation")
    mutated = copy.deepcopy(payload)
    mutated["claims"].append({"claim_key": "x", "category": "timeline"})
    assert fixture_hash(mutated) != payload["fixture_hash"]
    with pytest.raises(Exception):
        verify_fixture_hash(mutated)


# ---------------------------------------------------------------------------
# Scope / hash / lineage (REQ-FORK-03)
# ---------------------------------------------------------------------------


def test_package_hash_mismatch_fails_closed():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="阿宁走入竹林。"),
        package,
        expected_package_hash="1" * 64,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_PACKAGE_HASH_MISMATCH)
    assert result.violations[0].field == "package_hash"


def test_non_fanfiction_space_is_scope_denied():
    package = build_package()
    forged = dict(package)
    forged["space"] = "original_canon"
    result = _evaluate(
        build_candidate_json(draft="阿宁走入竹林。"), forged
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_SCOPE_DENIED)
    assert result.violations[0].field == "space"


def test_cross_fork_package_fails_closed():
    package = build_package(fork_id=7)
    result = _evaluate(
        build_candidate_json(draft="阿宁走入竹林。"),
        package,
        fork_id=99,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CROSS_FORK)


def test_incomplete_lineage_fails_closed():
    package = build_package()
    forged = copy.deepcopy(package)
    del forged["version"]["manifest_hash"]
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), forged)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("incomplete_lineage")


def test_intent_mismatch_fails_closed():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="重写第一章。", intent="rewrite"),
        package,
        package_intent="continuation",
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_INTENT_MISMATCH)


def test_budget_blocked_fails_closed_before_publish():
    package = build_package()
    forged = copy.deepcopy(package)
    forged["budget_estimate"] = {
        "blocked": True,
        "block_reason": "budget_exhausted",
    }
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), forged)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_BUDGET_EXHAUSTED)
    assert result.violations[0].field == "budget_estimate.blocked"


# ---------------------------------------------------------------------------
# Dimension availability (fail closed on missing facts)
# ---------------------------------------------------------------------------


def test_missing_world_state_blocks_qualification():
    package = build_package(
        dimensions={
            **build_package()["dimensions"],
            "world_state": dimension_view(status=DimensionStatus.UNAVAILABLE),
        }
    )
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), package)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("dimension_unavailable:world_state")


def test_blocked_evidence_dimension_blocks_qualification():
    package = build_package(
        dimensions={
            **build_package()["dimensions"],
            "evidence": dimension_view(
                status=DimensionStatus.BLOCKED, block_reason="beyond_cutoff"
            ),
        }
    )
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), package)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("dimension_blocked:evidence")


# ---------------------------------------------------------------------------
# Evidence allowlist (draft / branch / divergence)
# ---------------------------------------------------------------------------


def test_citation_outside_package_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="阿宁走入竹林。", citations=[OUTSIDE_EVIDENCE]
        ),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_EVIDENCE_OUTSIDE_PACKAGE)
    assert result.violations[0].evidence_keys == [OUTSIDE_EVIDENCE]
    assert result.violations[0].field == "dimensions.evidence.items"


def test_branch_suggestion_evidence_outside_package_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="阿宁走入竹林。",
            branch=[
                {
                    "choice_text": "a",
                    "branch_summary": "b",
                    "triggering_conflict": "c",
                    "canon_delta_hash": HEX64,
                    "evidence_refs": [OUTSIDE_EVIDENCE],
                }
            ],
        ),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE)
    assert result.violations[0].field.startswith("branch_suggestions[0]")


def test_divergence_affected_evidence_outside_package_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="阿宁走入竹林。",
            divergence={
                "divergence_type": "character",
                "reason": "twist",
                "affected_evidence": [OUTSIDE_EVIDENCE],
                "scope": "derivative",
            },
        ),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_DIVERGENCE_EVIDENCE_OUTSIDE_PACKAGE)


def test_evidence_allowlist_from_package():
    package = build_package()
    assert CANDIDATE_EVIDENCE_1 in package_evidence_allowlist(package)


# ---------------------------------------------------------------------------
# Continuity claims: character / fact / timeline / clue (D-37-03)
# ---------------------------------------------------------------------------


def test_character_contradiction_blocks_and_is_locatable():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="阿宁宣告他知道秘密。"),
        package,
        claims=[
            _claim(
                claim_key="hero-knows",
                category="character_behavior",
                entity_key="hero",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CHARACTER_CONTRADICTION)
    violation = next(v for v in result.violations if v.code == CODE_CHARACTER_CONTRADICTION)
    # Locatable to the hero's frozen world_state canonical_payload field.
    assert violation.field == "dimensions.world_state.items[0].canonical_payload"
    assert violation.claim_key == "hero-knows"


def test_fact_contradiction_blocks_without_declared_override():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="死者真的复活了。"),
        package,
        claims=[
            _claim(
                claim_key="fact-resurrection",
                category="established_fact",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_FACT_CONTRADICTION)


def test_timeline_contradiction_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="回村后才进竹林。"),
        package,
        claims=[
            _claim(
                claim_key="order",
                category="timeline",
                evidence_keys=[CANDIDATE_EVIDENCE_2],
                chapter_number=2,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_TIMELINE_CONTRADICTION)


def test_clue_payoff_contradiction_blocks_and_is_locatable():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="金色脚印之谜解开了。"),
        package,
        claims=[
            _claim(
                claim_key="clue-payoff",
                category="clue",
                clue_id="clue-1",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CLUE_CONTRADICTION)
    violation = next(v for v in result.violations if v.code == CODE_CLUE_CONTRADICTION)
    # Locatable to the clue's frozen status field.
    assert violation.field == "dimensions.unresolved_clues.items[0].status"


def test_character_outside_package_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="陌生人走入竹林。"),
        package,
        claims=[
            _claim(
                claim_key="stranger",
                category="character_behavior",
                entity_key="stranger",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="consistent",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CHARACTER_OUTSIDE_PACKAGE)


def test_clue_outside_package_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="红色脚印之谜。"),
        package,
        claims=[
            _claim(
                claim_key="ghost-clue",
                category="clue",
                clue_id="clue-99",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="consistent",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CLUE_OUTSIDE_PACKAGE)


def test_claim_cutoff_exceeded_is_spoiler_blocked():
    package = build_package()  # through_chapter = 2
    result = _evaluate(
        build_candidate_json(draft="第三章的秘密。"),
        package,
        claims=[
            _claim(
                claim_key="future",
                category="established_fact",
                evidence_keys=[CANDIDATE_EVIDENCE_2],
                chapter_number=3,
                disposition="consistent",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CUTOFF_EXCEEDED)


def test_claim_missing_evidence_blocks():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="阿宁走入竹林。"),
        package,
        claims=[
            _claim(
                claim_key="ungrounded",
                category="established_fact",
                evidence_keys=[],
                chapter_number=1,
                disposition="consistent",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_MISSING_EVIDENCE)


def test_claim_uncertain_is_warning_not_blocked():
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="阿宁走入竹林。"),
        package,
        claims=[
            _claim(
                claim_key="unknown",
                category="established_fact",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="unknown",
            )
        ],
    )
    assert result.verdict is GateVerdict.CANDIDATE
    assert result.has_code(CODE_UNCERTAIN)
    uncertain = next(v for v in result.violations if v.code == CODE_UNCERTAIN)
    assert uncertain.severity is GateViolationSeverity.WARNING


def test_claim_evidence_outside_package_blocks():
    package = build_package()
    violations = check_continuity_claims(
        parse_candidate(build_candidate_json(draft="x")),
        package,
        [
            _claim(
                claim_key="c",
                category="established_fact",
                evidence_keys=[OUTSIDE_EVIDENCE],
                disposition="consistent",
            )
        ],
    )
    assert any(v.code == CODE_EVIDENCE_OUTSIDE_PACKAGE for v in violations)
    assert violations[0].field == "claims.c.evidence_keys"


def test_declared_divergence_covers_its_own_contradiction():
    """An explicit CanonDelta covers only its matching contradiction class."""
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="为转折，阿宁其实早已知晓秘密。",
            divergence={
                "divergence_type": "character",
                "reason": "the twist requires early knowledge",
                "affected_evidence": [CANDIDATE_EVIDENCE_1],
                "scope": "derivative",
            },
        ),
        package,
        claims=[
            _claim(
                claim_key="declared",
                category="character_behavior",
                entity_key="hero",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="contradiction",
            )
        ],
    )
    # The character contradiction is covered -> needs_override, never accepted.
    assert result.verdict is GateVerdict.NEEDS_OVERRIDE
    assert result.reason == CODE_DIVERGENCE_REQUIRES_OVERRIDE
    assert not result.has_code(CODE_CHARACTER_CONTRADICTION)


def test_declared_divergence_does_not_cover_other_classes():
    """A character divergence never covers a timeline contradiction."""
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="为转折，阿宁其实早已知晓秘密。",
            divergence={
                "divergence_type": "character",
                "reason": "the twist requires early knowledge",
                "affected_evidence": [CANDIDATE_EVIDENCE_1],
                "scope": "derivative",
            },
        ),
        package,
        claims=[
            _claim(
                claim_key="undeclared-timeline",
                category="timeline",
                evidence_keys=[CANDIDATE_EVIDENCE_2],
                chapter_number=2,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_TIMELINE_CONTRADICTION)


# ---------------------------------------------------------------------------
# Timeline causal-graph integrity (package self-check)
# ---------------------------------------------------------------------------


def _package_with_cyclic_timeline():
    package = build_package()
    forged = copy.deepcopy(package)
    forged["dimensions"]["timeline"] = dimension_view(
        status=DimensionStatus.AVAILABLE,
        items=[
            {
                "event_key": "event-x",
                "canonical_payload": {},
                "canonical_payload_hash": HEX64,
            },
            {
                "event_key": "event-y",
                "canonical_payload": {},
                "canonical_payload_hash": HEX64,
            },
            {
                "edge_key": "edge-x",
                "source_event_key": "event-x",
                "target_event_key": "event-y",
                "edge_type": "cause",
            },
            {
                "edge_key": "edge-y",
                "source_event_key": "event-y",
                "target_event_key": "event-x",
                "edge_type": "cause",
            },
        ],
    )
    return forged


def test_timeline_cycle_fails_closed():
    package = _package_with_cyclic_timeline()
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), package)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_TIMELINE_CYCLE)


def test_timeline_edge_missing_event_fails_closed():
    package = build_package()
    forged = copy.deepcopy(package)
    forged["dimensions"]["timeline"] = dimension_view(
        status=DimensionStatus.AVAILABLE,
        items=[
            {
                "event_key": "event-a",
                "canonical_payload": {},
                "canonical_payload_hash": HEX64,
            },
            {
                "edge_key": "edge-ghost",
                "source_event_key": "event-a",
                "target_event_key": "event-ghost",
                "edge_type": "cause",
            },
        ],
    )
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), forged)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_TIMELINE_MISSING_EVENT)


def test_unresolved_clue_without_evidence_fails_closed():
    package = build_package()
    forged = copy.deepcopy(package)
    forged["dimensions"]["unresolved_clues"] = dimension_view(
        status=DimensionStatus.AVAILABLE,
        items=[
            {
                "logical_clue_id": "clue-1",
                "status": "active",
                "evidence_refs": [],
            }
        ],
    )
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), forged)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("clue_without_evidence")


# ---------------------------------------------------------------------------
# Verdict assembly: clean / divergence / blocked
# ---------------------------------------------------------------------------


def test_clean_candidate_yields_candidate_verdict():
    package = build_package()
    result = _evaluate(build_candidate_json(draft="阿宁走入竹林。"), package)
    assert result.verdict is GateVerdict.CANDIDATE
    assert result.reason is None
    assert result.errors == []


def test_explicit_divergence_is_needs_override_never_auto_promoted():
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="为转折，阿宁转身离开竹林。",
            divergence={
                "divergence_type": "world_rule",
                "reason": "a deliberate rule-breaking beat for the twist",
                "affected_evidence": [CANDIDATE_EVIDENCE_1],
                "scope": "derivative",
            },
        ),
        package,
    )
    assert result.verdict is GateVerdict.NEEDS_OVERRIDE
    assert result.reason == CODE_DIVERGENCE_REQUIRES_OVERRIDE
    assert result.violations == []


def test_failure_keeps_candidate_and_reason():
    """Blocked means the candidate row keeps its reason — never silent publish."""
    package = build_package()
    result = _evaluate(
        build_candidate_json(draft="阿宁宣告他知道秘密。", citations=[OUTSIDE_EVIDENCE]),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.reason == CODE_EVIDENCE_OUTSIDE_PACKAGE
    assert result.detail is not None


# ---------------------------------------------------------------------------
# BranchSuggestion contract (REQ-FORK-06 / D-37-05)
# ---------------------------------------------------------------------------


def test_branch_suggestion_carries_all_six_fields_disabled_by_default():
    package = build_package()
    result = _evaluate(
        build_candidate_json(
            draft="阿宁在竹林入口迟疑片刻。",
            branch=[
                {
                    "choice_text": "深入竹林",
                    "branch_summary": "循着脚印继续前进",
                    "triggering_conflict": "脚印在深处消失",
                    "canon_delta_hash": HEX64,
                    "evidence_refs": [CANDIDATE_EVIDENCE_1],
                }
            ],
        ),
        package,
    )
    assert result.verdict is GateVerdict.CANDIDATE
    assert len(result.branch_suggestions) == 1
    suggestion = result.branch_suggestions[0]
    for field in (
        "choice_text",
        "branch_summary",
        "triggering_conflict",
        "canon_delta_hash",
        "evidence_refs",
        "enabled_by_default",
    ):
        assert field in suggestion, f"missing BranchSuggestion field {field}"
    assert suggestion["enabled_by_default"] is False


def test_branch_suggestion_enabled_default_is_rejected_at_schema():
    with pytest.raises(ValueError):
        parse_candidate(
            json.dumps(
                {
                    "schema_version": "derivative-candidate.v1",
                    "intent": "continuation",
                    "draft_text": "x",
                    "citation_keys": [],
                    "divergence": None,
                    "branch_suggestions": [
                        {
                            "choice_text": "a",
                            "branch_summary": "b",
                            "triggering_conflict": "c",
                            "canon_delta_hash": HEX64,
                            "evidence_refs": [],
                            "enabled_by_default": True,
                        }
                    ],
                }
            )
        )


def test_branch_suggestions_are_inert_candidate_outputs():
    """The gate result exposes only dicts; no fork/approval side effect exists."""
    payload = build_fixture("branch-suggestion")
    _, result, _ = qualify_fixture(payload)
    assert result.branch_suggestions
    for suggestion in result.branch_suggestions:
        assert suggestion["enabled_by_default"] is False
        assert set(suggestion["evidence_refs"]) <= package_evidence_allowlist(
            payload["package"]
        )
        # Suggestions describe a branch only; nothing beyond the dict is created.
        assert set(suggestion) == {
            "choice_text",
            "branch_summary",
            "triggering_conflict",
            "canon_delta_hash",
            "evidence_refs",
            "enabled_by_default",
        }


def test_gates_module_never_creates_fork_or_reuses_approval():
    """AST: gates.py has no write path and no approval/fork capability."""
    tree = ast.parse(GATES_SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    forbidden_imports = {
        "CanonFork",
        "ClueActivePointer",
        "ContextPackageRecord",
        "DerivativeGenerationCandidate",
        "DerivativeGenerationJob",
    }
    assert not (imported & forbidden_imports), (
        f"gates must not import write/promotion models: {imported & forbidden_imports}"
    )
    # No session/add write surface anywhere in the module (a set.add() is not
    # a persistence write; the fail-closed signal is a session write).
    assert "session.add" not in GATES_SOURCE
    assert "self._session" not in GATES_SOURCE
    assert "AsyncSession" not in GATES_SOURCE
    assert "allow_divergence" not in GATES_SOURCE
    assert "publish_derivative_revision" not in GATES_SOURCE


# ---------------------------------------------------------------------------
# ContinuityClaim model strictness
# ---------------------------------------------------------------------------


def test_character_behavior_claim_requires_entity_key():
    with pytest.raises(ValueError):
        _claim(claim_key="c", category="character_behavior", disposition="consistent")


def test_clue_claim_requires_clue_id():
    with pytest.raises(ValueError):
        _claim(claim_key="c", category="clue", disposition="consistent")


def test_continuity_claim_rejects_extra_fields():
    with pytest.raises(ValueError):
        ContinuityClaim.model_validate(
            {
                "claim_key": "c",
                "category": "timeline",
                "disposition": "consistent",
                "bonus": 1,
            }
        )
