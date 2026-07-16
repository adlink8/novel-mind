"""Deterministic reuse economics report from durable PostgreSQL authority.

Separates observed actual, full-rebuild upper bound, avoided upper bound,
carry reuse and exact-cache reuse. Never uses worker self-reported counters.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import NarrativeMemoryManifest
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildBudgetReservation,
    NarrativeMemoryBuildModelCallAttempt,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.models.narrative_memory_rebuild import (
    NarrativeMemoryRebuildItem,
    NarrativeMemoryRebuildPlan,
    NarrativeMemoryReuseReport,
)
from app.services.narrative_memory.rebuild_contracts import (
    RebuildDecision,
    stable_checksum,
)


class ReuseReportError(ValueError):
    pass


def report_has_provider_capability() -> bool:
    return False


def coalesce_ranges(numbers: list[int]) -> list[list[int]]:
    if not numbers:
        return []
    ordered = sorted(set(numbers))
    ranges: list[list[int]] = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append([start, prev])
        start = prev = n
    ranges.append([start, prev])
    return ranges


def compute_avoided_upper_bound(
    *,
    full_calls: int,
    full_input: int,
    full_output: int,
    full_cost: Decimal,
    observed_calls: int,
    observed_input: int,
    observed_output: int,
    observed_cost: Decimal,
) -> dict[str, Any]:
    avoided_calls = max(0, full_calls - observed_calls)
    avoided_input = max(0, full_input - observed_input)
    avoided_output = max(0, full_output - observed_output)
    avoided_cost = max(Decimal("0"), full_cost - observed_cost)
    return {
        "calls": avoided_calls,
        "input_tokens": avoided_input,
        "output_tokens": avoided_output,
        "cost_usd": str(avoided_cost),
        "formula": (
            "max(0, full_rebuild_upper_bound - observed_actual) "
            "per metric; cost uses Decimal floor at zero"
        ),
    }


async def recompute_reuse_report(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
    full_rebuild_stage_count: int | None = None,
    reservation_envelope_input: int = 0,
    reservation_envelope_output: int = 0,
    price_input_per_million: str | None = None,
    price_output_per_million: str | None = None,
) -> dict[str, Any]:
    """Recompute report body purely from durable rows."""

    plan = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.owner_id == owner_id,
            NarrativeMemoryRebuildPlan.novel_id == novel_id,
            NarrativeMemoryRebuildPlan.id == plan_id,
        )
    )
    if plan is None:
        raise ReuseReportError("plan not found")

    items = list(
        (
            await session.scalars(
                select(NarrativeMemoryRebuildItem).where(
                    NarrativeMemoryRebuildItem.plan_id == plan_id
                )
            )
        ).all()
    )
    if not items:
        raise ReuseReportError("plan has no items")

    decision_counts = Counter(i.decision for i in items)
    by_kind_rebuilt: dict[str, int] = defaultdict(int)
    by_kind_carried: dict[str, int] = defaultdict(int)
    by_kind_stale: dict[str, int] = defaultdict(int)
    dirty_chapters: list[int] = []
    for item in items:
        if item.decision == RebuildDecision.DIRTY.value:
            by_kind_rebuilt[item.asset_kind] += 1
            if item.chapter_start is not None:
                dirty_chapters.append(int(item.chapter_start))
        elif item.decision == RebuildDecision.CARRIED.value:
            by_kind_carried[item.asset_kind] += 1
        elif item.decision == RebuildDecision.STALE_BLOCKED.value:
            by_kind_stale[item.asset_kind] += 1

    carried_item_count = sum(
        1 for i in items if i.decision == RebuildDecision.CARRIED.value
    )

    parent_manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == plan.parent_version_id,
        )
    )
    target_manifest = await session.scalar(
        select(NarrativeMemoryManifest).where(
            NarrativeMemoryManifest.owner_id == owner_id,
            NarrativeMemoryManifest.novel_id == novel_id,
            NarrativeMemoryManifest.version_id == plan.target_version_id,
        )
    )

    run = await session.scalar(
        select(NarrativeMemoryBuildRun).where(
            NarrativeMemoryBuildRun.owner_id == owner_id,
            NarrativeMemoryBuildRun.novel_id == novel_id,
            NarrativeMemoryBuildRun.version_id == plan.target_version_id,
        )
    )
    stages: list[NarrativeMemoryBuildStage] = []
    attempts: list[NarrativeMemoryBuildModelCallAttempt] = []
    reservations: list[NarrativeMemoryBuildBudgetReservation] = []
    ledger = None
    if run is not None:
        stages = list(
            (
                await session.scalars(
                    select(NarrativeMemoryBuildStage).where(
                        NarrativeMemoryBuildStage.run_id == run.id
                    )
                )
            ).all()
        )
        attempts = list(
            (
                await session.scalars(
                    select(NarrativeMemoryBuildModelCallAttempt).where(
                        NarrativeMemoryBuildModelCallAttempt.run_id == run.id
                    )
                )
            ).all()
        )
        ledger = await session.scalar(
            select(NarrativeMemoryBuildBudgetLedger).where(
                NarrativeMemoryBuildBudgetLedger.run_id == run.id
            )
        )
        if ledger is not None:
            reservations = list(
                (
                    await session.scalars(
                        select(NarrativeMemoryBuildBudgetReservation).where(
                            NarrativeMemoryBuildBudgetReservation.ledger_id
                            == ledger.id
                        )
                    )
                ).all()
            )
        else:
            reservations = []

    # Carried items must not have stages
    carried_stage_keys = {
        i.stage_key
        for i in items
        if i.decision == RebuildDecision.CARRIED.value and i.stage_key
    }
    for stage in stages:
        if stage.stage_key in carried_stage_keys:
            raise ReuseReportError(
                f"carried asset has Phase 14 stage row: {stage.stage_key}"
            )

    transport_calls = sum(1 for a in attempts if a.status == "succeeded")
    cache_hits = sum(1 for a in attempts if a.status == "cache_hit")
    input_tokens = sum(int((a.usage or {}).get("input_tokens") or 0) for a in attempts)
    output_tokens = sum(int((a.usage or {}).get("output_tokens") or 0) for a in attempts)
    if ledger is not None:
        cost = Decimal(str(ledger.settled_cost_usd or 0))
    else:
        cost = sum((Decimal(str(a.cost_usd or 0)) for a in attempts), Decimal("0"))

    observed_actual = {
        "label": "observed_actual",
        "calls": transport_calls,
        "cache_hits": cache_hits,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": str(cost),
        "attempt_rows": len(attempts),
        "dirty_stage_rows": len(stages),
        "reservation_rows": len(reservations),
    }

    # Full rebuild upper bound: all planned chapter/arc/global stages
    if full_rebuild_stage_count is None:
        # Derive from boundary plan chapter coverage + parents + global
        boundary = plan.boundary_plan or {}
        chapter_max = int(boundary.get("chapter_max") or 0)
        chapter_min = int(boundary.get("chapter_min") or 1)
        parent_keys = set((boundary.get("parent_to_global") or {}).keys())
        n_chapters = max(0, chapter_max - chapter_min + 1) if chapter_max else 0
        # chapter stages + arc/volume parents + global (+ optional plan)
        full_calls = n_chapters + len(parent_keys) + 1
    else:
        full_calls = full_rebuild_stage_count

    if price_input_per_million is None or price_output_per_million is None:
        # Unknown price blocks cost upper bound (still report call upper bound)
        full_input = reservation_envelope_input * full_calls
        full_output = reservation_envelope_output * full_calls
        full_cost = Decimal("0")
        price_known = False
    else:
        price_known = True
        full_input = reservation_envelope_input * full_calls
        full_output = reservation_envelope_output * full_calls
        full_cost = (
            Decimal(price_input_per_million) * Decimal(full_input) / Decimal(1_000_000)
            + Decimal(price_output_per_million)
            * Decimal(full_output)
            / Decimal(1_000_000)
        )

    full_rebuild_upper_bound = {
        "label": "full_rebuild_upper_bound",
        "calls": full_calls,
        "input_tokens": full_input,
        "output_tokens": full_output,
        "cost_usd": str(full_cost),
        "price_known": price_known,
        "reservation_envelope_input_per_stage": reservation_envelope_input,
        "reservation_envelope_output_per_stage": reservation_envelope_output,
    }

    avoided = compute_avoided_upper_bound(
        full_calls=full_calls,
        full_input=full_input,
        full_output=full_output,
        full_cost=full_cost,
        observed_calls=transport_calls,
        observed_input=input_tokens,
        observed_output=output_tokens,
        observed_cost=cost,
    )
    avoided["label"] = "avoided_upper_bound"

    if any(
        v < 0
        for v in (
            avoided["calls"],
            avoided["input_tokens"],
            avoided["output_tokens"],
        )
    ):
        raise ReuseReportError("negative avoided metrics")

    carry_reuse = {
        "label": "carry_reuse",
        "carried_item_count": carried_item_count,
        "by_kind": dict(sorted(by_kind_carried.items())),
        "note": "counted from rebuild items decision=carried; no Phase 14 stages",
    }
    cache_reuse = {
        "label": "exact_cache_reuse",
        "cache_hits": cache_hits,
        "note": "from Phase 14 model call attempts status=cache_hit only",
    }

    dirty_paths = sorted(
        {
            f"{i.asset_key}:{'|'.join(i.direct_reasons or [])}"
            for i in items
            if i.decision == RebuildDecision.DIRTY.value
        }
    )

    body = {
        "plan_id": plan.id,
        "plan_checksum": plan.plan_checksum,
        "parent_version_id": plan.parent_version_id,
        "target_version_id": plan.target_version_id,
        "parent_manifest_checksum": (
            parent_manifest.manifest_checksum if parent_manifest else None
        ),
        "target_manifest_checksum": (
            target_manifest.manifest_checksum if target_manifest else None
        ),
        "rebuilt_counts": {
            "total": decision_counts.get(RebuildDecision.DIRTY.value, 0),
            "by_kind": dict(sorted(by_kind_rebuilt.items())),
        },
        "carried_counts": {
            "total": carried_item_count,
            "by_kind": dict(sorted(by_kind_carried.items())),
        },
        "stale_counts": {
            "total": decision_counts.get(RebuildDecision.STALE_BLOCKED.value, 0),
            "by_kind": dict(sorted(by_kind_stale.items())),
        },
        "dirty_ranges": coalesce_ranges(dirty_chapters),
        "dirty_dependency_paths": dirty_paths,
        "observed_actual": observed_actual,
        "full_rebuild_upper_bound": full_rebuild_upper_bound,
        "avoided_upper_bound": avoided,
        "cache_reuse": cache_reuse,
        "carry_reuse": carry_reuse,
        "formula_inputs": {
            "full_rebuild_stage_count": full_calls,
            "reservation_envelope_input": reservation_envelope_input,
            "reservation_envelope_output": reservation_envelope_output,
            "price_input_per_million": price_input_per_million,
            "price_output_per_million": price_output_per_million,
            "price_known": price_known,
        },
    }
    body["report_checksum"] = stable_checksum(
        {k: v for k, v in body.items() if k != "report_checksum"}
    )
    return body


async def persist_reuse_report(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    plan_id: int,
    body: dict[str, Any] | None = None,
    **recompute_kwargs: Any,
) -> NarrativeMemoryReuseReport:
    if body is None:
        body = await recompute_reuse_report(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            plan_id=plan_id,
            **recompute_kwargs,
        )
    plan = await session.scalar(
        select(NarrativeMemoryRebuildPlan).where(
            NarrativeMemoryRebuildPlan.id == plan_id,
            NarrativeMemoryRebuildPlan.owner_id == owner_id,
            NarrativeMemoryRebuildPlan.novel_id == novel_id,
        )
    )
    if plan is None:
        raise ReuseReportError("plan not found")

    existing = await session.scalar(
        select(NarrativeMemoryReuseReport).where(
            NarrativeMemoryReuseReport.owner_id == owner_id,
            NarrativeMemoryReuseReport.novel_id == novel_id,
            NarrativeMemoryReuseReport.plan_id == plan_id,
            NarrativeMemoryReuseReport.report_checksum == body["report_checksum"],
        )
    )
    if existing is not None:
        return existing

    row = NarrativeMemoryReuseReport(
        owner_id=owner_id,
        novel_id=novel_id,
        plan_id=plan_id,
        parent_version_id=plan.parent_version_id,
        target_version_id=plan.target_version_id,
        plan_checksum=plan.plan_checksum,
        parent_manifest_checksum=body.get("parent_manifest_checksum"),
        target_manifest_checksum=body.get("target_manifest_checksum"),
        rebuilt_counts=body["rebuilt_counts"],
        carried_counts=body["carried_counts"],
        stale_counts=body["stale_counts"],
        dirty_ranges=body["dirty_ranges"],
        observed_actual=body["observed_actual"],
        full_rebuild_upper_bound=body["full_rebuild_upper_bound"],
        avoided_upper_bound=body["avoided_upper_bound"],
        cache_reuse=body["cache_reuse"],
        carry_reuse=body["carry_reuse"],
        formula_inputs=body["formula_inputs"],
        report_checksum=body["report_checksum"],
        body=body,
    )
    session.add(row)
    await session.flush()
    return row
