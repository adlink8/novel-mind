"""Append-only human overrides and cross-version evidence-identity relink."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clue import ClueOverride, MachineClue
from app.schemas.clue import ClueActorSource, ClueLifecycleState, ClueOverrideAction
from app.services.clues.lifecycle import (
    append_lifecycle_event,
)


@dataclass(frozen=True)
class MachineClueView:
    logical_clue_id: str
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
    logical_clue_id: str
    field_name: str
    value: Any
    action: str = "annotate"
    supersedes_id: int | None = None
    status: str = "active"
    needs_relink: bool = False
    author: str = "owner"
    reason: str = ""


@dataclass(frozen=True)
class RelinkResult:
    relinked: int
    needs_relink: int


def latest_overrides(
    rows: Iterable[ClueOverride | FieldOverride],
) -> list[ClueOverride | FieldOverride]:
    """Latest-wins heads per (logical_clue_id, field_name) by highest id."""

    heads: dict[tuple[str, str], ClueOverride | FieldOverride] = {}
    for row in sorted(rows, key=lambda r: r.id):
        heads[(row.logical_clue_id, row.field_name)] = row
    return list(heads.values())


def relink_overrides(
    overrides: Iterable[FieldOverride],
    *,
    old_events: list[MachineClueView],
    new_events: list[MachineClueView],
) -> RelinkResult:
    """Exactly one stable evidence match relinks; zero/multiple → needs_relink.

    Pure adapter (mutates FieldOverride in memory only) for unit tests.
    """

    old_by_id = {event.logical_clue_id: event for event in old_events}
    new_by_identity: dict[tuple[str, ...], list[MachineClueView]] = {}
    for event in new_events:
        new_by_identity.setdefault(event.stable_identity, []).append(event)
    relinked = pending = 0
    for override in overrides:
        old = old_by_id.get(override.logical_clue_id)
        matches = new_by_identity.get(old.stable_identity, []) if old else []
        if len(matches) == 1:
            override.logical_clue_id = matches[0].logical_clue_id
            override.needs_relink = False
            relinked += 1
        else:
            override.needs_relink = True
            pending += 1
    return RelinkResult(relinked, pending)


async def _latest_head(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    logical_clue_id: str,
    field_name: str,
) -> ClueOverride | None:
    rows = list(
        (
            await session.scalars(
                select(ClueOverride)
                .where(
                    ClueOverride.owner_id == owner_id,
                    ClueOverride.novel_id == novel_id,
                    ClueOverride.logical_clue_id == logical_clue_id,
                    ClueOverride.field_name == field_name,
                )
                .order_by(ClueOverride.id)
            )
        ).all()
    )
    return rows[-1] if rows else None


async def append_override(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    logical_clue_id: str,
    action: ClueOverrideAction | str,
    field_name: str,
    value: dict[str, Any],
    author: str,
    reason: str,
    version_id: int | None = None,
    evidence_signature: str | None = None,
    needs_relink: bool = False,
) -> ClueOverride:
    """INSERT superseding override; never UPDATE prior rows."""

    action_v = ClueOverrideAction(action)
    prior = await _latest_head(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        logical_clue_id=logical_clue_id,
        field_name=field_name,
    )
    row = ClueOverride(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        action=action_v.value,
        field_name=field_name,
        value=deepcopy(value),
        author=author,
        reason=reason[:500],
        status="needs_relink" if needs_relink else "active",
        supersedes_id=prior.id if prior is not None else None,
        needs_relink=needs_relink,
        evidence_signature=evidence_signature,
    )
    session.add(row)
    await session.flush()
    return row


async def human_confirm(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    logical_clue_id: str,
    author: str,
    reason: str,
    evidence: list[dict[str, Any]],
) -> tuple[ClueOverride, Any]:
    """Protected candidate→active plus disposition override."""

    lifecycle = await append_lifecycle_event(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        to_status=ClueLifecycleState.ACTIVE,
        actor_source=ClueActorSource.HUMAN,
        reason=reason,
        evidence=evidence,
        event_key=f"human-confirm:{logical_clue_id}:{version_id}",
        gate_audit={"source": "human_confirm"},
    )
    override = await append_override(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        action=ClueOverrideAction.CONFIRM,
        field_name="disposition",
        value={"confirmed": True, "to_status": "active"},
        author=author,
        reason=reason,
    )
    return override, lifecycle


async def human_reject(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    logical_clue_id: str,
    author: str,
    reason: str,
    evidence: list[dict[str, Any]] | None = None,
) -> tuple[ClueOverride, Any]:
    """Protected nonterminal→dismissed plus disposition override."""

    lifecycle = await append_lifecycle_event(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        to_status=ClueLifecycleState.DISMISSED,
        actor_source=ClueActorSource.HUMAN,
        reason=reason,
        evidence=list(evidence or []),
        event_key=f"human-reject:{logical_clue_id}:{version_id}",
        gate_audit={"source": "human_reject"},
    )
    override = await append_override(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        action=ClueOverrideAction.REJECT,
        field_name="disposition",
        value={"rejected": True, "to_status": "dismissed"},
        author=author,
        reason=reason,
    )
    return override, lifecycle


async def human_annotate(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    logical_clue_id: str,
    author: str,
    reason: str,
    note: str,
) -> ClueOverride:
    """Annotation leaves lifecycle state unchanged."""

    return await append_override(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        action=ClueOverrideAction.ANNOTATE,
        field_name="note",
        value={"note": note},
        author=author,
        reason=reason,
    )


async def human_adjust_link(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    logical_clue_id: str,
    author: str,
    reason: str,
    link: dict[str, Any],
) -> ClueOverride:
    """Superseding link adjustment override (no machine link table mutation)."""

    return await append_override(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        action=ClueOverrideAction.ADJUST_LINK,
        field_name="link",
        value={"link": link},
        author=author,
        reason=reason,
    )


async def list_active_overrides(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    logical_clue_ids: set[str] | None = None,
) -> list[ClueOverride]:
    q = select(ClueOverride).where(
        ClueOverride.owner_id == owner_id,
        ClueOverride.novel_id == novel_id,
    )
    if logical_clue_ids is not None:
        if not logical_clue_ids:
            return []
        q = q.where(ClueOverride.logical_clue_id.in_(logical_clue_ids))
    rows = list((await session.scalars(q.order_by(ClueOverride.id))).all())
    heads = latest_overrides(rows)
    return [r for r in heads if not getattr(r, "needs_relink", False)]


async def ensure_machine_clue_exists(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    logical_clue_id: str,
) -> MachineClue | None:
    return await session.scalar(
        select(MachineClue).where(
            MachineClue.owner_id == owner_id,
            MachineClue.novel_id == novel_id,
            MachineClue.version_id == version_id,
            MachineClue.logical_clue_id == logical_clue_id,
        )
    )


class OverrideStore:
    """In-memory append-only adapter for unit tests."""

    def __init__(self) -> None:
        self.rows: list[FieldOverride] = []

    def append(
        self,
        *,
        owner_id: int,
        novel_id: int,
        logical_clue_id: str,
        field_name: str,
        value: Any,
        action: str = "annotate",
        author: str = "owner",
        reason: str = "note",
    ) -> FieldOverride:
        current = next(
            (
                row
                for row in reversed(self.rows)
                if row.owner_id == owner_id
                and row.novel_id == novel_id
                and row.logical_clue_id == logical_clue_id
                and row.field_name == field_name
                and row.status == "active"
            ),
            None,
        )
        # Soft-supersede in memory only (mirrors latest-wins; PG never UPDATEs).
        if current is not None:
            current.status = "superseded"
        row = FieldOverride(
            len(self.rows) + 1,
            owner_id,
            novel_id,
            logical_clue_id,
            field_name,
            deepcopy(value),
            action=action,
            supersedes_id=current.id if current else None,
            author=author,
            reason=reason,
        )
        self.rows.append(row)
        return row

    def active_for(self, owner_id: int, novel_id: int) -> list[FieldOverride]:
        return [
            row
            for row in latest_overrides(self.rows)
            if row.owner_id == owner_id
            and row.novel_id == novel_id
            and row.status == "active"
            and not row.needs_relink
        ]
