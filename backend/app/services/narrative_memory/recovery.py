"""Failure classification and idempotent resume for the whole-book builder.

Phase 28-01 (REQ-NM-01): every stage converges to a durable terminal state
(``completed`` | ``isolated`` | ``blocked``) or a recoverable checkpoint; a
single chapter failure isolates that chapter and blocks only its dependents —
never an unconditional whole-book restart (D-02/D-03/D-04). Exact cache reuse
requires checksum-identical inputs (D-04). All outputs remain immutable
candidate-only; nothing here writes a pointer or performs a cutover (D-07).

This module is deliberately free of transport/provider imports so recovery
logic is pure and replayable from durable rows only.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildStage
from app.services.narrative_memory.builder_contracts import (
    ReasonCode,
    ResumePlan,
    ResumePlanItem,
    StageLineage,
    TerminalState,
    classify_failure,
)
from app.services.narrative_memory.builder_repository import BuilderRepository

# Statuses that never carry a terminal state and remain resumable.
RESUMABLE_STATUSES = frozenset({"pending", "running", "paused"})
# Statuses whose durable row already encodes an explicit terminal state.
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "isolated",
        "paused_budget",
        "paused_dependency",
        "cancelled",
        "blocked_dependency",
    }
)


class RecoveryError(RuntimeError):
    """Raised when recovery preconditions are violated."""


def terminal_state_for_status(status: str | None) -> str | None:
    if status == "completed":
        return TerminalState.COMPLETED.value
    if status in {"failed", "isolated", "paused_budget", "paused_dependency", "cancelled"}:
        return TerminalState.ISOLATED.value
    if status == "blocked_dependency":
        return TerminalState.BLOCKED.value
    return None


def is_silently_pending(stage: NarrativeMemoryBuildStage) -> bool:
    """A stage that never received a durable transition is silently pending."""
    if terminal_state_for_status(stage.status) is not None:
        return False
    if stage.status not in RESUMABLE_STATUSES:
        return False
    checkpoint = dict(stage.checkpoint or {})
    return not checkpoint and not stage.status_reason and not stage.reason_code


def build_resume_plan(
    stages: Sequence[NarrativeMemoryBuildStage],
) -> ResumePlan:
    """Derive a deterministic resume plan from durable stage rows only.

    Terminal stages (completed/isolated/blocked) are never re-run; only stages
    whose rows carry no terminal state remain runnable. The plan is the single
    source of truth for what a resumed worker may touch.
    """
    runnable: list[ResumePlanItem] = []
    terminal: list[ResumePlanItem] = []
    silent_pending: list[str] = []
    for stage in stages:
        terminal_state = terminal_state_for_status(stage.status)
        item = ResumePlanItem(
            stage_key=stage.stage_key,
            status=stage.status,
            terminal_state=terminal_state,
            reason_code=stage.reason_code,
            attempt_count=int(stage.attempt_count or 0),
            runnable=terminal_state is None,
            blocked_by=tuple(stage.dependency_keys or ()),
        )
        if terminal_state is None:
            runnable.append(item)
            if is_silently_pending(stage):
                silent_pending.append(stage.stage_key)
        else:
            terminal.append(item)
    run_id = stages[0].run_id if stages else 0
    return ResumePlan(
        run_id=run_id,
        runnable=tuple(runnable),
        terminal=tuple(terminal),
        has_silent_pending=bool(silent_pending),
        silent_pending_keys=tuple(sorted(silent_pending)),
    )


def validate_cache_reuse(
    *,
    stored_source_checksum: str | None,
    stored_lineage: dict[str, Any] | None,
    stored_package_checksum: str | None,
    current_source_checksum: str | None,
    current_lineage: StageLineage | None,
    current_package_checksum: str | None,
) -> tuple[bool, ReasonCode | None]:
    """Exact-cache gate: only checksum-identical inputs may be reused (D-04).

    Returns ``(ok, reason)``. A ``False`` result carries a stable reason code
    (``source_snapshot_drift``, ``stale_cache_rejected``) so the failure is
    replayable and auditable.
    """
    if stored_source_checksum and current_source_checksum:
        if stored_source_checksum != current_source_checksum:
            return False, ReasonCode.SOURCE_DRIFT
    if stored_lineage and current_lineage is not None:
        current = current_lineage.model_dump(mode="json")
        if stored_lineage != current:
            return False, ReasonCode.STALE_CACHE
    if stored_package_checksum and current_package_checksum:
        if stored_package_checksum != current_package_checksum:
            return False, ReasonCode.STALE_CACHE
    return True, None


class RecoveryCoordinator:
    """Coordinates failure classification, isolation, and idempotent resume."""

    def __init__(self, repo: BuilderRepository) -> None:
        self._repo = repo

    async def resume_plan(self, run_id: int) -> ResumePlan:
        stages = await self._repo.list_stages(run_id)
        plan = build_resume_plan(stages)
        return plan

    async def isolate_chapter(
        self,
        session: AsyncSession,
        *,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        exc: BaseException,
        attempt_count: int | None = None,
    ) -> str:
        """Classify a chapter failure, isolate the chapter, block dependents.

        Only the failed chapter is isolated; dependent arc/global stages are
        marked ``blocked_dependency``. Completed siblings are never rewound and
        no whole-book restart is ever issued (D-03).
        """
        code, failure_class = classify_failure(exc)
        await self._repo.set_run_error_code(run_id, code.value)
        isolated = await self._repo.isolate_stage(
            stage,
            exc=exc,
            reason_code=code,
            attempt_count=attempt_count,
            journal=True,
        )
        await self._repo.block_dependents(
            run_id,
            isolated.stage_key,
            reason="dependency_failed",
            reason_code=ReasonCode.DEPENDENCY_FAILED,
            journal=True,
        )
        await session.flush()
        return code.value

    async def cancel_stage(
        self,
        *,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        reason_code: ReasonCode | str = ReasonCode.CANCELLED_BEFORE_PERSIST,
    ) -> None:
        await self._repo.mark_stage(
            stage,
            status="cancelled",
            reason=(
                reason_code.value if isinstance(reason_code, ReasonCode) else reason_code
            ),
            reason_code=reason_code,
            journal=True,
        )
        await self._repo.set_run_error_code(run_id, "cancel_requested")

    async def pause_budget(
        self,
        *,
        run_id: int,
        stage: NarrativeMemoryBuildStage,
        exc: BaseException,
    ) -> str:
        code, _ = classify_failure(exc)
        await self._repo.mark_stage(
            stage,
            status="paused_budget",
            reason=code.value,
            reason_code=code,
            journal=True,
        )
        await self._repo.update_run_status(
            run_id, status="paused_budget", reason=code.value
        )
        await self._repo.set_run_error_code(run_id, code.value)
        return code.value

    async def require_owner(
        self, *, owner_id: int, version_id: int
    ) -> None:
        """Owner/version audit gate before any recovery write (V4)."""
        version = await self._repo._session.scalar(  # noqa: SLF001
            select(NarrativeMemoryVersion).where(
                NarrativeMemoryVersion.owner_id == owner_id,
                NarrativeMemoryVersion.id == version_id,
            )
        )
        if version is None:
            raise RecoveryError(ReasonCode.OWNER_MISMATCH.value)
