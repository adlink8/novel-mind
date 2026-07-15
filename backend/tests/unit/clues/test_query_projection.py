"""Unit tests for visible-set-first lifecycle projection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.clue import ClueLifecycleState
from app.services.clues.query import derive_visible_state, _event_visible

pytestmark = pytest.mark.unit

HEX = "a" * 64


def _event(
    *,
    eid: int,
    from_status: str,
    to_status: str,
    identities: list[str] | None = None,
    cue_chapter: int | None = None,
    payoff_chapter: int | None = None,
    actor: str = "machine",
):
    return SimpleNamespace(
        id=eid,
        from_status=from_status,
        to_status=to_status,
        actor_source=actor,
        reason=f"{from_status}->{to_status}",
        event_key=f"k{eid}",
        evidence_identities=identities or [],
        cue_chapter=cue_chapter,
        payoff_chapter=payoff_chapter,
    )


def test_before_payoff_cutoff_projects_reinforced_not_paid_off():
    """Machine paid_off is hidden until payoff chapter is visible."""

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
            identities=[f"reinf:2:0:10:{HEX}"],
        ),
        _event(
            eid=3,
            from_status="reinforced",
            to_status="paid_off",
            identities=[
                f"cue:1:0:10:{HEX}",
                f"pay:5:0:10:{HEX}",
            ],
            cue_chapter=1,
            payoff_chapter=5,
        ),
    ]
    # Cutoff chapter 2: payoff chapter 5 is hidden → reinforced.
    # Note: identity uses chapter_id; tests align chapter_id with narrative number.
    state = derive_visible_state(events, cutoff=2)
    assert state == ClueLifecycleState.REINFORCED

    full = derive_visible_state(events, cutoff=None)
    assert full == ClueLifecycleState.PAID_OFF


def test_event_visible_uses_payoff_chapter_column():
    paid = _event(
        eid=1,
        from_status="reinforced",
        to_status="paid_off",
        identities=[],
        cue_chapter=1,
        payoff_chapter=9,
    )
    assert _event_visible(paid, cutoff=3) is False
    assert _event_visible(paid, cutoff=9) is True
    assert _event_visible(paid, cutoff=None) is True


def test_no_events_defaults_to_candidate():
    assert derive_visible_state([], cutoff=1) == ClueLifecycleState.CANDIDATE
