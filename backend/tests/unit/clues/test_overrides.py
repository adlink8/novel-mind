"""Unit tests for append-only clue overrides and evidence-identity relink."""

from __future__ import annotations

import pytest

from app.services.clues.overrides import (
    FieldOverride,
    MachineClueView,
    OverrideStore,
    latest_overrides,
    relink_overrides,
)

pytestmark = pytest.mark.unit


def test_append_supersedes_without_mutating_prior_identity():
    store = OverrideStore()
    first = store.append(
        owner_id=1,
        novel_id=2,
        logical_clue_id="clue-a",
        field_name="note",
        value={"note": "v1"},
    )
    second = store.append(
        owner_id=1,
        novel_id=2,
        logical_clue_id="clue-a",
        field_name="note",
        value={"note": "v2"},
    )
    assert first.id != second.id
    assert second.supersedes_id == first.id
    assert first.status == "superseded"
    assert second.status == "active"
    active = store.active_for(1, 2)
    assert len(active) == 1
    assert active[0].value == {"note": "v2"}


def test_relink_exactly_one_match():
    old = [
        MachineClueView("old-id", ("ev1:1:0:10:aaa",), {"title": "A"}),
    ]
    new = [
        MachineClueView("new-id", ("ev1:1:0:10:aaa",), {"title": "A"}),
    ]
    overrides = [
        FieldOverride(1, 1, 1, "old-id", "note", {"note": "keep"}),
    ]
    result = relink_overrides(overrides, old_events=old, new_events=new)
    assert result.relinked == 1
    assert result.needs_relink == 0
    assert overrides[0].logical_clue_id == "new-id"
    assert overrides[0].needs_relink is False


def test_relink_zero_and_multiple_matches_need_relink():
    old = [
        MachineClueView("a", ("sig-a",), {}),
        MachineClueView("b", ("sig-b",), {}),
    ]
    new = [
        MachineClueView("x1", ("sig-b",), {}),
        MachineClueView("x2", ("sig-b",), {}),
    ]
    overrides = [
        FieldOverride(1, 1, 1, "a", "note", {"note": "missing"}),
        FieldOverride(2, 1, 1, "b", "note", {"note": "ambiguous"}),
    ]
    result = relink_overrides(overrides, old_events=old, new_events=new)
    assert result.relinked == 0
    assert result.needs_relink == 2
    assert all(o.needs_relink for o in overrides)


def test_latest_overrides_heads_by_id():
    rows = [
        FieldOverride(1, 1, 1, "c1", "note", {"note": "a"}, status="active"),
        FieldOverride(2, 1, 1, "c1", "note", {"note": "b"}, status="active", supersedes_id=1),
        FieldOverride(3, 1, 1, "c1", "disposition", {"confirmed": True}, status="active"),
    ]
    heads = latest_overrides(rows)
    by_field = {h.field_name: h for h in heads}
    assert by_field["note"].value == {"note": "b"}
    assert by_field["disposition"].value == {"confirmed": True}
