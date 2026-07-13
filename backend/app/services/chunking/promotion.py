"""Qualified-only CAS prepare/commit for chunker candidates (07-05)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.chunking.builds import InMemoryBuildStore
from app.services.chunking.reconcile import reconcile_build
from app.services.chunking.schemas import QualifiedChunkerEvidence


class PromotionError(Exception):
    def __init__(self, message: str, *, active_unchanged: bool = True):
        super().__init__(message)
        self.message = message
        self.active_unchanged = active_unchanged


def _validate_evidence(
    evidence: QualifiedChunkerEvidence,
    store: InMemoryBuildStore,
    build_id: str,
) -> None:
    if evidence.status != "qualified":
        raise PromotionError(f"evidence status {evidence.status!r} not qualified")
    if not evidence.quality_comparable:
        raise PromotionError("quality_comparable=false")
    if evidence.build_id != build_id:
        raise PromotionError("evidence build_id mismatch")
    rec = store.builds.get(build_id)
    if rec is None:
        raise PromotionError("build not found")
    if evidence.manifest_checksum != rec.manifest_checksum:
        raise PromotionError("manifest_checksum mismatch")
    if evidence.source_snapshot_hash != rec.source_snapshot_hash:
        raise PromotionError("source_snapshot_hash mismatch")
    if evidence.chunker_name != rec.chunker_name:
        raise PromotionError("chunker_name mismatch")
    if evidence.chunker_version != rec.chunker_version:
        raise PromotionError("chunker_version mismatch")
    if evidence.chunker_config_hash != rec.chunker_config_hash:
        raise PromotionError("chunker_config_hash mismatch")
    report = reconcile_build(store, build_id, cleanup=True)
    if not report.clean:
        raise PromotionError("reconcile not clean")


def prepare_promotion(
    store: InMemoryBuildStore,
    *,
    build_id: str,
    evidence: QualifiedChunkerEvidence,
) -> dict[str, Any]:
    """Validate evidence + reconcile; mark prepared. Does not move active."""
    prev_active = store.get_active(store.builds[build_id].novel_id) if build_id in store.builds else None
    try:
        _validate_evidence(evidence, store, build_id)
    except PromotionError:
        raise
    rec = store.builds[build_id]
    journal = list(rec.journal) + [
        {
            "event": "prepared",
            "at": datetime.now(timezone.utc).isoformat(),
            "evidence_sig": evidence.report_signature,
            "prev_active": prev_active,
        }
    ]
    store.builds[build_id] = rec.model_copy(
        update={"status": "prepared", "journal": journal}
    )
    return {
        "ok": True,
        "build_id": build_id,
        "status": "prepared",
        "active": store.get_active(rec.novel_id),
    }


def commit_promotion(
    store: InMemoryBuildStore,
    *,
    build_id: str,
    evidence: QualifiedChunkerEvidence,
) -> dict[str, Any]:
    """Re-validate evidence then CAS active pointer to candidate build."""
    if build_id not in store.builds:
        raise PromotionError("build not found")
    rec = store.builds[build_id]
    prev = store.get_active(rec.novel_id)

    # Idempotent
    if prev == build_id and rec.status == "committed":
        return {
            "ok": True,
            "idempotent": True,
            "active": build_id,
            "previous": prev,
        }

    try:
        _validate_evidence(evidence, store, build_id)
        if rec.status not in ("prepared", "reconciled", "qualified", "built"):
            # allow commit after prepare; re-prepare path also ok if evidence valid
            pass
    except PromotionError as exc:
        # active unchanged
        return {
            "ok": False,
            "error": exc.message,
            "active": prev,
        }

    store.active[rec.novel_id] = build_id
    journal = list(rec.journal) + [
        {
            "event": "committed",
            "at": datetime.now(timezone.utc).isoformat(),
            "prev_active": prev,
            "evidence_sig": evidence.report_signature,
        }
    ]
    store.builds[build_id] = rec.model_copy(
        update={"status": "committed", "journal": journal, "is_candidate": False}
    )
    return {
        "ok": True,
        "idempotent": False,
        "active": build_id,
        "previous": prev,
    }
