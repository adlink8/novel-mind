"""Journaled prepare/commit promotion for an exact narrative candidate."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativePromotionJournal,
)
from app.services.knowledge_units.materialize import stable_hash


class PromotionError(ValueError):
    pass


class NarrativePromotionService:
    async def prepare(
        self,
        db: AsyncSession,
        *,
        candidate_build_id: int,
        candidate_checksum: str,
        eval_report: dict,
        reconcile_report: dict,
        approved_by: str,
    ) -> NarrativePromotionJournal:
        build = await db.get(NarrativeIndexBuild, candidate_build_id)
        if build is None or build.status != "candidate":
            raise PromotionError("candidate build is missing or not candidate")
        if build.manifest_checksum != candidate_checksum:
            raise PromotionError("candidate checksum mismatch")
        if not approved_by.strip():
            raise PromotionError("first cutover approval is required")
        if not eval_report.get("passed") or not eval_report.get("dataset_hash"):
            raise PromotionError("frozen evaluation did not pass")
        if not eval_report.get("canary", {}).get("passed"):
            raise PromotionError("canary did not pass")
        if any(reconcile_report.get(key) for key in ("missing", "orphan", "duplicate", "wrong_owner", "deleted", "deprecated")):
            raise PromotionError("candidate reconcile has residue")
        pointer = await db.scalar(
            select(NarrativeActivePointer).where(
                NarrativeActivePointer.owner_id == build.owner_id,
                NarrativeActivePointer.novel_id == build.novel_id,
                NarrativeActivePointer.domain_profile == build.domain_profile,
            )
        )
        transaction_key = stable_hash(
            {
                "candidate": candidate_build_id,
                "checksum": candidate_checksum,
                "eval": eval_report["dataset_hash"],
                "approved_by": approved_by,
            }
        )[:120]
        existing = await db.scalar(
            select(NarrativePromotionJournal).where(
                NarrativePromotionJournal.transaction_key == transaction_key
            )
        )
        if existing is not None:
            return existing
        journal = NarrativePromotionJournal(
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            transaction_key=transaction_key,
            candidate_build_id=build.id,
            previous_build_id=pointer.build_id if pointer else None,
            status="prepared",
            candidate_checksum=candidate_checksum,
            previous_checksum=pointer.active_manifest_checksum if pointer else None,
            details={
                "approved_by": approved_by,
                "dataset_hash": eval_report["dataset_hash"],
                "eval": eval_report,
                "reconcile": reconcile_report,
            },
        )
        db.add(journal)
        await db.flush()
        return journal

    async def commit(
        self,
        db: AsyncSession,
        *,
        journal_id: int,
        candidate_checksum: str,
    ) -> NarrativeActivePointer:
        journal = await db.get(NarrativePromotionJournal, journal_id)
        if journal is None or journal.status != "prepared":
            raise PromotionError("journal is missing or not prepared")
        if journal.candidate_checksum != candidate_checksum:
            raise PromotionError("commit checksum mismatch")
        build = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
        if build is None or build.status != "candidate" or build.manifest_checksum != candidate_checksum:
            raise PromotionError("candidate changed after prepare")
        pointer = await db.scalar(
            select(NarrativeActivePointer).where(
                NarrativeActivePointer.owner_id == journal.owner_id,
                NarrativeActivePointer.novel_id == journal.novel_id,
                NarrativeActivePointer.domain_profile == journal.domain_profile,
            )
        )
        if pointer is None:
            pointer = NarrativeActivePointer(
                owner_id=journal.owner_id,
                novel_id=journal.novel_id,
                domain_profile=journal.domain_profile,
                build_id=build.id,
                pointer_version=1,
                active_manifest_checksum=candidate_checksum,
                activated_at=datetime.now(UTC),
            )
            db.add(pointer)
        else:
            if pointer.build_id != journal.previous_build_id or pointer.active_manifest_checksum != journal.previous_checksum:
                raise PromotionError("active pointer changed after prepare")
            previous = await db.get(NarrativeIndexBuild, pointer.build_id)
            if previous is not None:
                previous.status = "deprecated"
            pointer.build_id = build.id
            pointer.pointer_version += 1
            pointer.active_manifest_checksum = candidate_checksum
            pointer.activated_at = datetime.now(UTC)
        build.status = "active"
        journal.status = "committed"
        await db.flush()
        return pointer


narrative_promotion_service = NarrativePromotionService()
