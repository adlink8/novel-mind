"""Append-only field overlays and explicit cross-version relinking."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class MachineEventView:
    logical_event_id: str
    evidence_identity: tuple[str, ...]
    fields: dict[str, Any]

    @property
    def stable_identity(self) -> tuple[str, ...]:
        return tuple(sorted(self.evidence_identity))


@dataclass
class FieldOverride:
    id: int
    owner_id: int
    novel_id: int
    logical_event_id: str
    field_name: str
    value: Any
    supersedes_id: int | None = None
    status: str = "active"
    needs_relink: bool = False


@dataclass(frozen=True)
class OverrideAudit:
    override_id: int
    action: str
    logical_event_id: str
    field_name: str


@dataclass(frozen=True)
class RelinkResult:
    relinked: int
    needs_relink: int


class OverrideStore:
    """Deterministic adapter matching the persisted append-only override contract."""

    def __init__(self) -> None:
        self.rows: list[FieldOverride] = []
        self.audit: list[OverrideAudit] = []

    def append(self, *, owner_id: int, novel_id: int, logical_event_id: str,
               field_name: str, value: Any) -> FieldOverride:
        current = next((row for row in reversed(self.rows)
                        if row.owner_id == owner_id and row.novel_id == novel_id
                        and row.logical_event_id == logical_event_id
                        and row.field_name == field_name and row.status == "active"), None)
        if current is not None:
            current.status = "superseded"
        row = FieldOverride(len(self.rows) + 1, owner_id, novel_id, logical_event_id,
                            field_name, deepcopy(value), current.id if current else None)
        self.rows.append(row)
        self.audit.append(OverrideAudit(row.id, "append", logical_event_id, field_name))
        return row

    def active_for(self, owner_id: int, novel_id: int) -> list[FieldOverride]:
        return [row for row in self.rows
                if row.owner_id == owner_id and row.novel_id == novel_id
                and row.status == "active"]


def apply_overrides(event: MachineEventView,
                    overrides: Iterable[FieldOverride]) -> dict[str, Any]:
    visible = deepcopy(event.fields)
    for override in sorted(overrides, key=lambda row: row.id):
        if (override.status == "active" and not override.needs_relink
                and override.logical_event_id == event.logical_event_id):
            visible[override.field_name] = deepcopy(override.value)
    return visible


def relink_overrides(overrides: Iterable[FieldOverride], *,
                     old_events: list[MachineEventView],
                     new_events: list[MachineEventView]) -> RelinkResult:
    old_by_id = {event.logical_event_id: event for event in old_events}
    new_by_identity: dict[tuple[str, ...], list[MachineEventView]] = {}
    for event in new_events:
        new_by_identity.setdefault(event.stable_identity, []).append(event)
    relinked = pending = 0
    for override in overrides:
        old = old_by_id.get(override.logical_event_id)
        matches = new_by_identity.get(old.stable_identity, []) if old else []
        if len(matches) == 1:
            override.logical_event_id = matches[0].logical_event_id
            override.needs_relink = False
            relinked += 1
        else:
            override.needs_relink = True
            pending += 1
    return RelinkResult(relinked, pending)


def derive_visible_edge(source: MachineEventView, target: MachineEventView,
                        overrides: Iterable[FieldOverride]) -> tuple[str, str] | None:
    overlay = list(overrides)
    source_fields = apply_overrides(source, overlay)
    target_fields = apply_overrides(target, overlay)
    if not source_fields.get("visible", True) or not target_fields.get("visible", True):
        return None
    return source.logical_event_id, target.logical_event_id
