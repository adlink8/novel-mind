import pytest

from app.services.timeline.overrides import (
    MachineEventView,
    OverrideStore,
    apply_overrides,
    derive_visible_edge,
    relink_overrides,
)

pytestmark = pytest.mark.unit


def _event(logical_id: str, evidence: tuple[str, ...], **fields) -> MachineEventView:
    base = {"title": "机器标题", "participants": ["阿宁"], "visible": True}
    base.update(fields)
    return MachineEventView(logical_id, evidence, base)


def test_field_overrides_are_append_only_and_overlay_machine_rows():
    store = OverrideStore()
    first = store.append(owner_id=1, novel_id=2, logical_event_id="old",
                         field_name="title", value="人工标题")
    second = store.append(owner_id=1, novel_id=2, logical_event_id="old",
                          field_name="title", value="最终标题")
    event = _event("old", ("ev-1",))

    assert first.status == "superseded"
    assert second.supersedes_id == first.id
    assert event.fields["title"] == "机器标题"
    assert apply_overrides(event, store.active_for(1, 2))["title"] == "最终标题"
    assert len(store.audit) == 2


def test_reanalysis_relinks_unique_stable_evidence_identity():
    store = OverrideStore()
    override = store.append(owner_id=1, novel_id=2, logical_event_id="old",
                            field_name="participants", value=["阿宁", "李舟"])
    result = relink_overrides(
        store.active_for(1, 2),
        old_events=[_event("old", ("ev-1", "ev-2"))],
        new_events=[_event("new", ("ev-2", "ev-1"))],
    )
    assert result.relinked == 1
    assert override.logical_event_id == "new"
    assert override.needs_relink is False
    assert apply_overrides(_event("new", ("ev-1", "ev-2")), [override])["participants"] == ["阿宁", "李舟"]


@pytest.mark.parametrize("new_events", [[], [
    _event("new-a", ("ev-1",)), _event("new-b", ("ev-1",))
]])
def test_missing_or_ambiguous_target_is_retained_as_needs_relink(new_events):
    store = OverrideStore()
    override = store.append(owner_id=1, novel_id=2, logical_event_id="old",
                            field_name="visible", value=False)
    result = relink_overrides(store.active_for(1, 2),
                              old_events=[_event("old", ("ev-1",))], new_events=new_events)
    assert result.needs_relink == 1
    assert override.logical_event_id == "old"
    assert override.needs_relink is True
    assert override.status == "active"


def test_edges_are_derived_from_overlay_visible_fields():
    store = OverrideStore()
    store.append(owner_id=1, novel_id=2, logical_event_id="target",
                 field_name="visible", value=False)
    source = _event("source", ("s",))
    target = _event("target", ("t",))
    assert derive_visible_edge(source, target, store.active_for(1, 2)) is None
