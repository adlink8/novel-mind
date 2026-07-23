"""Append-only lifecycle transition legality and evidence-order rules."""

from __future__ import annotations

import pytest

from app.schemas.clue import (
    ClueEvidenceRef,
    ClueLifecycleState,
    LifecycleEventInput,
    LifecycleTransitionError,
    is_legal_transition,
    replay_lifecycle,
    validate_evidence_for_transition,
    validate_lifecycle_event,
    validate_transition_legality,
)

pytestmark = pytest.mark.unit

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64


def _ev(
    evidence_id: str,
    role: str,
    chapter: int,
    start: int = 0,
    end: int = 20,
    content_hash: str = HEX64,
) -> ClueEvidenceRef:
    return ClueEvidenceRef.model_validate(
        {
            "evidence_id": evidence_id,
            "role": role,
            "chapter_id": chapter,
            "narrative_chapter_number": chapter,
            "source_start": start,
            "source_end": end,
            "content_hash": content_hash,
        }
    )


def test_legal_transition_table():
    assert is_legal_transition("candidate", "active")
    assert is_legal_transition("candidate", "dismissed")
    assert is_legal_transition("active", "reinforced")
    assert is_legal_transition("active", "dismissed")
    assert is_legal_transition("reinforced", "reinforced")
    assert is_legal_transition("reinforced", "paid_off")
    assert is_legal_transition("reinforced", "dismissed")

    illegal = [
        ("candidate", "reinforced"),
        ("candidate", "paid_off"),
        ("active", "candidate"),
        ("active", "paid_off"),
        ("reinforced", "active"),
        ("paid_off", "reinforced"),
        ("paid_off", "dismissed"),
        ("dismissed", "active"),
        ("dismissed", "candidate"),
    ]
    for src, dst in illegal:
        assert not is_legal_transition(src, dst)
        with pytest.raises(LifecycleTransitionError):
            validate_transition_legality(src, dst)


def test_active_requires_cue_evidence():
    with pytest.raises(LifecycleTransitionError, match="cue"):
        validate_evidence_for_transition("candidate", "active", [])
    validate_evidence_for_transition("candidate", "active", [_ev("c1", "cue", 1)])


def test_reinforced_requires_new_reinforcement_identity():
    reinf = _ev("r1", "reinforcement", 2, content_hash=HEX64_B)
    validate_evidence_for_transition("active", "reinforced", [reinf])

    with pytest.raises(LifecycleTransitionError, match="reinforcement"):
        validate_evidence_for_transition("active", "reinforced", [])

    with pytest.raises(LifecycleTransitionError, match="new reinforcement"):
        validate_evidence_for_transition(
            "reinforced",
            "reinforced",
            [reinf],
            consumed_evidence_ids={reinf.identity_key()},
        )


def test_paid_off_requires_distinct_later_payoff():
    cue = _ev("c1", "cue", 1, start=0, end=30, content_hash=HEX64)
    payoff = _ev("p1", "payoff", 8, start=5, end=40, content_hash=HEX64_B)

    validate_evidence_for_transition("reinforced", "paid_off", [cue, payoff])

    # Missing cue
    with pytest.raises(LifecycleTransitionError, match="cue"):
        validate_evidence_for_transition("reinforced", "paid_off", [payoff])

    # Missing payoff
    with pytest.raises(LifecycleTransitionError, match="payoff"):
        validate_evidence_for_transition("reinforced", "paid_off", [cue])

    # Same identity used as both (identical coordinates + hash + id)
    same = _ev("x1", "cue", 3, start=10, end=40, content_hash=HEX64_C)
    same_pay = _ev("x1", "payoff", 3, start=10, end=40, content_hash=HEX64_C)
    with pytest.raises(LifecycleTransitionError, match="distinct"):
        validate_evidence_for_transition("reinforced", "paid_off", [same, same_pay])

    # Payoff not later than cue
    early_pay = _ev("p0", "payoff", 1, start=0, end=10, content_hash=HEX64_B)
    with pytest.raises(LifecycleTransitionError, match="later"):
        validate_evidence_for_transition("reinforced", "paid_off", [cue, early_pay])

    # Cannot paid_off from active
    with pytest.raises(LifecycleTransitionError):
        validate_evidence_for_transition("active", "paid_off", [cue, payoff])


