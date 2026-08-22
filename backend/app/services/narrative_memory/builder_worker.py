"""Durable bottom-up narrative-memory candidate builder worker.

Lease-safe Chapter→Arc→Global candidate builder (no promotion). This module
owns the orchestration core — run creation/lease, source-drift fail-closed,
chapter/arc/global/manifest phase scheduling, run-status finalization and the
``WorkerResult`` contract.

拆分说明（refactor split）：per-chapter candidate build seam 拆到
``_worker_chapter.py``（ChapterStateWorkerMixin），boundary/arc/global
聚合拆到 ``_worker_hierarchy.py``（HierarchyWorkerMixin），manifest
validation 拆到 ``_worker_manifest.py``（ManifestWorkerMixin），
``FORBIDDEN_IMPORT_FRAGMENTS`` 与 forbidden-capability 扫描器拆到叶模块
``_worker_scan.py``。``NarrativeMemoryBuilderWorker`` / ``WorkerResult`` /
``scan_builder_package_for_forbidden_capabilities`` 的 import surface 不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.novel import Chapter
from app.services.narrative_memory.audit import audit_assets, provider_calls_allowed
from app.services.narrative_memory.audit_contracts import EligibilityReport
from app.services.narrative_memory.audit_sources import AssetInventorySource
from app.services.narrative_memory.builder_contracts import (
    ModelDeploymentSnapshot,
    ReasonCode,
    RunPolicy,
    StageKind,
)
from app.services.narrative_memory.builder_gateway import (
    BuilderModelGateway,
    ModelTransport,
)
from app.services.narrative_memory.builder_repository import (
    BuilderRepository,
    BuilderRepositoryError,
)
from app.services.narrative_memory.recovery import RecoveryCoordinator
from app.services.narrative_memory.source_manifest import (
    compute_source_manifest,
    detect_chapter_drift,
    frozen_manifest_from_progress,
    recompute_source_manifest,
    store_frozen_manifest,
)

from ._worker_chapter import ChapterStateWorkerMixin
from ._worker_hierarchy import HierarchyWorkerMixin
from ._worker_manifest import ManifestWorkerMixin
from ._worker_scan import scan_builder_package_for_forbidden_capabilities

__all__ = [
    "NarrativeMemoryBuilderWorker",
    "WorkerResult",
    # re-export of the static forbidden-capability scanner (moved to _worker_scan)
    "scan_builder_package_for_forbidden_capabilities",
]


@dataclass(frozen=True)
class WorkerResult:
    run_id: int
    status: str
    status_reason: str | None
    completed_stages: tuple[str, ...]
    failed_stages: tuple[str, ...]
    blocked_stages: tuple[str, ...]
    transport_calls: int
    worker_artifact_checksum: str | None = None
    database_manifest_checksum: str | None = None
    source_manifest_checksum: str | None = None


class NarrativeMemoryBuilderWorker(
    ChapterStateWorkerMixin,
    HierarchyWorkerMixin,
    ManifestWorkerMixin,
):
    """Lease-safe Chapter→Arc→Global candidate builder (no promotion)."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        inventory_source: AssetInventorySource,
        transport: ModelTransport,
        deployment: ModelDeploymentSnapshot,
        optional_source_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._sessions = sessions
        self._inventory_source = inventory_source
        self._transport = transport
        self._deployment = deployment
        self._optional_source_loader = optional_source_loader
        self.transport_calls = 0

    async def start_run(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        run_policy: RunPolicy,
        chapter_ids: Sequence[int] | None = None,
    ) -> int:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            version = await repo.get_version(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            report = await audit_assets(
                self._inventory_source, owner_id=owner_id, novel_id=novel_id
            )
            self._assert_eligibility_matches(version, report)
            run = await repo.create_run(
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_id,
                eligibility_report_checksum=version.eligibility_report_checksum,
                eligibility_policy_version=version.eligibility_policy_version,
                run_policy=run_policy,
            )
            chapters = await self._resolve_chapters(
                session,
                novel_id=novel_id,
                hierarchy_build_id=version.hierarchy_build_id,
                chapter_ids=chapter_ids,
            )
            stage_specs = [
                {
                    "stage_key": f"chapter_state:{chapter.id}",
                    "stage_kind": StageKind.CHAPTER_STATE.value,
                    "chapter_start": chapter.chapter_number,
                    "chapter_end": chapter.chapter_number,
                    "dependency_keys": [],
                    "chapter_id": chapter.id,
                }
                for chapter in chapters
            ]
            # Placeholder parent/global stages filled after boundary plan.
            stage_specs.append(
                {
                    "stage_key": "arc_volume_plan:book",
                    "stage_kind": StageKind.ARC_VOLUME_PLAN.value,
                    "dependency_keys": [
                        f"chapter_state:{chapter.id}" for chapter in chapters
                    ],
                }
            )
            await repo.ensure_stages(run, stage_specs)
            # Stash chapter id map on run progress for deterministic resume.
            progress = dict(run.progress or {})
            progress["chapter_ids"] = {
                str(chapter.chapter_number): chapter.id for chapter in chapters
            }
            progress["chapter_numbers"] = [
                chapter.chapter_number for chapter in chapters
            ]
            # Freeze the source manifest (D-05): the snapshot drives chapter
            # execution and is DB-recomputable; drift fails closed on resume.
            manifest = await compute_source_manifest(
                session, version=version, chapters=chapters
            )
            progress = store_frozen_manifest(progress, manifest)
            await repo.update_run_status(run.id, status=run.status, progress=progress)
            run.source_snapshot_hash = version.source_snapshot_hash
            await session.commit()
            return run.id

    async def process_run(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        lease_id: str | None = None,
        max_stages: int | None = None,
    ) -> WorkerResult:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            version = await repo.get_version(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            run = await repo.get_run(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            if run is None:
                raise BuilderRepositoryError("run not found; call start_run first")
            report = await audit_assets(
                self._inventory_source, owner_id=owner_id, novel_id=novel_id
            )
            self._assert_eligibility_matches(version, report)
            claimed_lease = await repo.claim_run_lease(run, lease_id=lease_id)
            run_id = int(run.id)
            policy = RunPolicy.model_validate(run.run_policy)
            gateway = BuilderModelGateway(
                session,
                transport=self._transport,
                deployment=self._deployment,
                max_schema_repairs=policy.max_schema_repairs,
            )

            if not provider_calls_allowed(report):
                await repo.update_run_status(
                    run_id,
                    status="paused_dependency",
                    reason="provider_calls_not_allowed",
                )
                await session.commit()
                return await self._snapshot_result(
                    session, run_id, gateway.transport_calls
                )

            recovery = RecoveryCoordinator(repo)
            await repo.increment_resume_count(run_id)

            # Source drift fail-closed (D-05): recompute the frozen manifest
            # from current authority rows; any drifted chapter is blocked rather
            # than re-run against stale evidence, and never restarts the book.
            source_drift = await self._source_drift_map(
                session, run=run, version=version
            )

            processed = 0
            # Phase A: chapter states — resume plan drives what may run.
            plan = await recovery.resume_plan(run_id)
            runnable_keys = {item.stage_key for item in plan.runnable}
            stages = await repo.list_stages(run_id)
            chapter_stages = [
                s for s in stages if s.stage_kind == StageKind.CHAPTER_STATE.value
            ]
            for stage in chapter_stages:
                if max_stages is not None and processed >= max_stages:
                    break
                # Terminal stages (completed/isolated/blocked) are never re-run.
                if stage.stage_key not in runnable_keys:
                    continue
                if await repo.is_cancelled(run_id):
                    await repo.update_run_status(
                        run_id, status="cancelled", reason="cancel_requested"
                    )
                    break
                chapter_number = int(stage.chapter_start or 0)
                if chapter_number in source_drift:
                    # Fail closed: block the drifted chapter with a stable
                    # reason so the run is partial, not silently stale.
                    await repo.mark_stage(
                        stage,
                        status="blocked_dependency",
                        reason=source_drift[chapter_number],
                        reason_code=ReasonCode.SOURCE_DRIFT,
                        journal=True,
                    )
                    processed += 1
                    await session.flush()
                    await repo.heartbeat(run_id, claimed_lease)
                    continue
                await self._run_chapter_stage(
                    session,
                    repo=repo,
                    gateway=gateway,
                    version=version,
                    run_id=run_id,
                    stage=stage,
                    policy=policy,
                    recovery=recovery,
                )
                processed += 1
                await session.flush()
                await repo.heartbeat(run_id, claimed_lease)

            # Phase B: boundary plan + arc aggregates (if arc planner available)
            stages = await repo.list_stages(run_id)
            if all(
                s.status == "completed"
                for s in stages
                if s.stage_kind == StageKind.CHAPTER_STATE.value
            ):
                await self._ensure_boundary_and_parents(
                    session,
                    repo=repo,
                    gateway=gateway,
                    version=version,
                    run_id=run_id,
                    policy=policy,
                    max_stages=max_stages,
                    processed=processed,
                    lease_id=claimed_lease,
                    recovery=recovery,
                )

            # Phase C: global + manifest if parents complete
            await self._maybe_run_global_and_manifest(
                session,
                repo=repo,
                gateway=gateway,
                version=version,
                run_id=run_id,
                policy=policy,
                recovery=recovery,
            )

            self.transport_calls += gateway.transport_calls
            result = await self._finalize_run_status(session, repo, run_id)
            await session.commit()
            return result

    async def cancel(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> WorkerResult:
        async with self._sessions() as session:
            repo = BuilderRepository(session)
            run = await repo.request_cancel(
                owner_id=owner_id, novel_id=novel_id, version_id=version_id
            )
            await session.commit()
            return await self._snapshot_result(session, run.id, 0)

    async def _source_drift_map(
        self,
        session: AsyncSession,
        *,
        run: NarrativeMemoryBuildRun,
        version: NarrativeMemoryVersion,
    ) -> dict[int, str]:
        """Recompute the frozen manifest and return per-chapter drift reasons.

        Empty dict means the source is unchanged and the run may proceed. A
        non-empty dict means those chapters must be blocked (fail closed) rather
        than processed with stale evidence (D-05).
        """
        frozen = frozen_manifest_from_progress(run.progress)
        if frozen is None:
            return {}
        recomputed = await recompute_source_manifest(session, version=version)
        return detect_chapter_drift(frozen, recomputed)

    async def _resolve_chapters(
        self,
        session: AsyncSession,
        *,
        novel_id: int,
        hierarchy_build_id: str,
        chapter_ids: Sequence[int] | None,
    ) -> list[Chapter]:
        if chapter_ids:
            rows = (
                await session.scalars(
                    select(Chapter)
                    .where(
                        Chapter.novel_id == novel_id, Chapter.id.in_(tuple(chapter_ids))
                    )
                    .order_by(Chapter.chapter_number.asc())
                )
            ).all()
            return list(rows)
        # Distinct chapters present as evidence leaves in frozen hierarchy.
        chapter_id_rows = (
            await session.execute(
                select(ChunkHierarchyNode.chapter_id)
                .where(
                    ChunkHierarchyNode.build_id == hierarchy_build_id,
                    ChunkHierarchyNode.novel_id == novel_id,
                    ChunkHierarchyNode.level == "evidence",
                    ChunkHierarchyNode.chapter_id.is_not(None),
                )
                .distinct()
            )
        ).all()
        ids = [int(row[0]) for row in chapter_id_rows]
        if not ids:
            raise BuilderRepositoryError("no hierarchy chapters available")
        rows = (
            await session.scalars(
                select(Chapter)
                .where(Chapter.novel_id == novel_id, Chapter.id.in_(ids))
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
        return list(rows)

    def _assert_eligibility_matches(
        self, version: NarrativeMemoryVersion, report: EligibilityReport
    ) -> None:
        from app.services.narrative_memory.audit_contracts import (
            AssetKind,
            EligibilityStatus,
        )
        from app.services.narrative_memory.authority import CandidateAuthority

        if report.policy_version != version.eligibility_policy_version:
            raise BuilderRepositoryError("eligibility policy version mismatch")

        # Exact full-report checksum is ideal, but multi-hour candidate builds
        # routinely see optional-domain drift (timeline/relationship/clue). Versions
        # are append-only, so we accept resume when the required hierarchy lineage
        # still matches the frozen version and provider_calls remain allowed.
        checksum = CandidateAuthority._eligibility_checksum(report)  # noqa: SLF001
        if checksum != version.eligibility_report_checksum:
            hierarchy = next(
                (a for a in report.assets if a.kind == AssetKind.HIERARCHY),
                None,
            )
            if (
                hierarchy is None
                or hierarchy.status != EligibilityStatus.REUSABLE_EXACT
                or hierarchy.version_id != version.hierarchy_build_id
            ):
                raise BuilderRepositoryError(
                    "eligibility report checksum mismatch and hierarchy lineage drifted"
                )
        if not provider_calls_allowed(report):
            # Caller may still create run; process_run pauses before transport.
            return

    async def _finalize_run_status(
        self, session: AsyncSession, repo: BuilderRepository, run_id: int
    ) -> WorkerResult:
        from app.models.narrative_memory_builder import NarrativeMemoryBuildRun

        await repo.recompute_terminal_states(run_id)
        stages = await repo.list_stages(run_id)
        completed = tuple(s.stage_key for s in stages if s.status == "completed")
        failed = tuple(s.stage_key for s in stages if s.status == "failed")
        blocked = tuple(s.stage_key for s in stages if s.status == "blocked_dependency")
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        assert run is not None
        if run.cancel_requested and run.status != "completed":
            status = "cancelled"
            reason = "cancel_requested"
            await repo.set_run_error_code(run_id, "cancel_requested")
        elif run.status == "paused_budget":
            status = "paused_budget"
            reason = run.status_reason
        elif failed and completed:
            status = "partial"
            reason = "chapter_or_parent_failed"
        elif failed and not completed:
            status = "failed"
            reason = "all_failed"
        elif blocked:
            # D-05: drift-blocked chapters fail closed — the run is partial,
            # never falsely "completed" and never an unconditional restart.
            status = "partial"
            reason = "chapter_or_parent_blocked"
        elif stages and all(
            s.status == "completed"
            for s in stages
            if s.stage_kind
            in {
                StageKind.CHAPTER_STATE.value,
                StageKind.ARC_VOLUME_PLAN.value,
                StageKind.ARC_VOLUME_AGGREGATE.value,
                StageKind.GLOBAL_AGGREGATE.value,
                StageKind.MANIFEST_VALIDATION.value,
            }
            and s.stage_kind != StageKind.ARC_VOLUME_PLAN.value
            or s.status in {"completed", "pending"}
        ):
            # Completed only when global+manifest done if those stages exist.
            has_global = any(
                s.stage_kind == StageKind.GLOBAL_AGGREGATE.value for s in stages
            )
            global_done = any(
                s.stage_kind == StageKind.GLOBAL_AGGREGATE.value
                and s.status == "completed"
                for s in stages
            )
            manifest_done = any(
                s.stage_kind == StageKind.MANIFEST_VALIDATION.value
                and s.status == "completed"
                for s in stages
            )
            chapters_done = all(
                s.status == "completed"
                for s in stages
                if s.stage_kind == StageKind.CHAPTER_STATE.value
            )
            if chapters_done and (not has_global or (global_done and manifest_done)):
                if has_global and global_done and manifest_done:
                    status = "completed"
                    reason = "completed_candidate"
                elif chapters_done and not has_global:
                    status = (
                        "running"
                        if any(s.status == "pending" for s in stages)
                        else "partial"
                    )
                    reason = "chapters_complete"
                else:
                    status = "partial" if failed or blocked else "running"
                    reason = run.status_reason
            else:
                status = "partial" if failed or blocked else "running"
                reason = run.status_reason
        else:
            status = run.status
            reason = run.status_reason

        await repo.update_run_status(run_id, status=status, reason=reason)
        return WorkerResult(
            run_id=run_id,
            status=status,
            status_reason=reason,
            completed_stages=completed,
            failed_stages=failed,
            blocked_stages=blocked,
            transport_calls=self.transport_calls,
            source_manifest_checksum=(
                (run.progress or {}).get("source_manifest_checksum") if run else None
            ),
        )

    async def _snapshot_result(
        self, session: AsyncSession, run_id: int, transport_calls: int
    ) -> WorkerResult:
        repo = BuilderRepository(session)
        stages = await repo.list_stages(run_id)
        from app.models.narrative_memory_builder import NarrativeMemoryBuildRun

        run = await session.get(NarrativeMemoryBuildRun, run_id)
        return WorkerResult(
            run_id=run_id,
            status=run.status if run else "failed",
            status_reason=run.status_reason if run else "missing",
            completed_stages=tuple(
                s.stage_key for s in stages if s.status == "completed"
            ),
            failed_stages=tuple(s.stage_key for s in stages if s.status == "failed"),
            blocked_stages=tuple(
                s.stage_key for s in stages if s.status == "blocked_dependency"
            ),
            transport_calls=transport_calls,
            source_manifest_checksum=(
                (run.progress or {}).get("source_manifest_checksum") if run else None
            ),
        )
