"""Frozen dirty-only Phase 14 stage mask from Phase 16 rebuild plans.

Provider-free planning. Only dirty stage keys may enter Phase 14 worker paths.
Carried items create no Phase 14 stage/call/reservation rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_rebuild import (
    NarrativeMemoryRebuildItem,
    NarrativeMemoryRebuildPlan,
)
from app.services.narrative_memory.builder_contracts import RunPolicy, StageKind
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.carry_forward import (
    CarryForwardError,
    CarryResult,
    carry_forward_from_plan,
)
from app.services.narrative_memory.rebuild_contracts import RebuildDecision


class RebuildExecutorError(ValueError):
    pass


@dataclass(frozen=True)
class DirtyStageMask:
    plan_id: int
    plan_checksum: str
    dirty_stage_keys: tuple[str, ...]
    carried_asset_keys: tuple[str, ...]
    stale_asset_keys: tuple[str, ...]
    stage_specs: tuple[dict[str, Any], ...]


def executor_has_provider_capability() -> bool:
    return False


async def load_plan_items(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
) -> tuple[NarrativeMemoryRebuildPlan, list[NarrativeMemoryRebuildItem]]:
    plan = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.owner_id == owner_id,
            NarrativeMemoryRebuildPlan.novel_id == novel_id,
            NarrativeMemoryRebuildPlan.id == plan_id,
        )
    )
    if plan is None:
        raise RebuildExecutorError("plan not found")
    items = list(
        (
            await session.scalars(
                select(NarrativeMemoryRebuildItem).where(
                    NarrativeMemoryRebuildItem.plan_id == plan.id
                )
            )
        ).all()
    )
    return plan, items


def build_dirty_stage_mask(
    plan: NarrativeMemoryRebuildPlan,
    items: Sequence[NarrativeMemoryRebuildItem],
) -> DirtyStageMask:
    """Translate rebuild items into Phase 14 stage specs for dirty assets only."""

    dirty_keys: list[str] = []
    carried: list[str] = []
    stale: list[str] = []
    specs: list[dict[str, Any]] = []

    for item in sorted(items, key=lambda i: i.asset_key):
        if item.decision == RebuildDecision.CARRIED.value:
            carried.append(item.asset_key)
            continue
        if item.decision == RebuildDecision.STALE_BLOCKED.value:
            stale.append(item.asset_key)
            continue
        if item.decision != RebuildDecision.DIRTY.value:
            continue
        stage_key = item.stage_key
        if not stage_key:
            continue
        dirty_keys.append(stage_key)
        kind = _stage_kind_for_asset(item.asset_kind, stage_key)
        if kind is None:
            continue
        specs.append(
            {
                "stage_key": stage_key,
                "stage_kind": kind,
                "chapter_start": item.chapter_start,
                "chapter_end": item.chapter_end,
                "dependency_keys": list(item.predecessor_keys or []),
            }
        )

    # Include boundary plan stage only when dirty AND not already carried.
    # A carried boundary_plan must never receive a Phase 14 stage row.
    boundary_carried = any(
        item.decision == RebuildDecision.CARRIED.value
        and (
            item.stage_key == "arc_volume_plan:book"
            or item.asset_kind == "boundary_plan"
        )
        for item in items
    )
    if (
        not boundary_carried
        and any(
            s["stage_kind"]
            in {
                StageKind.ARC_VOLUME_AGGREGATE.value,
                StageKind.GLOBAL_AGGREGATE.value,
            }
            for s in specs
        )
        and not any(s["stage_key"] == "arc_volume_plan:book" for s in specs)
    ):
        specs.append(
            {
                "stage_key": "arc_volume_plan:book",
                "stage_kind": StageKind.ARC_VOLUME_PLAN.value,
                "dependency_keys": [
                    s["stage_key"]
                    for s in specs
                    if s["stage_kind"] == StageKind.CHAPTER_STATE.value
                ],
            }
        )
        dirty_keys.append("arc_volume_plan:book")

    return DirtyStageMask(
        plan_id=plan.id,
        plan_checksum=plan.plan_checksum,
        dirty_stage_keys=tuple(sorted(set(dirty_keys))),
        carried_asset_keys=tuple(sorted(carried)),
        stale_asset_keys=tuple(sorted(stale)),
        stage_specs=tuple(specs),
    )


def _stage_kind_for_asset(asset_kind: str, stage_key: str) -> str | None:
    if asset_kind == "chapter_state" or stage_key.startswith("chapter_state:"):
        return StageKind.CHAPTER_STATE.value
    if asset_kind in {"story_arc", "volume"}:
        return StageKind.ARC_VOLUME_AGGREGATE.value
    if asset_kind == "global_story":
        return StageKind.GLOBAL_AGGREGATE.value
    if asset_kind == "boundary_plan" or stage_key == "arc_volume_plan:book":
        return StageKind.ARC_VOLUME_PLAN.value
    return None


async def materialize_carry_and_dirty_stages(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
    run_policy: RunPolicy,
    expected_plan_checksum: str | None = None,
) -> tuple[CarryResult, DirtyStageMask, int]:
    """Carry clean assets then create Phase 14 stages only for dirty keys.

    Returns (carry_result, mask, run_id).
    """

    plan, items = await load_plan_items(
        session, owner_id=owner_id, novel_id=novel_id, plan_id=plan_id
    )
    if expected_plan_checksum and plan.plan_checksum != expected_plan_checksum:
        raise RebuildExecutorError("plan checksum mismatch after run creation")
    if any(i.decision == RebuildDecision.STALE_BLOCKED.value for i in items):
        # Stale items block sealing; still allow dirty stage materialization for
        # non-dependent work but record in mask.
        pass

    try:
        carry_result = await carry_forward_from_plan(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            plan_id=plan_id,
            expected_plan_checksum=expected_plan_checksum or plan.plan_checksum,
        )
    except CarryForwardError as exc:
        raise RebuildExecutorError(str(exc)) from exc

    mask = build_dirty_stage_mask(plan, items)
    repo = BuilderRepository(session)
    version = await repo.get_version(
        owner_id=owner_id, novel_id=novel_id, version_id=plan.target_version_id
    )
    run = await repo.create_run(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=plan.target_version_id,
        eligibility_report_checksum=version.eligibility_report_checksum,
        eligibility_policy_version=version.eligibility_policy_version,
        run_policy=run_policy,
    )
    # Freeze rebuild plan identity on run progress (not a second status enum).
    progress = dict(run.progress or {})
    if (
        progress.get("rebuild_plan_checksum")
        and progress["rebuild_plan_checksum"] != plan.plan_checksum
    ):
        raise RebuildExecutorError("changed plan after run creation")
    progress["rebuild_plan_id"] = plan.id
    progress["rebuild_plan_checksum"] = plan.plan_checksum
    progress["dirty_stage_keys"] = list(mask.dirty_stage_keys)
    progress["carried_asset_keys"] = list(mask.carried_asset_keys)
    await repo.update_run_status(run.id, status=run.status, progress=progress)

    # Only dirty stages — never create stages for carried assets.
    if mask.stage_specs:
        await repo.ensure_stages(run, list(mask.stage_specs))

    # Prove no carried stage keys were created
    stages = await repo.list_stages(run.id)
    stage_keys = {s.stage_key for s in stages}
    for key in mask.carried_asset_keys:
        # carried asset keys are semantic; stage_key for chapter may match
        item = next((i for i in items if i.asset_key == key), None)
        if item and item.stage_key and item.stage_key in stage_keys:
            if item.decision == RebuildDecision.CARRIED.value:
                raise RebuildExecutorError(
                    f"carried asset created Phase 14 stage: {item.stage_key}"
                )

    return carry_result, mask, int(run.id)


def assert_stage_allowed(mask: DirtyStageMask, stage_key: str) -> None:
    if stage_key not in mask.dirty_stage_keys:
        raise RebuildExecutorError(
            f"stage {stage_key} not in frozen dirty closure"
        )
