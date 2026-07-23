"""Unit tests for visible-set-first lifecycle projection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.clue import ClueLifecycleState
from app.services.clues.query import (
    _event_visible,
    _link_visible,
    derive_visible_state,
)

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


def _evidence(*, evidence_id: str, narrative_chapter_number: int):
    return SimpleNamespace(
        evidence_id=evidence_id,
        narrative_chapter_number=narrative_chapter_number,
    )


def _link(*, supporting_evidence_ids: list[str] | None = None):
    return SimpleNamespace(supporting_evidence_ids=supporting_evidence_ids or [])


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


def test_link_visible_hides_when_supporting_evidence_beyond_cutoff():
    """List link_count and detail links share this supporting-evidence rule."""

    evidence = [
        _evidence(evidence_id="cue-1", narrative_chapter_number=1),
        _evidence(evidence_id="pay-9", narrative_chapter_number=9),
    ]
    early = _link(supporting_evidence_ids=["cue-1"])
    late = _link(supporting_evidence_ids=["pay-9"])
    both = _link(supporting_evidence_ids=["cue-1", "pay-9"])
    empty = _link(supporting_evidence_ids=[])

    assert _link_visible(early, cutoff=3, evidence_rows=evidence) is True
    assert _link_visible(late, cutoff=3, evidence_rows=evidence) is False
    assert _link_visible(both, cutoff=3, evidence_rows=evidence) is False
    # Empty support has no chapter to hide on → stays visible.
    assert _link_visible(empty, cutoff=3, evidence_rows=evidence) is True


def test_link_visible_full_book_or_no_cutoff_keeps_all():
    evidence = [
        _evidence(evidence_id="pay-9", narrative_chapter_number=9),
    ]
    late = _link(supporting_evidence_ids=["pay-9"])
    assert _link_visible(late, cutoff=None, evidence_rows=evidence) is True
    assert _link_visible(late, cutoff=9, evidence_rows=evidence) is True


def test_list_and_detail_link_filter_agree_on_visible_set():
    """Simulate list link_count vs detail links under the same cutoff."""

    evidence = [
        _evidence(evidence_id="cue-1", narrative_chapter_number=1),
        _evidence(evidence_id="reinf-2", narrative_chapter_number=2),
        _evidence(evidence_id="pay-8", narrative_chapter_number=8),
    ]
    links = [
        _link(supporting_evidence_ids=["cue-1"]),
        _link(supporting_evidence_ids=["reinf-2"]),
        _link(supporting_evidence_ids=["pay-8"]),
        _link(supporting_evidence_ids=["cue-1", "pay-8"]),
    ]
    cutoff = 3
    # Same predicate both surfaces must use.
    visible = [
        link
        for link in links
        if _link_visible(link, cutoff=cutoff, evidence_rows=evidence)
    ]
    assert len(visible) == 2
    assert visible[0].supporting_evidence_ids == ["cue-1"]
    assert visible[1].supporting_evidence_ids == ["reinf-2"]
    # Full book: count matches unfiltered length.
    full = [
        link
        for link in links
        if _link_visible(link, cutoff=None, evidence_rows=evidence)
    ]
    assert len(full) == len(links)
