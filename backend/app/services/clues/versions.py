"""Validated CAS promotion/rollback for clue active pointers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueLink,
    ClueOverride,
    CluePointerJournal,
    MachineClue,
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
    """Build an immutable machine-graph manifest for a clue analysis version."""

    clues = list(
        (
            await session.scalars(
                select(MachineClue)
                .where(MachineClue.version_id == version_id)
                .order_by(MachineClue.logical_clue_id)
            )
        ).all()
    )
    clue_ids = [row.id for row in clues]
    evidence = list(
        (
            await session.scalars(
                select(ClueEvidenceRef)
                .where(ClueEvidenceRef.version_id == version_id)
                .order_by(
                    ClueEvidenceRef.logical_clue_id,
                    ClueEvidenceRef.role,
                    ClueEvidenceRef.evidence_identity,
                )
            )
        ).all()
    )
    events = list(
        (
            await session.scalars(
                select(ClueLifecycleEvent)
                .where(ClueLifecycleEvent.version_id == version_id)
                .order_by(ClueLifecycleEvent.logical_clue_id, ClueLifecycleEvent.id)
            )
        ).all()
    )
    links = list(
        (
            await session.scalars(
                select(ClueLink)
                .where(ClueLink.version_id == version_id)
                .order_by(ClueLink.logical_clue_id, ClueLink.link_identity)
            )
        ).all()
    )

    clue_rows = [
        {
            "logical_clue_id": row.logical_clue_id,
            "title": row.title,
            "summary": row.summary,
            "package_hash": row.package_hash,
            "confidence": float(row.confidence),
            "publication_status": row.publication_status,
            "first_cue_chapter": row.first_cue_chapter,
            "first_cue_source_start": row.first_cue_source_start,
        }
        for row in clues
    ]
    evidence_rows = [
        {
            "logical_clue_id": row.logical_clue_id,
            "role": row.role,
            "evidence_id": row.evidence_id,
            "evidence_identity": row.evidence_identity,
            "chapter_id": row.chapter_id,
            "narrative_chapter_number": row.narrative_chapter_number,
            "source_start": row.source_start,
            "source_end": row.source_end,
            "content_hash": row.content_hash,
        }
        for row in evidence
    ]
    event_rows = [
        {
            "logical_clue_id": row.logical_clue_id,
            "from_status": row.from_status,
            "to_status": row.to_status,
            "actor_source": row.actor_source,
            "event_key": row.event_key,
            "evidence_identities": list(row.evidence_identities or []),
            "reason": row.reason,
        }
        for row in events
    ]
    link_rows = [
        {
            "logical_clue_id": row.logical_clue_id,
            "target_kind": row.target_kind,
            "link_identity": row.link_identity,
            "character_id": row.character_id,
            "timeline_event_id": row.timeline_event_id,
            "relationship_observation_ref": row.relationship_observation_ref,
            "validation_status": row.validation_status,
            "supporting_evidence_ids": list(row.supporting_evidence_ids or []),
        }
        for row in links
    ]
    components = {
        "clues": _checksum(clue_rows),
        "evidence": _checksum(evidence_rows),
        "lifecycle": _checksum(event_rows),
        "links": _checksum(link_rows),
    }
    manifest = {
        "schema": "clue-manifest.v1",
        "components": components,
        "clues": clue_rows,
        "evidence": evidence_rows,
        "lifecycle": event_rows,
        "links": link_rows,
        "machine_clue_count": len(clue_ids),
    }
    return manifest, _checksum(manifest)


async def _validated_version(
    session: AsyncSession, *, owner_id: int, novel_id: int, version_id: int
) -> ClueAnalysisVersion:
    version = await session.get(ClueAnalysisVersion, version_id)
    if version is None or version.owner_id != owner_id or version.novel_id != novel_id:
        raise ManifestValidationError(
            "candidate is outside the requested owner/novel scope"
        )
    if version.status not in {"candidate", "validated", "superseded"}:
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
    action: Literal["promote", "rollback"],
) -> ClueActivePointer:
    try:
        target = await _validated_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=target_version_id,
        )
        pointer = (
            await session.scalars(
                select(ClueActivePointer)
                .where(
                    ClueActivePointer.owner_id == owner_id,
                    ClueActivePointer.novel_id == novel_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        actual_revision = pointer.revision if pointer else 0
        if actual_revision != expected_revision:
            raise StalePointerError(
                f"stale active pointer revision: expected {expected_revision}, "
                f"current {actual_revision}"
            )
        previous_version_id = pointer.version_id if pointer else None
        if pointer is None:
            pointer = ClueActivePointer(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=target.id,
                revision=1,
                manifest_checksum=target.manifest_checksum or "",
            )
            session.add(pointer)
        else:
            pointer.version_id = target.id
            pointer.revision += 1
            pointer.manifest_checksum = target.manifest_checksum or ""
        session.add(
            CluePointerJournal(
                owner_id=owner_id,
                novel_id=novel_id,
                from_version_id=previous_version_id,
                to_version_id=target.id,
                action=action,
                expected_revision=expected_revision,
                resulting_revision=expected_revision + 1,
                manifest=target.manifest or {},
            )
        )
        await _relink_persisted_overrides(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            old_version_id=previous_version_id,
            new_version_id=target.id,
        )
        target.status = "validated"
        if previous_version_id and previous_version_id != target.id:
            previous = await session.get(ClueAnalysisVersion, previous_version_id)
            if previous is not None:
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
) -> ClueActivePointer:
    return await _move_pointer(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        target_version_id=candidate_version_id,
        expected_revision=expected_revision,
        action="promote",
    )


async def rollback_version(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    target_version_id: int,
    expected_revision: int,
) -> ClueActivePointer:
    return await _move_pointer(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        target_version_id=target_version_id,
        expected_revision=expected_revision,
        action="rollback",
    )


def evidence_signature_for_clue(evidence_rows: list[dict[str, Any]]) -> tuple[str, ...]:
    """Stable identity for override relink: sorted evidence identities."""

    return tuple(
        sorted(
            str(row.get("evidence_identity") or row.get("evidence_id") or "")
            for row in evidence_rows
            if row.get("evidence_identity") or row.get("evidence_id")
        )
    )


async def _relink_persisted_overrides(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    old_version_id: int | None,
    new_version_id: int,
) -> None:
    """Exactly-one evidence-identity match relinks; otherwise needs_relink.

    Physical append-only: supersede prior row by inserting a new override when
    the logical clue id changes; when only needs_relink flips and id stays the
    same, we still append a superseding row for PostgreSQL trigger safety.
    """

    if old_version_id is None:
        return
    old_manifest, _ = await snapshot_manifest(session, old_version_id)
    new_manifest, _ = await snapshot_manifest(session, new_version_id)

    def identities(manifest: dict) -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for row in manifest.get("evidence") or []:
            logical = str(row.get("logical_clue_id") or "")
            ident = str(row.get("evidence_identity") or "")
            if logical and ident:
                grouped.setdefault(logical, []).append(ident)
        return {k: tuple(sorted(v)) for k, v in grouped.items()}

    old_ids = identities(old_manifest)
    reverse: dict[tuple[str, ...], list[str]] = {}
    for logical_id, identity in identities(new_manifest).items():
        reverse.setdefault(identity, []).append(logical_id)

    overrides = list(
        (
            await session.scalars(
                select(ClueOverride).where(
                    ClueOverride.owner_id == owner_id,
                    ClueOverride.novel_id == novel_id,
                    ClueOverride.status == "active",
                )
            )
        ).all()
    )
    # Latest-wins heads: highest id per (logical_clue_id, field_name).
    heads: dict[tuple[str, str], ClueOverride] = {}
    for override in sorted(overrides, key=lambda r: r.id):
        heads[(override.logical_clue_id, override.field_name)] = override

    for override in heads.values():
        matches = reverse.get(old_ids.get(override.logical_clue_id, ()), [])
        if len(matches) == 1 and matches[0] == override.logical_clue_id:
            # Same logical id — no append needed.
            continue
        # Append-only: never UPDATE prior rows (PG triggers reject mutation).
        session.add(
            ClueOverride(
                owner_id=override.owner_id,
                novel_id=override.novel_id,
                version_id=new_version_id,
                logical_clue_id=(
                    matches[0] if len(matches) == 1 else override.logical_clue_id
                ),
                action=override.action,
                field_name=override.field_name,
                value=dict(override.value or {}),
                author=override.author,
                reason=f"reanalysis_relink:{override.reason}"[:500],
                status="needs_relink" if len(matches) != 1 else "active",
                supersedes_id=override.id,
                needs_relink=len(matches) != 1,
                evidence_signature=override.evidence_signature,
            )
        )


async def compare_machine_versions(
    session: AsyncSession,
    *,
    from_version_id: int,
    to_version_id: int,
) -> dict[str, Any]:
    """Machine-only version diff; override overlays reported separately."""

    from_manifest, _ = await snapshot_manifest(session, from_version_id)
    to_manifest, _ = await snapshot_manifest(session, to_version_id)
    from_ids = {row["logical_clue_id"] for row in from_manifest["clues"]}
    to_ids = {row["logical_clue_id"] for row in to_manifest["clues"]}
    from_by = {row["logical_clue_id"]: row for row in from_manifest["clues"]}
    to_by = {row["logical_clue_id"]: row for row in to_manifest["clues"]}
    changed = sorted(
        lid
        for lid in (from_ids & to_ids)
        if from_by[lid] != to_by[lid]
        or _lifecycle_signature(from_manifest, lid)
        != _lifecycle_signature(to_manifest, lid)
    )
    # group lifecycle by logical for diff
    lifecycle_differences = []
    all_logical = sorted(from_ids | to_ids)
    for lid in all_logical:
        a = [e for e in from_manifest["lifecycle"] if e["logical_clue_id"] == lid]
        b = [e for e in to_manifest["lifecycle"] if e["logical_clue_id"] == lid]
        if a != b:
            lifecycle_differences.append(
                {
                    "logical_clue_id": lid,
                    "from_events": a,
                    "to_events": b,
                }
            )
    return {
        "from_version_id": from_version_id,
        "to_version_id": to_version_id,
        "added_logical_clue_ids": sorted(to_ids - from_ids),
        "removed_logical_clue_ids": sorted(from_ids - to_ids),
        "changed_logical_clue_ids": changed,
        "lifecycle_differences": lifecycle_differences,
        "override_applications": [],  # filled by query/overrides layer
    }


def _lifecycle_signature(manifest: dict, logical_clue_id: str) -> list:
    return [
        e
        for e in manifest.get("lifecycle") or []
        if e.get("logical_clue_id") == logical_clue_id
    ]
