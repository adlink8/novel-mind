"""Journaled prepare/commit promotion for an exact narrative candidate."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativePromotionJournal,
    NarrativeSourceWatermark,
)
from app.services.knowledge_units.eval import sign_run, verify_run
from app.services.knowledge_units.materialize import stable_hash


PROMOTION_EVIDENCE_VERSION = "promotion-evidence.v2"


class PromotionError(ValueError):
    pass


def _verify_promotion_envelope(
    envelope: dict[str, Any], *, secret: str, build: NarrativeIndexBuild
) -> None:
    if (
        envelope.get("schema_version") != PROMOTION_EVIDENCE_VERSION
        or not secret
        or not verify_run(envelope, secret)
    ):
        raise PromotionError("promotion evidence envelope is invalid")
    candidate = envelope.get("candidate", {})
    if candidate != {
        "build_id": build.id,
        "checksum": build.manifest_checksum,
        "collection": build.collection_name,
        "owner_id": build.owner_id,
        "novel_id": build.novel_id,
        "domain": build.domain_profile,
        "source_snapshot_id": build.source_snapshot_id,
        "build_key": build.build_key,
    }:
        raise PromotionError("promotion evidence candidate lineage mismatch")
    approval = envelope.get("approval", {})
    if not approval.get("identity") or not approval.get("approved_at"):
        raise PromotionError("promotion evidence approval is invalid")
    reports = envelope.get("domain_evaluations")
    if not isinstance(reports, list) or not reports:
        raise PromotionError("promotion evidence domain runs are missing")
    for report in reports:
        if not verify_run(report, secret):
            raise PromotionError("promotion evidence domain run is invalid")


def _signed_promotion_envelope(
    *,
    build: NarrativeIndexBuild,
    reports: list[dict[str, Any]],
    reconcile_report: dict[str, Any],
    approved_by: str,
    approved_at: datetime,
    before: dict[str, Any],
    after: dict[str, Any],
    secret: str,
) -> dict[str, Any]:
    envelope = {
        "schema_version": PROMOTION_EVIDENCE_VERSION,
        "candidate": {
            "build_id": build.id,
            "checksum": build.manifest_checksum,
            "collection": build.collection_name,
            "owner_id": build.owner_id,
            "novel_id": build.novel_id,
            "domain": build.domain_profile,
            "source_snapshot_id": build.source_snapshot_id,
            "build_key": build.build_key,
        },
        "domain_evaluations": sorted(
            reports, key=lambda item: (item["domain"], item["run_id"])
        ),
        "direct_chroma_reconcile": reconcile_report,
        "approval": {
            "identity": approved_by,
            "approved_at": approved_at.isoformat(),
        },
        "lineage": {"before": before, "after": after},
    }
    envelope["signature"] = sign_run(envelope, secret)
    return envelope


class NarrativePromotionService:
    async def prepare(
        self,
        db: AsyncSession,
        *,
        candidate_build_id: int,
        candidate_checksum: str,
        reconcile_report: dict,
        approved_by: str,
        eval_report: dict | None = None,
        eval_reports: list[dict] | None = None,
        evidence_secret: str = "",
    ) -> NarrativePromotionJournal:
        build = await db.get(NarrativeIndexBuild, candidate_build_id)
        if build is None or build.status != "candidate" or not build.collection_name:
            raise PromotionError("candidate build is missing or not candidate")
        if build.manifest_checksum != candidate_checksum:
            raise PromotionError("candidate checksum mismatch")
        if not approved_by.strip():
            raise PromotionError("first cutover approval is required")
        reports = eval_reports or ([eval_report] if eval_report else [])
        required_domains = (
            {build.domain_profile}
            if build.domain_profile in {"fiction", "history"}
            else {"fiction", "history"}
        )
        if (
            not evidence_secret
            or {r.get("domain") for r in reports} != required_domains
        ):
            raise PromotionError("signed frozen evaluation domain evidence is required")
        for report in reports:
            if (
                not verify_run(report, evidence_secret)
                or not report.get("passed")
                or not report.get("canary", {}).get("passed")
            ):
                raise PromotionError("frozen evaluation evidence is invalid")
            expected = (
                build.id,
                build.manifest_checksum,
                build.collection_name,
                build.owner_id,
                build.novel_id,
            )
            actual = tuple(
                report.get(k)
                for k in (
                    "build_id",
                    "candidate_checksum",
                    "collection",
                    "owner_id",
                    "novel_id",
                )
            )
            if (
                actual != expected
                or report.get("faithfulness_failures") != 0
                or not report.get("outputs")
            ):
                raise PromotionError(
                    "evaluation evidence belongs to another candidate or is static"
                )
        residue_keys = (
            "missing",
            "orphan",
            "duplicate",
            "wrong_build",
            "wrong_owner",
            "deleted",
            "deprecated",
        )
        if (
            reconcile_report.get("build_id") != build.id
            or reconcile_report.get("collection") != build.collection_name
        ):
            raise PromotionError("candidate reconcile binding mismatch")
        if any(reconcile_report.get(key) for key in residue_keys):
            raise PromotionError("candidate reconcile has residue")
        pointer = await db.scalar(
            select(NarrativeActivePointer).where(
                NarrativeActivePointer.owner_id == build.owner_id,
                NarrativeActivePointer.novel_id == build.novel_id,
                NarrativeActivePointer.domain_profile == build.domain_profile,
            )
        )
        watermark = await db.scalar(
            select(NarrativeSourceWatermark).where(
                NarrativeSourceWatermark.owner_id == build.owner_id,
                NarrativeSourceWatermark.novel_id == build.novel_id,
                NarrativeSourceWatermark.domain_profile == build.domain_profile,
            )
        )
        previous_build = (
            await db.get(NarrativeIndexBuild, pointer.build_id) if pointer else None
        )
        before = {
            "build_id": pointer.build_id if pointer else None,
            "collection": previous_build.collection_name if previous_build else None,
            "manifest": pointer.active_manifest_checksum if pointer else None,
            "watermark": {
                "snapshot_id": watermark.snapshot_id,
                "build_id": watermark.build_id,
                "source_watermark": watermark.source_watermark,
                "manifest_checksum": watermark.manifest_checksum,
            }
            if watermark
            else None,
        }
        after = {
            "build_id": build.id,
            "collection": build.collection_name,
            "manifest": build.manifest_checksum,
            "watermark": {
                "snapshot_id": build.source_snapshot_id,
                "build_id": build.id,
                "manifest_checksum": build.manifest_checksum,
            },
        }
        transaction_key = stable_hash(
            {
                "candidate": candidate_build_id,
                "checksum": candidate_checksum,
                "eval_runs": sorted(r["run_id"] for r in reports),
                "reconcile": reconcile_report,
                "approved_by": approved_by,
            }
        )[:120]
        existing = await db.scalar(
            select(NarrativePromotionJournal).where(
                NarrativePromotionJournal.transaction_key == transaction_key
            )
        )
        if existing is not None:
            _verify_promotion_envelope(
                existing.details.get("promotion_evidence", {}),
                secret=evidence_secret,
                build=build,
            )
            return existing
        envelope = _signed_promotion_envelope(
            build=build,
            reports=reports,
            reconcile_report=reconcile_report,
            approved_by=approved_by,
            approved_at=datetime.now(UTC),
            before=before,
            after=after,
            secret=evidence_secret,
        )
        _verify_promotion_envelope(envelope, secret=evidence_secret, build=build)
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
                "promotion_evidence": envelope,
                "before": before,
                "after": after,
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
        evidence_secret: str,
    ) -> NarrativeActivePointer:
        journal = await db.get(NarrativePromotionJournal, journal_id)
        if journal is None or journal.status != "prepared":
            raise PromotionError("journal is missing or not prepared")
        if journal.candidate_checksum != candidate_checksum:
            raise PromotionError("commit checksum mismatch")
        build = await db.get(NarrativeIndexBuild, journal.candidate_build_id)
        if (
            build is None
            or build.status != "candidate"
            or build.manifest_checksum != candidate_checksum
        ):
            raise PromotionError("candidate changed after prepare")
        _verify_promotion_envelope(
            journal.details.get("promotion_evidence", {}),
            secret=evidence_secret,
            build=build,
        )
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
            if (
                pointer.build_id != journal.previous_build_id
                or pointer.active_manifest_checksum != journal.previous_checksum
            ):
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
