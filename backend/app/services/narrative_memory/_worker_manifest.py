"""Manifest-validation mixin for the narrative-memory builder worker.

Extracted from ``builder_worker.py`` (Phase 14/28 builder report + manifest
seal): this mixin owns the final manifest_validation stage — recomputing the
candidate manifest from the persisted snapshot, sealing/reporting it, and
writing the durable build report. It never promotes and never writes active
pointers (D-02/D-07); a structural failure fails the stage closed instead of
silently sealing.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.builder_contracts import ReasonCode
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.recovery import RecoveryCoordinator


class ManifestWorkerMixin:
    """Manifest validation + build-report seam (see module docstring)."""

    async def _run_manifest_stage(
        self,
        session: AsyncSession,
        *,
        repo: BuilderRepository,
        version: NarrativeMemoryVersion,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        worker_artifact: str | None,
        recovery: RecoveryCoordinator | None = None,
    ) -> None:
        await repo.mark_stage(stage, status="running", increment_attempt=True)
        try:
            from app.services.narrative_memory.builder_report import write_build_report
            from app.services.narrative_memory.manifests import (
                SealConflictError,
                compute_manifest_from_snapshot,
                load_candidate_snapshot,
                seal_and_report,
            )

            snapshot = await load_candidate_snapshot(
                session,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
            )
            computation = compute_manifest_from_snapshot(snapshot)
            db_checksum = computation.manifest_checksum
            try:
                sealed = await seal_and_report(
                    session,
                    owner_id=version.owner_id,
                    novel_id=version.novel_id,
                    version_id=version.id,
                )
                db_checksum = sealed.manifest_checksum
                if not sealed.structural.ok:
                    await repo.mark_stage(
                        stage,
                        status="failed",
                        reason="structural_blocked",
                        reason_code=ReasonCode.DEPENDENCY_FAILED,
                        checkpoint={
                            "reasons": list(sealed.structural.reason_codes),
                            "database_manifest_checksum": db_checksum,
                        },
                        journal=True,
                    )
                    await write_build_report(
                        session,
                        run_id=run_id,
                        owner_id=version.owner_id,
                        novel_id=version.novel_id,
                        version_id=version.id,
                        worker_artifact_checksum=worker_artifact,
                        database_manifest_checksum=db_checksum,
                    )
                    return
            except SealConflictError:
                # Already sealed — recompute only.
                pass
            await write_build_report(
                session,
                run_id=run_id,
                owner_id=version.owner_id,
                novel_id=version.novel_id,
                version_id=version.id,
                worker_artifact_checksum=worker_artifact,
                database_manifest_checksum=db_checksum,
            )
            await repo.mark_stage(
                stage,
                status="completed",
                artifact_checksum=db_checksum,
                package_checksum=db_checksum,
                reason_code=ReasonCode.COMPLETED_CANDIDATE,
                checkpoint={
                    "database_manifest_checksum": db_checksum,
                    "worker_artifact_checksum": worker_artifact,
                },
                journal=True,
            )
        except Exception as exc:  # noqa: BLE001
            await repo.mark_stage(
                stage,
                status="failed",
                reason=f"{type(exc).__name__}:{str(exc)}"[:160],
                reason_code=ReasonCode.INTERNAL_ERROR,
                journal=True,
            )
