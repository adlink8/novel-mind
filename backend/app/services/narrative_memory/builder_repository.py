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
    NarrativeMemoryBuildCheckpoint,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import (
    ReasonCode,
    RunPolicy,
    TerminalState,
    classify_failure,
)


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
        reason_code: ReasonCode | str | None = None,
        package_checksum: str | None = None,
        cache_key: str | None = None,
        artifact_checksum: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        increment_attempt: bool = False,
        source_checksum: str | None = None,
        model_lineage: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        journal: bool = False,
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
        previous_terminal = self._terminal_for_status(locked.status)
        locked.status = status
        locked.status_reason = reason
        reason_code_value = (
            reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
        )
        if reason_code_value is not None:
            locked.reason_code = reason_code_value
        locked.terminal_state = self._terminal_for_status(status)
        if package_checksum is not None:
            locked.package_checksum = package_checksum
        if cache_key is not None:
            locked.cache_key = cache_key
        if artifact_checksum is not None:
            locked.artifact_checksum = artifact_checksum
        if checkpoint is not None:
            checkpoint = dict(checkpoint)
            if reason_code_value is not None:
                checkpoint["reason_code"] = reason_code_value
            if self._terminal_for_status(status) is not None:
                checkpoint["terminal_state"] = self._terminal_for_status(status)
            locked.checkpoint = checkpoint
        if source_checksum is not None:
            locked.source_checksum = source_checksum
        if model_lineage is not None:
            locked.model_lineage = model_lineage
        if idempotency_key is not None:
            locked.idempotency_key = idempotency_key
        if increment_attempt:
            locked.attempt_count = int(locked.attempt_count or 0) + 1
        if journal and previous_terminal != locked.terminal_state:
            await self.record_checkpoint(
                run_id=locked.run_id,
                stage_key=locked.stage_key,
                terminal_state=locked.terminal_state,
                reason_code=locked.reason_code,
                attempt_count=int(locked.attempt_count or 0),
                checkpoint=dict(locked.checkpoint or {}),
            )
        await self._session.flush()
        return locked

    async def record_checkpoint(
        self,
        *,
        run_id: int,
        stage_key: str,
        terminal_state: str | None,
        reason_code: str | None = None,
        attempt_count: int = 0,
        checkpoint: dict[str, Any] | None = None,
    ) -> NarrativeMemoryBuildCheckpoint:
        """Append an immutable recovery checkpoint journal row (D-04)."""
        row = NarrativeMemoryBuildCheckpoint(
            run_id=run_id,
            stage_key=stage_key,
            terminal_state=terminal_state or TerminalState.BLOCKED.value,
            reason_code=reason_code,
            attempt_count=attempt_count,
            checkpoint=checkpoint or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def block_dependents(
        self,
        run_id: int,
        stage_key: str,
        *,
        reason: str = "dependency_failed",
        reason_code: ReasonCode | str = ReasonCode.DEPENDENCY_FAILED,
        journal: bool = True,
    ) -> list[NarrativeMemoryBuildStage]:
        """Mark every stage transitively depending on `stage_key` as blocked.

        Only explicit terminal `blocked_dependency` rows are written; completed
        stages are never rewound, so a single chapter failure never triggers a
        whole-book restart (D-03).
        """
        stages = await self.list_stages(run_id)
        by_key = {s.stage_key: s for s in stages}
        if stage_key not in by_key:
            return []
        dependents = self._transitive_dependents(stages, stage_key)
        blocked: list[NarrativeMemoryBuildStage] = []
        for dependent in dependents:
            row = by_key[dependent]
            if row.status in {"completed", "failed", "cancelled", "isolated"}:
                continue
            if row.status == "blocked_dependency":
                continue
            await self.mark_stage(
                row,
                status="blocked_dependency",
                reason=reason,
                reason_code=reason_code,
                journal=journal,
            )
            blocked.append(row)
        return blocked

    async def isolate_stage(
        self,
        stage: NarrativeMemoryBuildStage,
        *,
        exc: BaseException | None = None,
        reason_code: ReasonCode | str | None = None,
        attempt_count: int | None = None,
        journal: bool = True,
    ) -> NarrativeMemoryBuildStage:
        """Classify a failure and write an explicit isolated terminal state."""
        if reason_code is None:
            code, _ = (
                classify_failure(exc)
                if exc is not None
                else (
                    ReasonCode.INTERNAL_ERROR,
                    None,
                )
            )
            reason_code = code
        attempt = (
            attempt_count
            if attempt_count is not None
            else int(stage.attempt_count or 0) + 1
        )
        return await self.mark_stage(
            stage,
            status="failed",
            reason=(
                reason_code.value
                if isinstance(reason_code, ReasonCode)
                else reason_code
            ),
            reason_code=reason_code,
            checkpoint={
                "recoverable": True,
                "isolated": True,
                "attempt_count": attempt,
            },
            journal=journal,
        )

    async def recompute_terminal_states(self, run_id: int) -> None:
        """Normalise terminal_state for every stage from its durable status."""
        stages = await self.list_stages(run_id)
        for stage in stages:
            terminal = self._terminal_for_status(stage.status)
            if terminal is not None and stage.terminal_state != terminal:
                stage.terminal_state = terminal
        await self._session.flush()

    async def increment_resume_count(self, run_id: int) -> int:
        run = await self._session.get(
            NarrativeMemoryBuildRun, run_id, with_for_update=True
        )
        if run is None:
            raise BuilderRepositoryError("run not found")
        run.resume_count = int(run.resume_count or 0) + 1
        await self._session.flush()
        return int(run.resume_count)

    async def get_ledger_totals(self, run_id: int) -> dict[str, Any]:
        """Replayable calls/tokens/cost/cache totals from the durable ledger."""
        ledger = await self._session.scalar(
            select(NarrativeMemoryBuildBudgetLedger).where(
                NarrativeMemoryBuildBudgetLedger.run_id == run_id
            )
        )
        if ledger is None:
            return {
                "settled_calls": 0,
                "settled_input_tokens": 0,
                "settled_output_tokens": 0,
                "settled_cost_usd": "0",
                "cache_hits": 0,
                "cache_cost_usd": "0",
            }
        return {
            "settled_calls": int(ledger.settled_calls),
            "settled_input_tokens": int(ledger.settled_input_tokens),
            "settled_output_tokens": int(ledger.settled_output_tokens),
            "settled_cost_usd": str(ledger.settled_cost_usd),
            "cache_hits": int(ledger.cache_hits),
            "cache_cost_usd": str(ledger.cache_cost_usd),
        }

    async def set_run_error_code(self, run_id: int, error_code: str) -> None:
        run = await self._session.get(NarrativeMemoryBuildRun, run_id)
        if run is not None:
            run.last_error_code = error_code[:80]
            await self._session.flush()

    @staticmethod
    def _terminal_for_status(status: str | None) -> str | None:
        if status == "completed":
            return TerminalState.COMPLETED.value
        if status in {
            "failed",
            "paused_budget",
            "paused_dependency",
            "cancelled",
            "isolated",
        }:
            return TerminalState.ISOLATED.value
        if status == "blocked_dependency":
            return TerminalState.BLOCKED.value
        return None

    @staticmethod
    def _transitive_dependents(
        stages: Sequence[NarrativeMemoryBuildStage], stage_key: str
    ) -> list[str]:
        dependents_of: dict[str, set[str]] = {}
        for stage in stages:
            for dep in stage.dependency_keys:
                dependents_of.setdefault(dep, set()).add(stage.stage_key)
        blocked: set[str] = set()
        frontier = list(dependents_of.get(stage_key, set()))
        while frontier:
            current = frontier.pop()
            if current in blocked:
                continue
            blocked.add(current)
            frontier.extend(dependents_of.get(current, set()))
        return sorted(blocked)

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
