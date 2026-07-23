"""Validated CAS promotion and byte-identical timeline rollback."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import AnalysisVersion
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
    TimelineEvidenceRef,
    TimelineOverride,
    TimelineParticipant,
    TimelinePointerJournal,
)


class ManifestValidationError(RuntimeError):
    pass


class StalePointerError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _checksum(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


async def snapshot_manifest(session: AsyncSession, version_id: int) -> tuple[dict, str]:
    events = list(
        (
            await session.scalars(
                select(MachineTimelineEvent)
                .where(MachineTimelineEvent.version_id == version_id)
                .order_by(MachineTimelineEvent.logical_event_id)
            )
        ).all()
    )
    event_ids = [event.id for event in events]
    participants = (
        list(
            (
                await session.scalars(
                    select(TimelineParticipant)
                    .where(TimelineParticipant.event_id.in_(event_ids))
                    .order_by(TimelineParticipant.event_id, TimelineParticipant.id)
                )
            ).all()
        )
        if event_ids
        else []
    )
    evidence = (
        list(
            (
                await session.scalars(
                    select(TimelineEvidenceRef)
                    .where(TimelineEvidenceRef.event_id.in_(event_ids))
                    .order_by(
                        TimelineEvidenceRef.event_id, TimelineEvidenceRef.evidence_id
                    )
                )
            ).all()
        )
        if event_ids
        else []
    )
    edges = list(
        (
            await session.scalars(
                select(TimelineCausalEdge)
                .where(TimelineCausalEdge.version_id == version_id)
                .order_by(
                    TimelineCausalEdge.source_event_id,
                    TimelineCausalEdge.target_event_id,
                    TimelineCausalEdge.edge_type,
                )
            )
        ).all()
    )

    event_rows = [
        {
            "logical_event_id": row.logical_event_id,
            "title": row.title,
            "description": row.description,
            "event_type": row.event_type,
            "time_precision": row.time_precision,
            "time_expression": row.time_expression,
            "exact_time": row.exact_time,
            "relative_anchor_event_id": row.relative_anchor_event_id,
            "relative_relation": row.relative_relation,
            "fuzzy_start": row.fuzzy_start,
            "fuzzy_end": row.fuzzy_end,
            "narrative_chapter_number": row.narrative_chapter_number,
            "narrative_index": row.narrative_index,
            "story_rank": row.story_rank,
            "story_constraints": row.story_constraints,
            "confidence": row.confidence,
            "publication_status": row.publication_status,
        }
        for row in events
    ]
    id_to_logical = {row.id: row.logical_event_id for row in events}
    participant_rows = [
        {
            "event": id_to_logical[row.event_id],
            "entity_id": row.entity_id,
            "mention": row.mention,
        }
        for row in participants
    ]
    evidence_rows = [
        {
            "event": id_to_logical[row.event_id],
            "chapter_id": row.chapter_id,
            "evidence_id": row.evidence_id,
            "source_start": row.source_start,
            "source_end": row.source_end,
            "content_hash": row.content_hash,
        }
        for row in evidence
    ]
    edge_rows = [
        {
            "source": id_to_logical[row.source_event_id],
            "target": id_to_logical[row.target_event_id],
            "edge_type": row.edge_type,
            "confidence": row.confidence,
            "evidence_refs": row.evidence_refs,
        }
        for row in edges
    ]
    components = {
        "events": _checksum(event_rows),
        "participants": _checksum(participant_rows),
        "evidence": _checksum(evidence_rows),
        "edges": _checksum(edge_rows),
    }
    manifest = {
        "schema": "timeline-manifest.v1",
        "components": components,
        "events": event_rows,
        "participants": participant_rows,
        "evidence": evidence_rows,
        "edges": edge_rows,
    }
    return manifest, _checksum(manifest)


async def _validated_version(
    session: AsyncSession, *, owner_id: int, novel_id: int, version_id: int
) -> AnalysisVersion:
    version = await session.get(AnalysisVersion, version_id)
    if version is None or version.owner_id != owner_id or version.novel_id != novel_id:
        raise ManifestValidationError(
            "candidate is outside the requested owner/novel scope"
        )
    if version.status not in {"candidate", "active", "superseded"}:
        raise ManifestValidationError("version status cannot be activated")
    manifest, checksum = await snapshot_manifest(session, version.id)
    if version.manifest != manifest or version.manifest_checksum != checksum:
        raise ManifestValidationError(
            "stored manifest does not match immutable graph rows"
        )
    return version


async def _move_pointer(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    target_version_id: int,
    expected_revision: int,
    action: Literal["promotion", "rollback"],
) -> TimelineActivePointer:
    try:
        target = await _validated_version(
            session, owner_id=owner_id, novel_id=novel_id, version_id=target_version_id
        )
        pointer = (
            await session.scalars(
                select(TimelineActivePointer)
                .where(
                    TimelineActivePointer.owner_id == owner_id,
                    TimelineActivePointer.novel_id == novel_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        actual_revision = pointer.revision if pointer else 0
        if actual_revision != expected_revision:
            raise StalePointerError(
                f"stale active pointer revision: expected {expected_revision}, current {actual_revision}"
            )
        previous_version_id = pointer.version_id if pointer else None
        if pointer is None:
            pointer = TimelineActivePointer(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=target.id,
                revision=1,
                manifest_checksum=target.manifest_checksum,
            )
            session.add(pointer)
        else:
            pointer.version_id = target.id
            pointer.revision += 1
            pointer.manifest_checksum = target.manifest_checksum
        session.add(
            TimelinePointerJournal(
                owner_id=owner_id,
                novel_id=novel_id,
                from_version_id=previous_version_id,
                to_version_id=target.id,
                action=action,
                expected_revision=expected_revision,
                resulting_revision=expected_revision + 1,
                manifest=target.manifest,
            )
        )
        await _relink_persisted_overrides(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            old_version_id=previous_version_id,
            new_version_id=target.id,
        )
        target.status = "active"
        if previous_version_id and previous_version_id != target.id:
            previous = await session.get(AnalysisVersion, previous_version_id)
            if previous:
                previous.status = "superseded"
        await session.commit()
        await session.refresh(pointer)
        return pointer
    except Exception:
        await session.rollback()
        raise


async def promote_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_version_id: int,
    expected_revision: int,
) -> TimelineActivePointer:
    return await _move_pointer(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        target_version_id=candidate_version_id,
        expected_revision=expected_revision,
        action="promotion",
    )


async def rollback_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    target_version_id: int,
    expected_revision: int,
) -> TimelineActivePointer:
    return await _move_pointer(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        target_version_id=target_version_id,
        expected_revision=expected_revision,
        action="rollback",
    )


async def _relink_persisted_overrides(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    old_version_id: int | None,
    new_version_id: int,
) -> None:
    if old_version_id is None:
        return
    old_manifest, _ = await snapshot_manifest(session, old_version_id)
    new_manifest, _ = await snapshot_manifest(session, new_version_id)

    def identities(manifest: dict) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for row in manifest["evidence"]:
            grouped.setdefault(row["event"], []).append(row["evidence_id"])
        return {event: tuple(sorted(refs)) for event, refs in grouped.items()}

    old_ids = identities(old_manifest)
    reverse: dict[tuple[str, ...], list[str]] = {}
    for logical_id, identity in identities(new_manifest).items():
        reverse.setdefault(identity, []).append(logical_id)
    overrides = (
        await session.scalars(
            select(TimelineOverride)
            .where(
                TimelineOverride.owner_id == owner_id,
                TimelineOverride.novel_id == novel_id,
                TimelineOverride.status == "active",
            )
            .with_for_update()
        )
    ).all()
    for override in overrides:
        matches = reverse.get(old_ids.get(override.logical_event_id, ()), [])
        if len(matches) == 1:
            override.logical_event_id = matches[0]
            override.needs_relink = False
        else:
            override.needs_relink = True
