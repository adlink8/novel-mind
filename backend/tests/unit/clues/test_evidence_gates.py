"""Evidence, temporal, threshold and conflict gates for clue lifecycle."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.clues.evidence import (
    build_clue_evidence_package,
    make_clue_evidence_unit,
)
from app.services.clues.gates import (
    AUTO_ACCEPT_THRESHOLD,
    REVIEW_THRESHOLD,
    ClueGateService,
    policy_hash,
)

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64


def _package(**overrides: Any):
    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=1,
        narrative_chapter_number=1,
        text="a silver key under the ash gate",
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-later",
        chapter_id=5,
        narrative_chapter_number=5,
        text="unlocked the vault with the silver key",
        role_hint="later",
        source_start=0,
    )
    reinf = make_clue_evidence_unit(
        evidence_id="ev-reinf",
        chapter_id=3,
        narrative_chapter_number=3,
        text="Bob mentioned the silver key again",
        role_hint="later",
    )
    kwargs = dict(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-gate",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[reinf, later],
        recall_signals={"vector": {"score": 0.99}},
    )
    kwargs.update(overrides)
    return build_clue_evidence_package(**kwargs)


def _judgment(package=None, **overrides: Any) -> dict[str, Any]:
    package = package or _package()
    payload = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": package.candidate_id,
        "classification": "cue_only",
        "cue_evidence_ids": package.cue_ids(),
        "later_evidence_ids": [],
        "confidence": 0.9,
        "conflict_flags": [],
        "rationale": "early cue is concrete and foreshadowing",
    }
    payload.update(overrides)
    return payload


def test_active_requires_cue_evidence():
    gates = ClueGateService()
    package = _package()
    decision = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package, classification="cue_only"),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
        hierarchy_build_id="build-1",
    )
    assert decision.accepted is True
    assert decision.status == "accepted"

    # Wrong classification
    bad = gates.evaluate_transition(
        package=package,
        judgment=_judgment(
            package, classification="payoff", later_evidence_ids=package.later_ids()[:1]
        ),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert bad.accepted is False
    assert any("classification_mismatch" in f for f in bad.gate_failures)


def test_reinforced_requires_new_later_evidence():
    gates = ClueGateService()
    package = _package()
    judgment = _judgment(
        package,
        classification="reinforcement",
        cue_evidence_ids=package.cue_ids(),
        later_evidence_ids=["ev-reinf"],
        confidence=0.9,
        rationale="new reinforcement later in narrative",
    )
    ok = gates.evaluate_transition(
        package=package,
        judgment=judgment,
        from_status="active",
        to_status="reinforced",
        owner_id=1,
        novel_id=2,
    )
    assert ok.accepted is True

    # Reusing consumed reinforcement identity fails.
    consumed_key = None
    for unit in package.later_units:
        if unit.evidence_id == "ev-reinf":
            consumed_key = unit.identity_key()
    assert consumed_key
    blocked = gates.evaluate_transition(
        package=package,
        judgment=judgment,
        from_status="reinforced",
        to_status="reinforced",
        owner_id=1,
        novel_id=2,
        consumed_evidence_ids={consumed_key},
    )
    assert blocked.accepted is False
    assert any("transition_gate" in f for f in blocked.gate_failures)


def test_paid_off_requires_cue_later_and_from_reinforced():
    gates = ClueGateService()
    package = _package()
    judgment = _judgment(
        package,
        classification="payoff",
        cue_evidence_ids=package.cue_ids(),
        later_evidence_ids=["ev-later"],
        confidence=0.92,
        rationale="vault unlock pays off the silver key cue",
    )
    from_active = gates.evaluate_transition(
        package=package,
        judgment=judgment,
        from_status="active",
        to_status="paid_off",
        owner_id=1,
        novel_id=2,
    )
    assert from_active.accepted is False
    assert any("illegal" in f or "transition" in f for f in from_active.gate_failures)

    ok = gates.evaluate_transition(
        package=package,
        judgment=judgment,
        from_status="reinforced",
        to_status="paid_off",
        owner_id=1,
        novel_id=2,
    )
    assert ok.accepted is True


def test_order_conflict_and_motif_only_block_publication():
    gates = ClueGateService()
    package = _package()
    order = gates.evaluate_transition(
        package=package,
        judgment=_judgment(
            package,
            classification="payoff",
            cue_evidence_ids=package.cue_ids(),
            later_evidence_ids=["ev-later"],
            confidence=0.95,
            conflict_flags=["ORDER_CONFLICT"],
            rationale="model flagged order conflict",
        ),
        from_status="reinforced",
        to_status="paid_off",
        owner_id=1,
        novel_id=2,
    )
    assert order.accepted is False
    assert any("ORDER_CONFLICT" in f for f in order.gate_failures)

    motif = gates.evaluate_transition(
        package=package,
        judgment=_judgment(
            package,
            classification="cue_only",
            confidence=0.99,
            conflict_flags=["MOTIF_ONLY"],
            rationale="only a repeated motif",
        ),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert motif.accepted is False
    assert any("MOTIF_ONLY" in f for f in motif.gate_failures)


def test_temporal_gate_rejects_later_before_cue():
    gates = ClueGateService()
    # Craft package where "later" is narratively earlier (should fail temporal).
    early = make_clue_evidence_unit(
        evidence_id="ev-early-later",
        chapter_id=1,
        narrative_chapter_number=1,
        text="later-labeled but early text",
        source_start=0,
        role_hint="later",
    )
    cue = make_clue_evidence_unit(
        evidence_id="ev-late-cue",
        chapter_id=4,
        narrative_chapter_number=4,
        text="cue appearing later",
        source_start=0,
        role_hint="cue",
    )
    package = build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-cand-order",
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[early],
    )
    decision = gates.evaluate_transition(
        package=package,
        judgment={
            "schema_version": "clue-semantic-judgment.v1",
            "candidate_id": package.candidate_id,
            "classification": "reinforcement",
            "cue_evidence_ids": ["ev-late-cue"],
            "later_evidence_ids": ["ev-early-later"],
            "confidence": 0.9,
            "conflict_flags": [],
            "rationale": "order inverted",
        },
        from_status="active",
        to_status="reinforced",
        owner_id=1,
        novel_id=2,
    )
    assert decision.accepted is False
    assert any("temporal" in f for f in decision.gate_failures)


def test_scope_mismatch_and_out_of_package_ids():
    gates = ClueGateService()
    package = _package()
    scope = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package),
        from_status="candidate",
        to_status="active",
        owner_id=999,
        novel_id=2,
    )
    assert scope.accepted is False
    assert any("owner_mismatch" in f for f in scope.gate_failures)

    forged = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package, cue_evidence_ids=["ev-missing"]),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert forged.accepted is False
    assert any("out_of_package" in f for f in forged.gate_failures)


def test_human_protected_dismissal_blocks_republication():
    gates = ClueGateService()
    package = _package()
    blocked = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
        human_protected_dismissed=True,
    )
    assert blocked.accepted is False
    assert any("human_protection" in f for f in blocked.gate_failures)


def test_unsupported_relation_ref_blocks():
    gates = ClueGateService()
    package = _package()
    decision = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
        relation_ref_validation="source_unavailable",
    )
    assert decision.accepted is False
    assert any("relation_ref" in f for f in decision.gate_failures)


def test_threshold_bands_and_policy_hash_stable():
    gates = ClueGateService()
    package = _package()
    low = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package, confidence=0.4),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert low.accepted is False
    assert low.gate_status == "threshold_failed"

    mid = gates.evaluate_transition(
        package=package,
        judgment=_judgment(
            package, confidence=(REVIEW_THRESHOLD + AUTO_ACCEPT_THRESHOLD) / 2
        ),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert mid.accepted is False
    assert mid.needs_review is True

    assert policy_hash() == policy_hash()
    assert len(policy_hash()) == 64


def test_gates_have_no_side_effects_and_stable_codes():
    import app.services.clues.gates as mod

    source = open(mod.__file__, encoding="utf-8").read()
    assert "session.add" not in source
    assert "AsyncSession" not in source
    assert "db.flush" not in source

    gates = ClueGateService()
    package = _package()
    d1 = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package, confidence=0.2),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    d2 = gates.evaluate_transition(
        package=package,
        judgment=_judgment(package, confidence=0.2),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert d1.gate_failures == d2.gate_failures
    assert d1.reason_codes == d2.reason_codes


def test_recall_only_helper_never_accepts():
    gates = ClueGateService()
    package = _package()
    decision = gates.evaluate_recall_only_rejection(package=package)
    assert decision.accepted is False
    assert "recall_only" in decision.reason_codes


def test_human_confirm_active_with_cue():
    gates = ClueGateService()
    package = _package()
    decision = gates.evaluate_transition(
        package=package,
        judgment=None,
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
        human_protected_confirm=True,
    )
    assert decision.accepted is True
    assert "human_confirm" in decision.reason_codes
