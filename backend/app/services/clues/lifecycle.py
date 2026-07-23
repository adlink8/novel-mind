"""Append-only lifecycle event persistence for machine and human transitions.

Current state is always derived via replay_lifecycle — never a mutable column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clue import ClueEvidenceRef, ClueLifecycleEvent, MachineClue
from app.schemas.clue import (
    ClueActorSource,
    ClueEvidenceRef as ClueEvidenceRefSchema,
    ClueEvidenceRole,
    ClueLifecycleState,
    LifecycleEventInput,
    LifecycleTransitionError,
    replay_lifecycle,
    validate_lifecycle_event,
)


class LifecyclePersistError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppendLifecycleResult:
    event_id: int
    derived_state: ClueLifecycleState
    event_key: str


def _schema_evidence(
    rows: Iterable[dict[str, Any] | ClueEvidenceRefSchema],
) -> list[ClueEvidenceRefSchema]:
    out: list[ClueEvidenceRefSchema] = []
    for item in rows:
        if isinstance(item, ClueEvidenceRefSchema):
            out.append(item)
        else:
            out.append(ClueEvidenceRefSchema.model_validate(item))
    return out


async def load_lifecycle_events(
    session: AsyncSession,
    *,
    version_id: int,
    logical_clue_id: str,
) -> list[ClueLifecycleEvent]:
    return list(
        (
            await session.scalars(
                select(ClueLifecycleEvent)
                .where(
                    ClueLifecycleEvent.version_id == version_id,
                    ClueLifecycleEvent.logical_clue_id == logical_clue_id,
                )
                .order_by(ClueLifecycleEvent.id)
            )
        ).all()
    )


def events_to_inputs(rows: list[ClueLifecycleEvent]) -> list[LifecycleEventInput]:
    """Map ORM lifecycle rows to pure replay inputs (evidence reconstructed)."""

    inputs: list[LifecycleEventInput] = []
    for row in rows:
        evidence: list[ClueEvidenceRefSchema] = []
        for ident in row.evidence_identities or []:
            # identity format: evidence_id:chapter_id:start:end:content_hash
            parts = str(ident).split(":")
            if len(parts) >= 5:
                evidence_id = parts[0]
                try:
                    chapter_id = int(parts[1])
                    source_start = int(parts[2])
                    source_end = int(parts[3])
                    content_hash = parts[4]
                except ValueError:
                    continue
                role = ClueEvidenceRole.CUE
                if row.to_status == "reinforced":
                    role = ClueEvidenceRole.REINFORCEMENT
                elif row.to_status == "paid_off":
                    # payoff events carry both cue + payoff identities; role
                    # inferred by position later when full refs available.
                    role = ClueEvidenceRole.PAYOFF
                elif row.to_status == "dismissed":
                    role = ClueEvidenceRole.DISPOSITION
                evidence.append(
                    ClueEvidenceRefSchema(
                        evidence_id=evidence_id,
                        role=role,
                        chapter_id=chapter_id,
                        narrative_chapter_number=chapter_id,
                        source_start=source_start,
                        source_end=source_end,
                        content_hash=content_hash
                        if len(content_hash) == 64
                        else "0" * 64,
                    )
                )
        # For paid_off, rewrite first identity as cue if two+ present.
        if row.to_status == "paid_off" and len(evidence) >= 2:
            cue = evidence[0].model_copy(update={"role": ClueEvidenceRole.CUE})
            payoffs = [
                e.model_copy(update={"role": ClueEvidenceRole.PAYOFF})
                for e in evidence[1:]
            ]
            evidence = [cue, *payoffs]
        inputs.append(
            LifecycleEventInput(
                from_status=ClueLifecycleState(row.from_status),
                to_status=ClueLifecycleState(row.to_status),
                actor_source=ClueActorSource(row.actor_source),
                reason=row.reason,
                evidence=evidence,
                event_key=row.event_key,
            )
        )
    return inputs


async def derived_state_for_clue(
    session: AsyncSession,
    *,
    version_id: int,
    logical_clue_id: str,
) -> ClueLifecycleState:
    rows = await load_lifecycle_events(
        session, version_id=version_id, logical_clue_id=logical_clue_id
    )
    if not rows:
        return ClueLifecycleState.CANDIDATE
    return replay_lifecycle(events_to_inputs(rows))


async def consumed_reinforcement_identities(
    session: AsyncSession,
    *,
    version_id: int,
    logical_clue_id: str,
) -> set[str]:
    rows = await load_lifecycle_events(
        session, version_id=version_id, logical_clue_id=logical_clue_id
    )
    consumed: set[str] = set()
    for row in rows:
        if row.to_status == "reinforced":
            for ident in row.evidence_identities or []:
                consumed.add(str(ident))
    return consumed


async def append_lifecycle_event(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    logical_clue_id: str,
    to_status: ClueLifecycleState | str,
    actor_source: ClueActorSource | str,
    reason: str,
    evidence: list[dict[str, Any] | ClueEvidenceRefSchema],
    event_key: str,
    machine_clue_id: int | None = None,
    gate_audit: dict[str, Any] | None = None,
    protect_human_dismissal: bool = True,
) -> AppendLifecycleResult:
    """Validate + INSERT one lifecycle event. Never updates prior events."""

    dst = ClueLifecycleState(to_status)
    actor = ClueActorSource(actor_source)
    schema_evidence = _schema_evidence(evidence)

    rows = await load_lifecycle_events(
        session, version_id=version_id, logical_clue_id=logical_clue_id
    )
    inputs = events_to_inputs(rows)
    current = replay_lifecycle(inputs) if inputs else ClueLifecycleState.CANDIDATE

    if protect_human_dismissal and rows:
        last_human_dismiss = any(
            r.to_status == "dismissed" and r.actor_source == "human" for r in rows
        )
        if last_human_dismiss and actor == ClueActorSource.MACHINE:
            raise LifecyclePersistError(
                "human dismissal is protected from machine transitions"
            )
        last_human_confirm = any(
            r.to_status == "active" and r.actor_source == "human" for r in rows
        )
        if (
            last_human_confirm
            and actor == ClueActorSource.MACHINE
            and dst == ClueLifecycleState.DISMISSED
        ):
            raise LifecyclePersistError(
                "human confirm is protected from machine dismissal"
            )

    consumed: set[str] = set()
    for item in inputs:
        for ev in item.evidence:
            if ev.role == ClueEvidenceRole.REINFORCEMENT:
                consumed.add(ev.identity_key())

    event_input = LifecycleEventInput(
        from_status=current,
        to_status=dst,
        actor_source=actor,
        reason=reason,
        evidence=schema_evidence,
        event_key=event_key,
    )
    try:
        validate_lifecycle_event(event_input, consumed_evidence_ids=consumed)
    except LifecycleTransitionError as exc:
        raise LifecyclePersistError(str(exc)) from exc

    # Idempotent: same event_key already present → return existing.
    existing = await session.scalar(
        select(ClueLifecycleEvent).where(
            ClueLifecycleEvent.version_id == version_id,
            ClueLifecycleEvent.logical_clue_id == logical_clue_id,
            ClueLifecycleEvent.event_key == event_key,
        )
    )
    if existing is not None:
        derived = await derived_state_for_clue(
            session, version_id=version_id, logical_clue_id=logical_clue_id
        )
        return AppendLifecycleResult(
            event_id=existing.id, derived_state=derived, event_key=event_key
        )

    cue_chapter = cue_start = payoff_chapter = payoff_start = None
    if dst == ClueLifecycleState.PAID_OFF:
        cues = [e for e in schema_evidence if e.role == ClueEvidenceRole.CUE]
        payoffs = [e for e in schema_evidence if e.role == ClueEvidenceRole.PAYOFF]
        if cues:
            earliest = min(cues, key=lambda e: e.narrative_key())
            cue_chapter = earliest.narrative_chapter_number
            cue_start = earliest.source_start
        if payoffs:
            latest = max(payoffs, key=lambda e: e.narrative_key())
            payoff_chapter = latest.narrative_chapter_number
            payoff_start = latest.source_start

    if machine_clue_id is None:
        machine = await session.scalar(
            select(MachineClue).where(
                MachineClue.version_id == version_id,
                MachineClue.logical_clue_id == logical_clue_id,
            )
        )
        machine_clue_id = machine.id if machine is not None else None

    identities = [e.identity_key() for e in schema_evidence]
    row = ClueLifecycleEvent(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        machine_clue_id=machine_clue_id,
        from_status=current.value,
        to_status=dst.value,
        actor_source=actor.value,
        reason=reason[:500],
        event_key=event_key[:160],
        evidence_identities=identities,
        cue_chapter=cue_chapter,
        cue_source_start=cue_start,
        payoff_chapter=payoff_chapter,
        payoff_source_start=payoff_start,
        gate_audit=dict(gate_audit or {}),
    )
    session.add(row)
    await session.flush()

    for index, ev in enumerate(schema_evidence):
        # Attach only to the lifecycle event. Machine clue evidence rows are
        # persisted separately; sharing machine_clue_id + identity + role would
        # violate uq_clue_evidence_machine_identity and abort the transaction.
        session.add(
            ClueEvidenceRef(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_id,
                logical_clue_id=logical_clue_id,
                machine_clue_id=None,
                lifecycle_event_id=row.id,
                role=ev.role.value,
                evidence_id=ev.evidence_id,
                evidence_identity=ev.identity_key(),
                chapter_id=ev.chapter_id,
                narrative_chapter_number=ev.narrative_chapter_number,
                source_start=ev.source_start,
                source_end=ev.source_end,
                content_hash=ev.content_hash,
                excerpt=ev.excerpt,
                sort_order=index,
            )
        )
    await session.flush()

    derived = replay_lifecycle([*inputs, event_input])
    return AppendLifecycleResult(
        event_id=row.id, derived_state=derived, event_key=event_key
    )
