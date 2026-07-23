"""Persistence helpers for narrative-memory builder runs and stages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import RunPolicy


class BuilderRepositoryError(ValueError):
    pass


class BuilderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_version(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> NarrativeMemoryVersion:
        version = await self._session.scalar(
            select(NarrativeMemoryVersion).where(
                NarrativeMemoryVersion.owner_id == owner_id,
                NarrativeMemoryVersion.novel_id == novel_id,
                NarrativeMemoryVersion.id == version_id,
            )
        )
        if version is None:
            raise BuilderRepositoryError(
                "candidate version not found in explicit scope"
            )
        return version

    async def get_run(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> NarrativeMemoryBuildRun | None:
        return await self._session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == owner_id,
                NarrativeMemoryBuildRun.novel_id == novel_id,
                NarrativeMemoryBuildRun.version_id == version_id,
            )
        )

    async def create_run(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        eligibility_report_checksum: str,
        eligibility_policy_version: str,
        run_policy: RunPolicy,
    ) -> NarrativeMemoryBuildRun:
        existing = await self.get_run(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if existing is not None:
            return existing
        max_calls, max_in, max_out, max_cost = run_policy.budget.as_decimals()
        run = NarrativeMemoryBuildRun(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            eligibility_report_checksum=eligibility_report_checksum,
            eligibility_policy_version=eligibility_policy_version,
            status="pending",
            progress={},
            run_policy=run_policy.model_dump(mode="json"),
        )
        self._session.add(run)
        await self._session.flush()
        ledger = NarrativeMemoryBuildBudgetLedger(
            run_id=run.id,
            max_calls=max_calls,
            max_input_tokens=max_in,
            max_output_tokens=max_out,
            max_cost_usd=max_cost,
        )
        self._session.add(ledger)
        await self._session.flush()
        return run

    async def ensure_stages(
        self,
        run: NarrativeMemoryBuildRun,
        stages: Sequence[dict[str, Any]],
    ) -> list[NarrativeMemoryBuildStage]:
        existing = (
            await self._session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run.id
                )
            )
        ).all()
        by_key = {row.stage_key: row for row in existing}
        created: list[NarrativeMemoryBuildStage] = []
        for spec in stages:
            stage_key = spec["stage_key"]
            if stage_key in by_key:
                created.append(by_key[stage_key])
                continue
            row = NarrativeMemoryBuildStage(
                owner_id=run.owner_id,
                novel_id=run.novel_id,
                version_id=run.version_id,
                run_id=run.id,
                stage_key=stage_key,
                stage_kind=spec["stage_kind"],
                chapter_start=spec.get("chapter_start"),
                chapter_end=spec.get("chapter_end"),
                dependency_keys=list(spec.get("dependency_keys") or []),
                status="pending",
                checkpoint={},
            )
            self._session.add(row)
            created.append(row)
        await self._session.flush()
        return created

    async def list_stages(self, run_id: int) -> list[NarrativeMemoryBuildStage]:
        rows = (
            await self._session.scalars(
                select(NarrativeMemoryBuildStage)
                .where(NarrativeMemoryBuildStage.run_id == run_id)
                .order_by(NarrativeMemoryBuildStage.id.asc())
            )
        ).all()
        return list(rows)

    async def claim_run_lease(
        self,
        run: NarrativeMemoryBuildRun,
        *,
        lease_ttl_seconds: int = 120,
        lease_id: str | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        locked = await self._session.get(
            NarrativeMemoryBuildRun, run.id, with_for_update=True
        )
        if locked is None:
            raise BuilderRepositoryError("run disappeared")
        if locked.cancel_requested and locked.status not in {
            "cancelled",
            "completed",
        }:
            locked.status = "cancelled"
            locked.status_reason = "cancel_requested"
            await self._session.flush()
            raise BuilderRepositoryError("run cancelled")
        active_lease = (
            locked.lease_id
            and locked.lease_expires_at is not None
            and locked.lease_expires_at > now
            and lease_id is not None
            and locked.lease_id != lease_id
        )
        if active_lease:
            raise BuilderRepositoryError("run lease held by another worker")
        claimed = lease_id or uuid4().hex
        locked.lease_id = claimed
        locked.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        locked.heartbeat_at = now
        if locked.status in {"pending", "partial", "paused_dependency"}:
            locked.status = "running"
        await self._session.flush()
        return claimed

    async def heartbeat(
        self, run_id: int, lease_id: str, *, lease_ttl_seconds: int = 120
    ) -> None:
        run = await self._session.get(
            NarrativeMemoryBuildRun, run_id, with_for_update=True
        )
        if run is None:
            raise BuilderRepositoryError("lease lost")
        now = datetime.now(timezone.utc)
        # Re-attach after recovery paths that may have cleared an expired lease.
        if run.lease_id not in {None, lease_id}:
            if run.lease_expires_at is not None and run.lease_expires_at > now:
                raise BuilderRepositoryError("lease lost")
        run.lease_id = lease_id
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        await self._session.flush()

    async def request_cancel(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> NarrativeMemoryBuildRun:
        run = await self.get_run(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if run is None:
            raise BuilderRepositoryError("run not found")
        locked = await self._session.get(
            NarrativeMemoryBuildRun, run.id, with_for_update=True
        )
        assert locked is not None
        locked.cancel_requested = True
        if locked.status not in {"completed", "failed", "cancelled"}:
            locked.status_reason = "cancel_requested"
        await self._session.flush()
        return locked

    async def is_cancelled(self, run_id: int) -> bool:
        run = await self._session.get(NarrativeMemoryBuildRun, run_id)
        return bool(run is not None and run.cancel_requested)

    async def mark_stage(
        self,
        stage: NarrativeMemoryBuildStage,
        *,
        status: str,
        reason: str | None = None,
        package_checksum: str | None = None,
        cache_key: str | None = None,
        artifact_checksum: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ) -> NarrativeMemoryBuildStage:
        locked = await self._session.get(
            NarrativeMemoryBuildStage, stage.id, with_for_update=True
        )
        if locked is None:
            raise BuilderRepositoryError("stage not found")
        if locked.status == "completed":
            if (
                status == "completed"
                and artifact_checksum is not None
                and locked.artifact_checksum is not None
                and artifact_checksum != locked.artifact_checksum
            ):
                raise BuilderRepositoryError("completed stage artifact conflict")
            return locked
        locked.status = status
        locked.status_reason = reason
        if package_checksum is not None:
            locked.package_checksum = package_checksum
        if cache_key is not None:
            locked.cache_key = cache_key
        if artifact_checksum is not None:
            locked.artifact_checksum = artifact_checksum
        if checkpoint is not None:
            locked.checkpoint = checkpoint
        if increment_attempt:
            locked.attempt_count = int(locked.attempt_count or 0) + 1
        await self._session.flush()
        return locked

    async def update_run_status(
        self,
        run_id: int,
        *,
        status: str,
        reason: str | None = None,
        progress: dict[str, Any] | None = None,
        boundary_plan: dict[str, Any] | None = None,
        boundary_plan_checksum: str | None = None,
    ) -> None:
        run = await self._session.get(
            NarrativeMemoryBuildRun, run_id, with_for_update=True
        )
        if run is None:
            return
        if run.status not in {"cancelled", "completed"} or status == run.status:
            run.status = status
            if reason is not None:
                run.status_reason = reason
        if progress is not None:
            run.progress = progress
        if boundary_plan is not None:
            if (
                run.boundary_plan_checksum
                and boundary_plan_checksum
                and run.boundary_plan_checksum != boundary_plan_checksum
            ):
                raise BuilderRepositoryError("boundary plan checksum conflict")
            run.boundary_plan = boundary_plan
            run.boundary_plan_checksum = boundary_plan_checksum
        await self._session.flush()