def test_dismissed_is_terminal_and_allows_empty_evidence():
    validate_evidence_for_transition("candidate", "dismissed", [])
    validate_evidence_for_transition(
        "active",
        "dismissed",
        [_ev("d1", "disposition", 2, content_hash=HEX64_B)],
    )
    with pytest.raises(LifecycleTransitionError):
        validate_transition_legality("dismissed", "active")
    with pytest.raises(LifecycleTransitionError):
        validate_transition_legality("paid_off", "reinforced")


def test_replay_lifecycle_happy_path_to_paid_off():
    cue = _ev("c1", "cue", 1)
    r1 = _ev("r1", "reinforcement", 3, content_hash=HEX64_B)
    r2 = _ev("r2", "reinforcement", 4, content_hash=HEX64_C)
    payoff = _ev("p1", "payoff", 9, content_hash="d" * 64)

    events = [
        LifecycleEventInput(
            from_status=ClueLifecycleState.CANDIDATE,
            to_status=ClueLifecycleState.ACTIVE,
            actor_source="machine",
            reason="cue",
            evidence=[cue],
            event_key="e1",
        ),
        LifecycleEventInput(
            from_status=ClueLifecycleState.ACTIVE,
            to_status=ClueLifecycleState.REINFORCED,
            actor_source="machine",
            reason="r1",
            evidence=[r1],
            event_key="e2",
        ),
        LifecycleEventInput(
            from_status=ClueLifecycleState.REINFORCED,
            to_status=ClueLifecycleState.REINFORCED,
            actor_source="machine",
            reason="r2",
            evidence=[r2],
            event_key="e3",
        ),
        LifecycleEventInput(
            from_status=ClueLifecycleState.REINFORCED,
            to_status=ClueLifecycleState.PAID_OFF,
            actor_source="machine",
            reason="payoff",
            evidence=[cue, payoff],
            event_key="e4",
        ),
    ]
    assert replay_lifecycle(events) == ClueLifecycleState.PAID_OFF


def test_replay_rejects_from_status_mismatch_and_duplicate_reinforcement():
    cue = _ev("c1", "cue", 1)
    r1 = _ev("r1", "reinforcement", 2, content_hash=HEX64_B)

    with pytest.raises(LifecycleTransitionError, match="does not match"):
        replay_lifecycle(
            [
                LifecycleEventInput(
                    from_status=ClueLifecycleState.ACTIVE,  # should be candidate
                    to_status=ClueLifecycleState.REINFORCED,
                    actor_source="machine",
                    reason="bad",
                    evidence=[r1],
                    event_key="e1",
                )
            ]
        )

    with pytest.raises(LifecycleTransitionError, match="new reinforcement"):
        replay_lifecycle(
            [
                LifecycleEventInput(
                    from_status=ClueLifecycleState.CANDIDATE,
                    to_status=ClueLifecycleState.ACTIVE,
                    actor_source="human",
                    reason="confirm",
                    evidence=[cue],
                    event_key="e1",
                ),
                LifecycleEventInput(
                    from_status=ClueLifecycleState.ACTIVE,
                    to_status=ClueLifecycleState.REINFORCED,
                    actor_source="machine",
                    reason="r1",
                    evidence=[r1],
                    event_key="e2",
                ),
                LifecycleEventInput(
                    from_status=ClueLifecycleState.REINFORCED,
                    to_status=ClueLifecycleState.REINFORCED,
                    actor_source="machine",
                    reason="dup",
                    evidence=[r1],
                    event_key="e3",
                ),
            ]
        )


def test_validate_lifecycle_event_wrapper():
    event = LifecycleEventInput(
        from_status=ClueLifecycleState.CANDIDATE,
        to_status=ClueLifecycleState.DISMISSED,
        actor_source="human",
        reason="reject",
        evidence=[],
        event_key="rej-1",
    )
    assert validate_lifecycle_event(event).to_status == ClueLifecycleState.DISMISSED
