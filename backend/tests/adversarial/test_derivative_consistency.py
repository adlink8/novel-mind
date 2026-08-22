"""Phase 37-03 adversarial derivative consistency gates (red team).

REQ-FORK-03 / REQ-CRE-06 / REQ-FORK-06 / D-37-03 / D-37-05: these red-team
gates prove, with deterministic pure functions and AST source checks (no
PostgreSQL):

- intentional character/fact/timeline/clue violations fail closed and are
  locatable to a package/candidate field — never silently repaired into canon;
- stale/forged evidence after sealing is detected (package hash mismatch);
- cross-fork packages, over-budget packages and missing world rules block;
- claims beyond the frozen cutoff (spoilers) block;
- BranchSuggestion is a disabled-by-default candidate output: schema/default
  enforcement, no auto-fork, no approval grant/reuse;
- the gates and fixtures are provider-free, DB-free and never write Original
  Canon / User Interpretation / Fanfiction Canon (T-37-03-01/02/SC).
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from app.services.derivative_generation.candidate import GateVerdict, parse_candidate
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
    qualify_fixture,
)
from app.services.derivative_generation.gates import (
    CODE_BRANCH_EVIDENCE_OUTSIDE_PACKAGE,
    CODE_CHARACTER_CONTRADICTION,
    CODE_CLUE_CONTRADICTION,
    CODE_CROSS_FORK,
    CODE_CUTOFF_EXCEEDED,
    CODE_DIVERGENCE_REQUIRES_OVERRIDE,
    CODE_EVIDENCE_OUTSIDE_PACKAGE,
    CODE_FACT_CONTRADICTION,
    CODE_PACKAGE_HASH_MISMATCH,
    CODE_TIMELINE_CONTRADICTION,
    ContinuityClaim,
    evaluate_consistency,
)

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX64 = "a" * 64

SERVICE_DIR = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "derivative_generation"
)
GATES_SOURCE = (SERVICE_DIR / "gates.py").read_text(encoding="utf-8")
FIXTURES_SOURCE = (SERVICE_DIR / "fixtures.py").read_text(encoding="utf-8")


def _run(candidate_json, package, *, claims=None, **overrides):
    draft = parse_candidate(candidate_json)
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
# Intentional violations fail closed and are locatable (REQ-CRE-06 / D-37-03)
# ---------------------------------------------------------------------------


def test_wrong_character_knowledge_fails_closed_and_locatable():
    package = build_package()
    result = _run(
        build_candidate_json(draft="阿宁宣告他已经知道竹林里的秘密。"),
        package,
        claims=[
            _claim(
                claim_key="hero-knows-secret",
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
    violation = next(
        v for v in result.violations if v.code == CODE_CHARACTER_CONTRADICTION
    )
    assert "world_state" in violation.field
    assert "hero" in violation.detail or violation.claim_key == "hero-knows-secret"


def test_impossible_causal_order_fails_closed():
    package = build_package()
    result = _run(
        build_candidate_json(draft="阿宁回村之后才踏入竹林。"),
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


def test_unresolved_clue_incorrectly_paid_off_fails_closed():
    package = build_package()
    result = _run(
        build_candidate_json(draft="金色脚印之谜已然解开。"),
        package,
        claims=[
            _claim(
                claim_key="payoff",
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
    assert "unresolved_clues" in violation.field


def test_established_fact_violation_fails_closed():
    package = build_package()
    result = _run(
        build_candidate_json(draft="死者真的复活了。"),
        package,
        claims=[
            _claim(
                claim_key="fact",
                category="established_fact",
                evidence_keys=[CANDIDATE_EVIDENCE_1],
                chapter_number=1,
                disposition="contradiction",
            )
        ],
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_FACT_CONTRADICTION)


def test_violation_never_repairs_package_or_candidate():
    """Fail closed means no auto-correction to canon and no mutation (D-37-03)."""
    package = build_package()
    before = copy.deepcopy(package)
    result = _run(
        build_candidate_json(
            draft="阿宁宣告他知道秘密。", citations=[OUTSIDE_EVIDENCE]
        ),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.reason == CODE_EVIDENCE_OUTSIDE_PACKAGE
    # The sealed package payload is untouched by the gate.
    assert package == before
    # The candidate keeps its reason; a blocked run is never a promotion.
    assert result.detail is not None


def test_stale_evidence_after_seal_is_detected():
    """A forged/stale evidence item invalidates the stored package hash."""
    payload = build_fixture("valid-continuation")
    stored_hash = payload["package_hash"]
    tampered = copy.deepcopy(payload["package"])
    tampered["dimensions"]["evidence"]["items"][0]["content_hash"] = "f" * 64
    assert package_hash(tampered) != stored_hash
    result = _run(
        payload["candidate"],
        tampered,
        expected_package_hash=stored_hash,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_PACKAGE_HASH_MISMATCH)


def test_cross_fork_package_fails_closed():
    package = build_package(fork_id=7)
    result = _run(
        build_candidate_json(draft="阿宁走入竹林。"),
        package,
        owner_id=2,
        novel_id=2,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_CROSS_FORK)


def test_over_budget_package_fails_closed_never_publishes():
    package = build_package()
    forged = copy.deepcopy(package)
    forged["budget_estimate"] = {"blocked": True, "block_reason": "budget_exhausted"}
    result = _run(build_candidate_json(draft="阿宁走入竹林。"), forged)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("budget_exhausted")
    assert result.detail is not None


def test_missing_world_rule_fails_closed():
    package = build_package(
        dimensions={
            **build_package()["dimensions"],
            "world_rules": dimension_view(status=DimensionStatus.UNAVAILABLE),
        }
    )
    result = _run(build_candidate_json(draft="阿宁走入竹林。"), package)
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code("dimension_unavailable:world_rules")


def test_spoiler_beyond_cutoff_fails_closed():
    """A claim beyond the frozen cutoff is blocked before any use (T-37-03-02)."""
    package = build_package()  # through_chapter = 2
    result = _run(
        build_candidate_json(draft="第三章才揭露的秘密。"),
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


def test_evidence_outside_package_is_never_silently_dropped():
    package = build_package()
    result = _run(
        build_candidate_json(draft="阿宁走入竹林。", citations=[OUTSIDE_EVIDENCE]),
        package,
    )
    assert result.verdict is GateVerdict.BLOCKED
    assert result.has_code(CODE_EVIDENCE_OUTSIDE_PACKAGE)
    # The forged ref is retained in the violation, never silently dropped.
    violation = next(
        v for v in result.violations if v.code == CODE_EVIDENCE_OUTSIDE_PACKAGE
    )
    assert violation.evidence_keys == [OUTSIDE_EVIDENCE]


def test_branch_suggestion_evidence_outside_package_is_blocked():
    package = build_package()
    result = _run(
        build_candidate_json(
            draft="阿宁在竹林入口迟疑。",
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


# ---------------------------------------------------------------------------
# Frozen sample set is the qualification gate (REQ-CRE-06)
# ---------------------------------------------------------------------------


def test_frozen_qualification_set_never_silently_publishes_a_violation():
    """Every blocked fixture stays blocked; valid fixtures stay candidate."""
    for fixture_key in FIXTURE_KEYS:
        payload = build_fixture(fixture_key)
        ok, result, missing = qualify_fixture(payload)
        assert ok, (
            f"{fixture_key} diverged: verdict={result.verdict.value} missing={missing}"
        )
        if payload["expected_verdict"] == "blocked":
            assert result.verdict is GateVerdict.BLOCKED
            assert result.reason is not None
        elif payload["expected_verdict"] == "needs_override":
            assert result.verdict is GateVerdict.NEEDS_OVERRIDE
            assert result.reason == CODE_DIVERGENCE_REQUIRES_OVERRIDE


def test_frozen_fixture_hash_is_deterministic_across_builds():
    hashes = {
        fixture_key: build_fixture(fixture_key)["fixture_hash"]
        for fixture_key in FIXTURE_KEYS
    }
    again = {
        fixture_key: build_fixture(fixture_key)["fixture_hash"]
        for fixture_key in FIXTURE_KEYS
    }
    assert hashes == again
    assert len(set(hashes.values())) == len(FIXTURE_KEYS)


def test_fixture_mutation_fails_the_qualification_gate():
    payload = copy.deepcopy(build_fixture("valid-continuation"))
    payload["claims"].append({"claim_key": "x", "category": "timeline"})
    with pytest.raises(Exception):
        qualify_fixture(payload)


def test_provider_success_alone_is_not_a_pass():
    """A structurally valid candidate that violates canon is still blocked."""
    for violation_fixture in (
        "contradictory-character-action",
        "impossible-timeline-order",
        "unresolved-clue-payoff",
    ):
        payload = build_fixture(violation_fixture)
        ok, result, missing = qualify_fixture(payload)
        assert result.verdict is GateVerdict.BLOCKED
        assert ok


# ---------------------------------------------------------------------------
# BranchSuggestion: disabled-by-default, no auto-fork, no approval reuse
# ---------------------------------------------------------------------------


def test_branch_suggestion_default_enabled_is_forbidden_at_schema():
    """An enabled-by-default BranchSuggestion is a schema failure (D-37-05)."""
    import json

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


def test_branch_suggestion_cannot_carry_approval_fields():
    """BranchSuggestion has no approval field; extra fields fail closed."""
    import json

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
                            "enabled_by_default": False,
                            "approval": "divergence-approved",
                        }
                    ],
                }
            )
        )


def test_gate_never_reuses_divergence_or_publish_approval():
    """No approval identifier is reachable from the gates module (REQ-FORK-06)."""
    for identifier in (
        "allow_divergence",
        "publish_derivative_revision",
        "approval_state",
        "DivergenceApproval",
        "PublicationApproval",
    ):
        assert identifier not in GATES_SOURCE, f"gates must not touch {identifier!r}"
        assert identifier not in FIXTURES_SOURCE, (
            f"fixtures must not touch {identifier!r}"
        )


def test_branch_suggestions_are_candidate_only_outputs():
    """A suggestion never creates a fork or changes Canon/branch state."""
    payload = build_fixture("branch-suggestion")
    package_before = copy.deepcopy(payload["package"])
    ok, result, _ = qualify_fixture(payload)
    assert ok and result.verdict is GateVerdict.CANDIDATE
    assert result.branch_suggestions
    # The gate left the frozen package untouched (no fork, no Canon write).
    assert payload["package"] == package_before
    for suggestion in result.branch_suggestions:
        assert suggestion["enabled_by_default"] is False


# ---------------------------------------------------------------------------
# AST: provider-free, DB-free, no Original/Interpretation/Fanfiction write
# ---------------------------------------------------------------------------


def test_gates_module_is_provider_and_db_free():
    """gates.py imports no model gateway, ORM session or network stack."""
    tree = ast.parse(GATES_SOURCE)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    for forbidden in (
        "litellm",
        "openai",
        "httpx",
        "aiohttp",
        "asyncio",
        "AsyncSession",
        "create_async_engine",
        "sqlalchemy",
    ):
        assert forbidden not in imported, f"gates must not import {forbidden!r}"
    # Everything imported is stdlib, pydantic or an ``app`` service module.
    for module in _imported_modules(GATES_SOURCE):
        assert module in {
            "__future__",
            "enum",
            "typing",
            "pydantic",
        } or module.startswith("app"), f"unexpected import {module!r} in gates.py"


def test_gates_and_fixtures_never_write_original_or_interpretation():
    """No Original Canon / User Interpretation write model is reachable."""
    for source in (GATES_SOURCE, FIXTURES_SOURCE):
        for forbidden in (
            "CanonSpaceArtifact",
            "original_canon",
            "user_interpretation",
        ):
            assert forbidden not in source, f"{forbidden!r} must not appear"


def test_gates_and_fixtures_never_construct_forks_or_packages():
    """No fork creation and no persistence write surface exists in 37-03."""
    for source in (GATES_SOURCE, FIXTURES_SOURCE):
        assert "CanonFork(" not in source
        assert "ClueActivePointer(" not in source
        assert "session.add" not in source
        assert "self._session" not in source


def _imported_modules(source: str) -> set[str]:
    """Absolute module names imported by a source file (AST)."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
    return modules


def test_no_third_party_dependency_introduced():
    """T-37-03-SC: the phase adds no package install (stdlib + app imports only)."""
    allowed = {"__future__", "json", "hashlib", "enum", "typing", "pydantic"}
    for source in (GATES_SOURCE, FIXTURES_SOURCE):
        for module in _imported_modules(source):
            assert module in allowed or module.startswith("app"), (
                f"unexpected third-party import {module!r} in 37-03 module"
            )
