"""Joint active-pointer rollback/restore and final watermark advancement."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import NarrativeActivePointer, NarrativeIndexBuild, NarrativePromotionJournal, NarrativeSourceSnapshot, NarrativeSourceWatermark
from app.services.knowledge_units.reconcile import ReconcileReport


class RollbackError(ValueError):
    pass


async def rollback_journal(db: AsyncSession, *, journal_id: int) -> NarrativeActivePointer | None:
    journal = await db.get(NarrativePromotionJournal, journal_id)
    if journal is None or journal.status != "committed":
        raise RollbackError("journal is not committed")
    pointer = await db.scalar(select(NarrativeActivePointer).where(NarrativeActivePointer.owner_id == journal.owner_id, NarrativeActivePointer.novel_id == journal.novel_id, NarrativeActivePointer.domain_profile == journal.domain_profile))
    if pointer is None or pointer.build_id != journal.candidate_build_id:
        raise RollbackError("active pointer no longer matches journal")
    candidate = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
    if candidate:
        candidate.status = "rolled_back"
    if journal.previous_build_id is None:
        await db.delete(pointer)
        result = None
    else:
        previous = await db.get(NarrativeIndexBuild, journal.previous_build_id)
        if previous is None:
            raise RollbackError("previous build is missing")
        previous.status = "active"
        pointer.build_id = previous.id
        pointer.pointer_version += 1
        pointer.active_manifest_checksum = journal.previous_checksum
        result = pointer
    journal.status = "rolled_back"
    await db.flush()
    return result


async def restore_journal(db: AsyncSession, *, journal_id: int) -> NarrativeActivePointer:
    journal = await db.get(NarrativePromotionJournal, journal_id)
    if journal is None or journal.status != "rolled_back":
        raise RollbackError("journal is not rolled back")
    pointer = await db.scalar(select(NarrativeActivePointer).where(NarrativeActivePointer.owner_id == journal.owner_id, NarrativeActivePointer.novel_id == journal.novel_id, NarrativeActivePointer.domain_profile == journal.domain_profile))
    candidate = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
    if candidate is None:
        raise RollbackError("candidate build is missing")
    if pointer is None:
        from datetime import UTC, datetime
        pointer = NarrativeActivePointer(owner_id=journal.owner_id, novel_id=journal.novel_id, domain_profile=journal.domain_profile, build_id=candidate.id, pointer_version=2, active_manifest_checksum=journal.candidate_checksum, activated_at=datetime.now(UTC))
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
    await db.flush()
    return pointer


async def advance_watermark(db: AsyncSession, *, build_id: int, snapshot_id: int, reconcile: ReconcileReport) -> NarrativeSourceWatermark:
    if not reconcile.passed or reconcile.build_id != build_id:
        raise RollbackError("watermark requires a clean reconcile")
    build = await db.get(NarrativeIndexBuild, build_id)
    snapshot = await db.get(NarrativeSourceSnapshot, snapshot_id)
    if build is None or snapshot is None or build.status != "active" or build.source_snapshot_id != snapshot_id:
        raise RollbackError("active build/snapshot mismatch")
    pointer = await db.scalar(select(NarrativeActivePointer).where(NarrativeActivePointer.owner_id == build.owner_id, NarrativeActivePointer.novel_id == build.novel_id, NarrativeActivePointer.domain_profile == build.domain_profile))
    if pointer is None or pointer.build_id != build_id or pointer.active_manifest_checksum != build.manifest_checksum:
        raise RollbackError("active pointer is not reconciled build")
    watermark = await db.scalar(select(NarrativeSourceWatermark).where(NarrativeSourceWatermark.owner_id == build.owner_id, NarrativeSourceWatermark.novel_id == build.novel_id, NarrativeSourceWatermark.domain_profile == build.domain_profile))
    if watermark is None:
        watermark = NarrativeSourceWatermark(owner_id=build.owner_id, novel_id=build.novel_id, domain_profile=build.domain_profile, snapshot_id=snapshot.id, build_id=build.id, source_watermark=snapshot.source_watermark, manifest_checksum=build.manifest_checksum)
        db.add(watermark)
    else:
        watermark.snapshot_id = snapshot.id
        watermark.build_id = build.id
        watermark.source_watermark = snapshot.source_watermark
        watermark.manifest_checksum = build.manifest_checksum
    await db.flush()
    return watermark
