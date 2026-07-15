"""Adversarial spoiler, cross-version, chat-only and source_unavailable gates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.schemas.clue import ClueLifecycleState
from app.services.clues.evidence import (
    build_clue_evidence_package,
    make_clue_evidence_unit,
)
from app.services.clues.gates import ClueGateService
from app.services.clues.query import derive_visible_state
from app.services.clues.sources import (
    NullRelationshipObservationSource,
    UnavailableRelationshipObservationSource,
    reject_freeform_chat_as_evidence,
)

pytestmark = pytest.mark.unit

HEX = "a" * 64
HEX_B = "b" * 64


def _event(
    *,
    eid: int,
    from_status: str,
    to_status: str,
    identities: list[str] | None = None,
    cue_chapter: int | None = None,
    payoff_chapter: int | None = None,
):
    return SimpleNamespace(
        id=eid,
        from_status=from_status,
        to_status=to_status,
        actor_source="machine",
        reason=f"{from_status}->{to_status}",
        event_key=f"k{eid}",
        evidence_identities=identities or [],
        cue_chapter=cue_chapter,
        payoff_chapter=payoff_chapter,
    )


def test_spoiler_hides_paid_off_until_payoff_cutoff():
    events = [
        _event(
            eid=1,
            from_status="candidate",
            to_status="active",
            identities=[f"cue:1:0:10:{HEX}"],
            cue_chapter=1,
        ),
        _event(
            eid=2,
            from_status="active",
            to_status="reinforced",
            identities=[f"re:2:0:10:{HEX}"],
        ),
        _event(
            eid=3,
            from_status="reinforced",
            to_status="paid_off",
            identities=[f"cue:1:0:10:{HEX}", f"pay:9:0:10:{HEX}"],
            cue_chapter=1,
            payoff_chapter=9,
        ),
    ]
    before = derive_visible_state(events, cutoff=3)
    assert before == ClueLifecycleState.REINFORCED
    assert before != ClueLifecycleState.PAID_OFF
    full = derive_visible_state(events, cutoff=None)
    assert full == ClueLifecycleState.PAID_OFF


def test_future_only_clue_hidden_before_first_cue_chapter():
    events = [
        _event(
            eid=1,
            from_status="candidate",
            to_status="active",
            identities=[f"future:9:0:10:{HEX}"],
            cue_chapter=9,
        ),
    ]
    early = derive_visible_state(events, cutoff=1)
    assert early == ClueLifecycleState.CANDIDATE


def test_chat_only_text_rejected_as_lifecycle_evidence():
    result = reject_freeform_chat_as_evidence(
        "READER_CHAT says this is paid off foreshadow"
    )
    assert result.status == "rejected"
    assert result.reason_code == "chat_freeform_forbidden"
    assert result.items == []


def test_chat_similarity_cannot_accept_active_or_paid_off():
    cue = make_clue_evidence_unit(
        evidence_id="ev-chat",
        chapter_id=1,
        narrative_chapter_number=1,
        text="A motif only appears here for testing gates.",
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-chat-later",
        chapter_id=5,
        narrative_chapter_number=5,
        text="A motif only appears later for testing gates.",
        role_hint="later",
    )
    package = build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id="clue-chat-adv",
        source_snapshot_hash=HEX,
        hierarchy_build_id="b",
        hierarchy_checksum=HEX_B,
        cue_units=[cue],
        later_units=[later],
        recall_signals={
            "vector": {"score": 0.999},
            "chat_assertion": {"text": "definitely a payoff"},
        },
    )
    gates = ClueGateService()
    active = gates.evaluate_transition(
        package=package,
        judgment={
            "schema_version": "clue-semantic-judgment.v1",
            "candidate_id": package.candidate_id,
            "classification": "cue_only",
            "cue_evidence_ids": package.cue_ids(),
            "later_evidence_ids": [],
            "confidence": 0.99,
            "conflict_flags": ["MOTIF_ONLY"],
            "rationale": "chat said so",
        },
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    paid = gates.evaluate_transition(
        package=package,
        judgment={
            "schema_version": "clue-semantic-judgment.v1",
            "candidate_id": package.candidate_id,
            "classification": "payoff",
            "cue_evidence_ids": package.cue_ids(),
            "later_evidence_ids": package.later_ids(),
            "confidence": 0.99,
            "conflict_flags": ["MOTIF_ONLY"],
            "rationale": "chat said so",
        },
        from_status="reinforced",
        to_status="paid_off",
        owner_id=1,
        novel_id=2,
    )
    assert active.accepted is False
    assert paid.accepted is False


@pytest.mark.asyncio
async def test_relationship_source_unavailable_is_explicit_not_empty_success():
    src = UnavailableRelationshipObservationSource(detail="phase09_outage")
    result = await src.list_observations(owner_id=1, novel_id=2, analysis_version_id=3)
    assert result.status == "source_unavailable"
    assert result.items == []
    assert result.is_unavailable is True

    null = NullRelationshipObservationSource()
    null_result = await null.list_observations(owner_id=1, novel_id=2)
    assert null_result.status == "source_unavailable"
    # Must not look like healthy empty success
    assert null_result.status != "empty"
    assert null_result.recall_signals()["relationship"]["status"] == "source_unavailable"


def test_cross_owner_scope_rejected_by_gate():
    cue = make_clue_evidence_unit(
        evidence_id="ev-v",
        chapter_id=1,
        narrative_chapter_number=1,
        text="versioned cue text for scope gate tests.",
        role_hint="cue",
    )
    package = build_clue_evidence_package(
        owner_id=10,
        novel_id=20,
        candidate_id="clue-version",
        source_snapshot_hash=HEX,
        hierarchy_build_id="build-a",
        hierarchy_checksum=HEX_B,
        cue_units=[cue],
        later_units=[],
        recall_signals={},
    )
    judgment = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": package.candidate_id,
        "classification": "cue_only",
        "cue_evidence_ids": package.cue_ids(),
        "later_evidence_ids": [],
        "confidence": 0.95,
        "conflict_flags": [],
        "rationale": "ok",
    }
    decision = ClueGateService().evaluate_transition(
        package=package,
        judgment=judgment,
        from_status="candidate",
        to_status="active",
        owner_id=999,
        novel_id=20,
    )
    assert decision.accepted is False
    assert any("scope" in f for f in decision.gate_failures) or "scope_failed" in decision.reason_codes


def test_critical_adversarial_counts_are_zero_on_gate_set():
    cases = [
        ("motif", "MOTIF_ONLY", "cue_only", "active"),
        ("shared", "INSUFFICIENT_PAYOFF", "payoff", "paid_off"),
        ("vector", "MOTIF_ONLY", "cue_only", "active"),
    ]
    false_active = 0
    false_paid_off = 0
    for name, flag, classification, to_status in cases:
        cue = make_clue_evidence_unit(
            evidence_id=f"ev-{name}",
            chapter_id=1,
            narrative_chapter_number=1,
            text=f"cue text for {name} hard negative case xx",
            role_hint="cue",
        )
        later = make_clue_evidence_unit(
            evidence_id=f"ev-{name}-l",
            chapter_id=4,
            narrative_chapter_number=4,
            text=f"later text for {name} hard negative case xx",
            role_hint="later",
        )
        package = build_clue_evidence_package(
            owner_id=1,
            novel_id=1,
            candidate_id=f"cand-{name}",
            source_snapshot_hash=HEX,
            hierarchy_build_id="b",
            hierarchy_checksum=HEX_B,
            cue_units=[cue],
            later_units=[later],
            recall_signals={"vector": {"score": 0.99}},
        )
        judgment = {
            "schema_version": "clue-semantic-judgment.v1",
            "candidate_id": package.candidate_id,
            "classification": classification,
            "cue_evidence_ids": package.cue_ids(),
            "later_evidence_ids": package.later_ids()
            if classification == "payoff"
            else [],
            "confidence": 0.99,
            "conflict_flags": [flag],
            "rationale": name,
        }
        from_status = "reinforced" if to_status == "paid_off" else "candidate"
        decision = ClueGateService().evaluate_transition(
            package=package,
            judgment=judgment,
            from_status=from_status,
            to_status=to_status,
            owner_id=1,
            novel_id=1,
        )
        if decision.accepted and to_status == "active":
            false_active += 1
        if decision.accepted and to_status == "paid_off":
            false_paid_off += 1
    assert false_active == 0
    assert false_paid_off == 0
