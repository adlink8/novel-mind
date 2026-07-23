"""Joint active-pointer rollback/restore and final watermark advancement."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativePromotionJournal,
    NarrativeSourceSnapshot,
    NarrativeSourceWatermark,
)
from app.services.knowledge_units.reconcile import ReconcileReport


class RollbackError(ValueError):
    pass


CollectionProbe = Callable[[dict[str, Any]], Awaitable[bool]]


def collection_checkpoint_probe(store: Any) -> CollectionProbe:
    """Build a strict direct-Chroma checkpoint validator for production paths."""

    async def probe(checkpoint: dict[str, Any]) -> bool:
        collection_name = checkpoint.get("collection")
        manifest = checkpoint.get("manifest")
        build_id = checkpoint.get("build_id")
        if not collection_name or not manifest or build_id is None:
            return False
        try:
            collection = await asyncio.to_thread(
                store.get_named_collection, collection_name
            )
            payload = await asyncio.to_thread(collection.get, include=["metadatas"])
        except Exception:
            return False
        ids = payload.get("ids") or []
        metadatas = payload.get("metadatas") or []
        return (
            bool(ids)
            and len(ids) == len(metadatas)
            and all(
                metadata
                and metadata.get("build_id") == build_id
                and metadata.get("manifest_checksum") == manifest
                for metadata in metadatas
            )
        )

    return probe


async def _require_checkpoint(
    checkpoint: dict[str, Any],
    *,
    build: NarrativeIndexBuild,
    collection_probe: CollectionProbe,
) -> None:
    expected = {
        "build_id": build.id,
        "collection": build.collection_name,
        "manifest": build.manifest_checksum,
    }
    if any(checkpoint.get(key) != value for key, value in expected.items()):
        raise RollbackError("target collection checkpoint does not match PostgreSQL")
    if not await collection_probe(checkpoint):
        raise RollbackError("target collection checkpoint is not recoverable")


async def rollback_journal(
    db: AsyncSession, *, journal_id: int, collection_probe: CollectionProbe
) -> NarrativeActivePointer | None:
    journal = await db.get(NarrativePromotionJournal, journal_id)
    if journal is None or journal.status != "committed":
        raise RollbackError("journal is not committed")
    pointer = await db.scalar(
        select(NarrativeActivePointer).where(
            NarrativeActivePointer.owner_id == journal.owner_id,
            NarrativeActivePointer.novel_id == journal.novel_id,
            NarrativeActivePointer.domain_profile == journal.domain_profile,
        )
    )
    if pointer is None or pointer.build_id != journal.candidate_build_id:
        raise RollbackError("active pointer no longer matches journal")
    candidate = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
    if candidate is None:
        raise RollbackError("candidate build is missing")
    before = journal.details.get("before", {})
    after = journal.details.get("after", {})
    if journal.previous_build_id:
        target = await db.get(NarrativeIndexBuild, journal.previous_build_id)
        if target is None:
            raise RollbackError("previous build is missing")
        checkpoint = before
    else:
        target = candidate
        checkpoint = after
    await _require_checkpoint(
        checkpoint, build=target, collection_probe=collection_probe
    )
    candidate.status = "rolled_back"
    if journal.previous_build_id is None:
        await db.delete(pointer)
        result = None
    else:
        target.status = "active"
        pointer.build_id = target.id
        pointer.pointer_version += 1
        pointer.active_manifest_checksum = journal.previous_checksum
        result = pointer
    journal.status = "rolled_back"
    await _restore_watermark(db, journal, "before")
    await db.flush()
    return result


async def restore_journal(
    db: AsyncSession, *, journal_id: int, collection_probe: CollectionProbe
) -> NarrativeActivePointer:
    journal = await db.get(NarrativePromotionJournal, journal_id)
    if journal is None or journal.status != "rolled_back":
        raise RollbackError("journal is not rolled back")
    pointer = await db.scalar(
        select(NarrativeActivePointer).where(
            NarrativeActivePointer.owner_id == journal.owner_id,
            NarrativeActivePointer.novel_id == journal.novel_id,
            NarrativeActivePointer.domain_profile == journal.domain_profile,
        )
    )
    candidate = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
    if candidate is None:
        raise RollbackError("candidate build is missing")
    after = journal.details.get("after", {})
    await _require_checkpoint(after, build=candidate, collection_probe=collection_probe)
    if pointer is None:
        from datetime import UTC, datetime

        pointer = NarrativeActivePointer(
            owner_id=journal.owner_id,
            novel_id=journal.novel_id,
            domain_profile=journal.domain_profile,
            build_id=candidate.id,
            pointer_version=2,
            active_manifest_checksum=journal.candidate_checksum,
            activated_at=datetime.now(UTC),
        )
        db.add(pointer)
    else:
        if journal.previous_build_id and pointer.build_id != journal.previous_build_id:
            raise RollbackError("rollback target changed before restore")
        previous = await db.get(NarrativeIndexBuild, pointer.build_id)
        if previous:
            previous.status = "deprecated"
        pointer.build_id = candidate.id
        pointer.pointer_version += 1
        pointer.active_manifest_checksum = journal.candidate_checksum
    candidate.status = "active"
    journal.status = "committed"
    await _restore_watermark(db, journal, "after")
    await db.flush()
    return pointer


async def _restore_watermark(
    db: AsyncSession, journal: NarrativePromotionJournal, side: str
) -> None:
    checkpoint = journal.details.get(side, {}).get("watermark")
    row = await db.scalar(
        select(NarrativeSourceWatermark).where(
            NarrativeSourceWatermark.owner_id == journal.owner_id,
            NarrativeSourceWatermark.novel_id == journal.novel_id,
            NarrativeSourceWatermark.domain_profile == journal.domain_profile,
        )
    )
    if checkpoint is None:
        if row is not None:
            await db.delete(row)
        return
    snapshot = await db.get(NarrativeSourceSnapshot, checkpoint["snapshot_id"])
    if snapshot is None:
        raise RollbackError("watermark snapshot checkpoint is missing")
    if row is None:
        row = NarrativeSourceWatermark(
            owner_id=journal.owner_id,
            novel_id=journal.novel_id,
            domain_profile=journal.domain_profile,
            snapshot_id=snapshot.id,
            build_id=checkpoint["build_id"],
            source_watermark=snapshot.source_watermark,
            manifest_checksum=checkpoint["manifest_checksum"],
        )
        db.add(row)
    else:
        row.snapshot_id, row.build_id = snapshot.id, checkpoint["build_id"]
        row.source_watermark, row.manifest_checksum = (
            snapshot.source_watermark,
            checkpoint["manifest_checksum"],
        )


async def advance_watermark(
    db: AsyncSession, *, build_id: int, snapshot_id: int, reconcile: ReconcileReport
) -> NarrativeSourceWatermark:
    if not reconcile.passed or reconcile.build_id != build_id:
        raise RollbackError("watermark requires a clean reconcile")
    build = await db.get(NarrativeIndexBuild, build_id)
    snapshot = await db.get(NarrativeSourceSnapshot, snapshot_id)
    if (
        build is None
        or snapshot is None
        or build.status != "active"
        or build.source_snapshot_id != snapshot_id
    ):
        raise RollbackError("active build/snapshot mismatch")
    pointer = await db.scalar(
        select(NarrativeActivePointer).where(
            NarrativeActivePointer.owner_id == build.owner_id,
            NarrativeActivePointer.novel_id == build.novel_id,
            NarrativeActivePointer.domain_profile == build.domain_profile,
        )
    )
    if (
        pointer is None
        or pointer.build_id != build_id
        or pointer.active_manifest_checksum != build.manifest_checksum
    ):
        raise RollbackError("active pointer is not reconciled build")
    watermark = await db.scalar(
        select(NarrativeSourceWatermark).where(
            NarrativeSourceWatermark.owner_id == build.owner_id,
            NarrativeSourceWatermark.novel_id == build.novel_id,
            NarrativeSourceWatermark.domain_profile == build.domain_profile,
        )
    )
    if watermark is None:
        watermark = NarrativeSourceWatermark(
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            snapshot_id=snapshot.id,
            build_id=build.id,
            source_watermark=snapshot.source_watermark,
            manifest_checksum=build.manifest_checksum,
        )
        db.add(watermark)
    else:
        watermark.snapshot_id = snapshot.id
        watermark.build_id = build.id
        watermark.source_watermark = snapshot.source_watermark
        watermark.manifest_checksum = build.manifest_checksum
    await db.flush()
    return watermark
