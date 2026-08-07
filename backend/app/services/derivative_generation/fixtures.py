"""Frozen continuation qualification fixtures (Phase 37-03, REQ-CRE-06).

The frozen sample set is the qualification gate (REQ-CRE-06 / D-37-03): every
fixture is a sealed context package payload + a strict provider candidate +
optional structured continuity claims + the expected deterministic verdict. A
fixture that no longer reproduces its expected verdict fails the gate.

Categories (37-VALIDATION / 37-03-PLAN):

- scene-class valid continuations: ``high_action``, ``quiet_emotion``,
  ``visual_ambiguity``, ``valid_continuation``;
- intentional violations, each locatable to a package/candidate field:
  ``contradictory_character_action`` (character_contradiction),
  ``impossible_timeline_order`` (timeline_contradiction),
  ``unresolved_clue_payoff`` (clue_contradiction),
  ``missing_world_rule`` (dimension_unavailable:world_rules),
  ``invalid_citation`` (evidence_outside_package);
- explicit override: ``allowed_divergence`` (needs_override);
- candidate branch output: ``branch_suggestion`` (candidate + disabled
  suggestions).

All fixtures are provider-free and DB-free: the package is assembled with
``assemble_package_payload`` and sealed deterministically, the candidate is a
raw strict-schema JSON string, and the expected verdict is reproduced by
``evaluate_consistency`` (gates.py). The ``fixture_hash`` freezes the sample
set; any mutation is detected (T-37-03-01 / T-37-03-SC).
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from app.services.derivative_generation.candidate import parse_candidate
from app.services.derivative_generation.context_package import (
    DimensionStatus,
    assemble_package_payload,
    budget_verdict,
    dimension_view,
    package_hash,
)
from app.services.derivative_generation.gates import (
    ContinuityClaim,
    ConsistencyGateResult,
    evaluate_consistency,
)

FIXTURE_SCHEMA_VERSION = "derivative-continuity-fixtures.v1"
FIXTURE_HASH_PREFIX = "derivative-continuity-fixtures.v1:fixture"
CANDIDATE_EVIDENCE_1 = "fork:ff-fixtures:chapter:1"
CANDIDATE_EVIDENCE_2 = "fork:ff-fixtures:chapter:2"
OUTSIDE_EVIDENCE = "fork:ff-fixtures:future:99"

HEX64 = "a" * 64

# Frozen fixture categories (37-VALIDATION + scene classes from 37-03-PLAN).
FIXTURE_CATEGORIES = (
    "high_action",
    "quiet_emotion",
    "visual_ambiguity",
    "valid_continuation",
    "contradictory_character_action",
    "impossible_timeline_order",
    "unresolved_clue_payoff",
    "missing_world_rule",
    "invalid_citation",
    "allowed_divergence",
    "branch_suggestion",
)

FIXTURE_KEYS = (
    "high-action",
    "quiet-emotion",
    "visual-ambiguity",
    "valid-continuation",
    "contradictory-character-action",
    "impossible-timeline-order",
    "unresolved-clue-payoff",
    "missing-world-rule",
    "invalid-citation",
    "allowed-divergence",
    "branch-suggestion",
)


class FixtureError(ValueError):
    """Fail-closed fixture gate violation."""


# ---------------------------------------------------------------------------
# Deterministic fixture builders (pure, DB-free)
# ---------------------------------------------------------------------------


def _lineage(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "source_version_key": "original:fixtures",
        "source_snapshot_hash": HEX64,
        "through_chapter": 2,
        "full_book_authorized": False,
        "cutoff_snapshot_hash": HEX64,
        "scope_hash": HEX64,
        "manifest_hash": HEX64,
    }
    data.update(overrides)
    return data


def _world_state_items() -> list[dict[str, Any]]:
    return [
        {
            "entity_key": "hero",
            "entity_type": "character",
            "authority": "novel",
            "confidence": 0.95,
            "disclosure_cutoff": 1,
            "aliases": ["阿宁"],
            "source_refs": ["chapter:1"],
            "canonical_payload": {"status": "alive", "knows_secret": False},
            "canonical_payload_hash": HEX64,
        },
        {
            "entity_key": "villain",
            "entity_type": "character",
            "authority": "novel",
            "confidence": 0.9,
            "disclosure_cutoff": 1,
            "aliases": ["黑衣客"],
            "source_refs": ["chapter:1"],
            "canonical_payload": {"status": "alive", "identity_hidden": True},
            "canonical_payload_hash": HEX64,
        },
    ]


def _timeline_items() -> list[dict[str, Any]]:
    return [
        {
            "event_key": "event-a",
            "authority": "novel",
            "confidence": 0.95,
            "effective_start": 1,
            "effective_end": 1,
            "disclosure_cutoff": 1,
            "source_refs": ["chapter:1"],
            "canonical_payload": {"summary": "阿宁进入竹林"},
            "canonical_payload_hash": HEX64,
        },
        {
            "event_key": "event-b",
            "authority": "novel",
            "confidence": 0.9,
            "effective_start": 2,
            "effective_end": 2,
            "disclosure_cutoff": 1,
            "source_refs": ["chapter:1"],
            "canonical_payload": {"summary": "阿宁发现金色脚印"},
            "canonical_payload_hash": HEX64,
        },
        {
            "event_key": "event-c",
            "authority": "novel",
            "confidence": 0.9,
            "effective_start": 2,
            "effective_end": 2,
            "disclosure_cutoff": 2,
            "source_refs": ["chapter:2"],
            "canonical_payload": {"summary": "阿宁回到村中"},
            "canonical_payload_hash": HEX64,
        },
        {
            "edge_key": "edge-1",
            "source_event_key": "event-a",
            "target_event_key": "event-b",
            "edge_type": "cause",
            "authority": "novel",
            "confidence": 0.9,
            "disclosure_cutoff": 1,
            "source_refs": ["chapter:1"],
            "canonical_payload": {"summary": "进入竹林导致发现脚印"},
            "canonical_payload_hash": HEX64,
        },
        {
            "edge_key": "edge-2",
            "source_event_key": "event-b",
            "target_event_key": "event-c",
            "edge_type": "cause",
            "authority": "novel",
            "confidence": 0.85,
            "disclosure_cutoff": 2,
            "source_refs": ["chapter:2"],
            "canonical_payload": {"summary": "发现脚印后回村"},
            "canonical_payload_hash": HEX64,
        },
    ]


def _clue_items() -> list[dict[str, Any]]:
    return [
        {
            "logical_clue_id": "clue-1",
            "title": "金色脚印",
            "summary": "竹林中反复出现的金色脚印",
            "status": "active",
            "confidence": 0.8,
            "first_cue_chapter": 1,
            "package_hash": HEX64,
            "evidence_refs": [
                {
                    "role": "cue",
                    "evidence_id": "ev-1",
                    "evidence_identity": "chapter:1",
                    "narrative_chapter_number": 1,
                    "source_start": 0,
                    "source_end": 12,
                    "content_hash": HEX64,
                }
            ],
        }
    ]


def _rule_items() -> list[dict[str, Any]]:
    return [
        {
            "rule_key": "no-magic-resurrection",
            "authority": "novel",
            "confidence": 0.95,
            "disclosure_cutoff": 1,
            "source_refs": ["chapter:1"],
            "canonical_payload": {"rule": "the dead cannot return to life"},
            "canonical_payload_hash": HEX64,
        }
    ]


def _evidence_items() -> list[dict[str, Any]]:
    return [
        {
            "candidate_key": CANDIDATE_EVIDENCE_1,
            "chapter_number": 1,
            "source_start": 0,
            "source_end": 120,
            "content_hash": HEX64,
        },
        {
            "candidate_key": CANDIDATE_EVIDENCE_2,
            "chapter_number": 2,
            "source_start": 0,
            "source_end": 140,
            "content_hash": HEX64,
        },
    ]


def _base_dimensions(**overrides: Any) -> dict[str, Any]:
    data = {
        "world_state": dimension_view(
            status=DimensionStatus.AVAILABLE, items=_world_state_items()
        ),
        "timeline": dimension_view(
            status=DimensionStatus.AVAILABLE, items=_timeline_items()
        ),
        "unresolved_clues": dimension_view(
            status=DimensionStatus.AVAILABLE, items=_clue_items()
        ),
        "world_rules": dimension_view(
            status=DimensionStatus.AVAILABLE, items=_rule_items()
        ),
        "evidence": dimension_view(
            status=DimensionStatus.AVAILABLE, items=_evidence_items()
        ),
        "user_intent": {
            "status": DimensionStatus.AVAILABLE.value,
            "kind": "continuation",
            "hash": HEX64,
        },
    }
    data.update(overrides)
    return data


def build_package(
    *,
    dimensions: dict[str, Any] | None = None,
    intent: str = "continuation",
    fork_id: int = 7,
) -> dict[str, Any]:
    """Assemble and seal one frozen context package payload."""
    core = assemble_package_payload(
        owner_id=1,
        novel_id=1,
        fork_id=fork_id,
        fork_key="ff-fixtures",
        intent=intent,
        lineage=_lineage(),
        dimensions=dimensions if dimensions is not None else _base_dimensions(),
        budget_estimate={},
    )
    core["budget_estimate"] = budget_verdict(core)
    return core


def build_candidate_json(
    *,
    draft: str,
    citations: list[str] | None = None,
    divergence: dict[str, Any] | None = None,
    branch: list[dict[str, Any]] | None = None,
    intent: str = "continuation",
    summary: str | None = None,
) -> str:
    """Strict provider response content for the frozen candidate."""
    payload = {
        "schema_version": "derivative-candidate.v1",
        "intent": intent,
        "draft_text": draft,
        "summary": summary,
        "citation_keys": citations or [CANDIDATE_EVIDENCE_1],
        "divergence": divergence,
        "branch_suggestions": branch or [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _divergence_payload(
    *, divergence_type: str = "character", reason: str, affected: list[str]
) -> dict[str, Any]:
    return {
        "divergence_type": divergence_type,
        "reason": reason,
        "affected_evidence": affected,
        "scope": "derivative",
    }


def _branch_payload() -> list[dict[str, Any]]:
    return [
        {
            "choice_text": "阿宁循着脚印深入竹林",
            "branch_summary": "继续追踪金色脚印，进入竹林深处",
            "triggering_conflict": "脚印在竹林深处消失",
            "canon_delta_hash": HEX64,
            "evidence_refs": [CANDIDATE_EVIDENCE_1],
            "enabled_by_default": False,
        }
    ]


# ---------------------------------------------------------------------------
# Fixture assembly + freezing
# ---------------------------------------------------------------------------


def canonical_fixture_bytes(payload: dict[str, Any]) -> bytes:
    """Canonical JSON of the fixture excluding its own ``fixture_hash``."""
    body = {k: v for k, v in payload.items() if k != "fixture_hash"}
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def fixture_hash(payload: dict[str, Any]) -> str:
    encoded = canonical_fixture_bytes(payload)
    return sha256(f"{FIXTURE_HASH_PREFIX}\n".encode("utf-8") + encoded).hexdigest()


def _assemble_fixture(
    *,
    fixture_key: str,
    category: str,
    description: str,
    package: dict[str, Any],
    candidate: str,
    claims: list[dict[str, Any]] | None = None,
    expected_verdict: str,
    expected_reasons: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "fixture_key": fixture_key,
        "category": category,
        "description": description,
        "through_chapter": package.get("version", {}).get("through_chapter"),
        "package": package,
        "package_hash": package_hash(package),
        "candidate": candidate,
        "claims": list(claims or []),
        "expected_verdict": expected_verdict,
        "expected_reasons": list(expected_reasons or []),
    }
    payload["fixture_hash"] = fixture_hash(payload)
    return payload


def build_fixture(fixture_key: str) -> dict[str, Any]:
    """Build one frozen fixture by key; unknown keys fail closed."""
    builders: dict[str, Any] = {
        "high-action": lambda: _assemble_fixture(
            fixture_key="high-action",
            category="high_action",
            description="high-action continuation is internally consistent",
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁拔刀斩开扑来的黑影，在竹林间疾速穿行。",
                citations=[CANDIDATE_EVIDENCE_1],
            ),
            claims=[
                {
                    "claim_key": "high-action-hero",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "consistent",
                }
            ],
            expected_verdict="candidate",
        ),
        "quiet-emotion": lambda: _assemble_fixture(
            fixture_key="quiet-emotion",
            category="quiet_emotion",
            description="quiet-emotion continuation is internally consistent",
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁在竹影下停住脚步，望着村口灯火沉默良久。",
                citations=[CANDIDATE_EVIDENCE_2],
            ),
            claims=[
                {
                    "claim_key": "quiet-emotion-hero",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_2],
                    "chapter_number": 2,
                    "disposition": "consistent",
                }
            ],
            expected_verdict="candidate",
        ),
        "visual-ambiguity": lambda: _assemble_fixture(
            fixture_key="visual-ambiguity",
            category="visual_ambiguity",
            description="visually ambiguous continuation keeps all facts intact",
            package=build_package(),
            candidate=build_candidate_json(
                draft="雾气中那道金色身影既像人又不像人。",
                citations=[CANDIDATE_EVIDENCE_2],
            ),
            claims=[
                {
                    "claim_key": "visual-ambiguity-clue",
                    "category": "clue",
                    "clue_id": "clue-1",
                    "evidence_keys": [CANDIDATE_EVIDENCE_2],
                    "chapter_number": 2,
                    "disposition": "consistent",
                }
            ],
            expected_verdict="candidate",
        ),
        "valid-continuation": lambda: _assemble_fixture(
            fixture_key="valid-continuation",
            category="valid_continuation",
            description="valid continuation: all facts/timeline/clues consistent",
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁跟随金色脚印走向竹林深处，脚步既轻又稳。",
            ),
            claims=[
                {
                    "claim_key": "valid-hero",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "consistent",
                },
                {
                    "claim_key": "valid-timeline",
                    "category": "timeline",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "consistent",
                },
            ],
            expected_verdict="candidate",
        ),
        "contradictory-character-action": lambda: _assemble_fixture(
            fixture_key="contradictory-character-action",
            category="contradictory_character_action",
            description=(
                "hero acts on secret knowledge the frozen package says he does not have"
            ),
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁向全村宣告他已经知道竹林里的秘密。",
                citations=[CANDIDATE_EVIDENCE_1],
            ),
            claims=[
                {
                    "claim_key": "hero-knows-secret",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "contradiction",
                }
            ],
            expected_verdict="blocked",
            expected_reasons=["character_contradiction"],
        ),
        "impossible-timeline-order": lambda: _assemble_fixture(
            fixture_key="impossible-timeline-order",
            category="impossible_timeline_order",
            description=(
                "candidate asserts event-c precedes event-a while the causal "
                "edges say event-a -> event-b -> event-c"
            ),
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁回到村中之后，才第一次踏入竹林。",
                citations=[CANDIDATE_EVIDENCE_2],
            ),
            claims=[
                {
                    "claim_key": "causal-order",
                    "category": "timeline",
                    "evidence_keys": [CANDIDATE_EVIDENCE_2],
                    "chapter_number": 2,
                    "disposition": "contradiction",
                }
            ],
            expected_verdict="blocked",
            expected_reasons=["timeline_contradiction"],
        ),
        "unresolved-clue-payoff": lambda: _assemble_fixture(
            fixture_key="unresolved-clue-payoff",
            category="unresolved_clue_payoff",
            description=("candidate pays off clue-1 while the package marks it active"),
            package=build_package(),
            candidate=build_candidate_json(
                draft="金色脚印之谜已然解开：那只是村民甲的恶作剧。",
                citations=[CANDIDATE_EVIDENCE_1],
            ),
            claims=[
                {
                    "claim_key": "clue-paid-off",
                    "category": "clue",
                    "clue_id": "clue-1",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "contradiction",
                }
            ],
            expected_verdict="blocked",
            expected_reasons=["clue_contradiction"],
        ),
        "missing-world-rule": lambda: _assemble_fixture(
            fixture_key="missing-world-rule",
            category="missing_world_rule",
            description=(
                "world_rules dimension is unavailable; the continuation cannot "
                "be qualified against rules the package does not contain"
            ),
            package=build_package(
                dimensions=_base_dimensions(
                    world_rules=dimension_view(status=DimensionStatus.UNAVAILABLE)
                )
            ),
            candidate=build_candidate_json(
                draft="阿宁在竹林里走得越来越深。",
                citations=[CANDIDATE_EVIDENCE_1],
            ),
            claims=[],
            expected_verdict="blocked",
            expected_reasons=["dimension_unavailable:world_rules"],
        ),
        "invalid-citation": lambda: _assemble_fixture(
            fixture_key="invalid-citation",
            category="invalid_citation",
            description="candidate cites evidence outside the sealed package",
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁在竹林里走得越来越深。",
                citations=[OUTSIDE_EVIDENCE],
            ),
            claims=[],
            expected_verdict="blocked",
            expected_reasons=["evidence_outside_package"],
        ),
        "allowed-divergence": lambda: _assemble_fixture(
            fixture_key="allowed-divergence",
            category="allowed_divergence",
            description=(
                "explicit CanonDelta declares a character divergence; the "
                "contradiction is covered and never auto-accepted"
            ),
            package=build_package(),
            candidate=build_candidate_json(
                draft="为故事转折，阿宁在结尾露出他其实早已知晓秘密的神情。",
                citations=[CANDIDATE_EVIDENCE_1],
                divergence=_divergence_payload(
                    divergence_type="character",
                    reason="the twist requires the hero to know the secret early",
                    affected=[CANDIDATE_EVIDENCE_1],
                ),
            ),
            claims=[
                {
                    "claim_key": "declared-divergence",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "contradiction",
                }
            ],
            expected_verdict="needs_override",
            expected_reasons=[],
        ),
        "branch-suggestion": lambda: _assemble_fixture(
            fixture_key="branch-suggestion",
            category="branch_suggestion",
            description=(
                "candidate carries a disabled-by-default BranchSuggestion bound "
                "to a conflict, canon delta hash and evidence refs"
            ),
            package=build_package(),
            candidate=build_candidate_json(
                draft="阿宁在竹林入口迟疑片刻，终于迈步。",
                citations=[CANDIDATE_EVIDENCE_1],
                branch=_branch_payload(),
            ),
            claims=[
                {
                    "claim_key": "branch-main",
                    "category": "character_behavior",
                    "entity_key": "hero",
                    "evidence_keys": [CANDIDATE_EVIDENCE_1],
                    "chapter_number": 1,
                    "disposition": "consistent",
                }
            ],
            expected_verdict="candidate",
            expected_reasons=[],
        ),
    }
    if fixture_key not in builders:
        raise FixtureError(f"unknown frozen fixture key: {fixture_key!r}")
    return builders[fixture_key]()


def build_all_fixtures() -> dict[str, dict[str, Any]]:
    """Return all frozen fixtures keyed by ``fixture_key`` (frozen set)."""
    return {key: build_fixture(key) for key in FIXTURE_KEYS}


def verify_fixture_hash(payload: dict[str, Any]) -> None:
    """Replay the fixture hash; a mutated fixture fails closed (T-37-03-01)."""
    expected = payload.get("fixture_hash")
    if fixture_hash(payload) != expected:
        raise FixtureError(
            f"fixture {payload.get('fixture_key')!r} hash does not replay; "
            "the frozen sample set was mutated"
        )


# ---------------------------------------------------------------------------
# Qualification gate (REQ-CRE-06: the frozen set is the gate)
# ---------------------------------------------------------------------------


def qualify_fixture(
    payload: dict[str, Any],
) -> tuple[bool, ConsistencyGateResult, list[str]]:
    """Run the deterministic gates and compare against the frozen expectation.

    Returns ``(ok, result, missing_reasons)`` where ``ok`` means the verdict
    matches ``expected_verdict`` and every ``expected_reasons`` code is present
    among the gate violations.
    """
    verify_fixture_hash(payload)
    package = payload["package"]
    # Re-seal the package hash: qualification always replays the package.
    expected_hash = package_hash(package)
    draft = parse_candidate(payload["candidate"])
    claims = [ContinuityClaim.model_validate(c) for c in payload.get("claims") or []]
    result = evaluate_consistency(
        draft,
        package,
        owner_id=package["owner_id"],
        novel_id=package["novel_id"],
        fork_id=package["fork_id"],
        expected_package_hash=expected_hash,
        package_intent=package["intent"],
        claims=claims,
    )
    verdict_ok = result.verdict.value == payload["expected_verdict"]
    codes = {violation.code for violation in result.violations}
    missing = [
        reason
        for reason in payload.get("expected_reasons") or []
        if reason not in codes
    ]
    return verdict_ok and not missing, result, missing


def run_frozen_qualification() -> list[tuple[str, bool, str, list[str]]]:
    """Qualify the whole frozen set; returns (key, ok, verdict, missing)."""
    results: list[tuple[str, bool, str, list[str]]] = []
    for fixture_key in FIXTURE_KEYS:
        payload = build_fixture(fixture_key)
        ok, result, missing = qualify_fixture(payload)
        results.append((fixture_key, ok, result.verdict.value, missing))
    return results


__all__ = [
    "CANDIDATE_EVIDENCE_1",
    "CANDIDATE_EVIDENCE_2",
    "FIXTURE_CATEGORIES",
    "FIXTURE_HASH_PREFIX",
    "FIXTURE_KEYS",
    "FIXTURE_SCHEMA_VERSION",
    "OUTSIDE_EVIDENCE",
    "FixtureError",
    "build_all_fixtures",
    "build_candidate_json",
    "build_fixture",
    "build_package",
    "canonical_fixture_bytes",
    "fixture_hash",
    "qualify_fixture",
    "run_frozen_qualification",
    "verify_fixture_hash",
]
