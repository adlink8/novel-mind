#!/usr/bin/env python3
"""Fixed explicit-version narrative-memory rebuild CLI (Phase 16).

Commands: plan | status | execute | cancel | resume | report

Requires owner, novel, parent version and target version. No promote, current,
default, all-books, embedding or Reader Chat options.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Ensure backend package root is importable when run as a script.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


FORBIDDEN_OPTIONS = frozenset(
    {
        "promote",
        "rollback",
        "active",
        "current",
        "default",
        "all-books",
        "embedding",
        "reader-chat",
        "chat",
    }
)


def _json_print(payload: Any, *, exit_code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return exit_code


async def _session_factory():
    from app.core.database import async_session_factory

    return async_session_factory


async def cmd_plan(args: argparse.Namespace) -> int:
    from app.services.narrative_memory.change_oracle import (
        compute_rebuild_plan,
        persist_rebuild_plan,
    )
    from app.services.narrative_memory.rebuild_contracts import OraclePolicy

    factory = await _session_factory()
    async with factory() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            parent_version_id=args.parent_version_id,
            target_version_id=args.target_version_id,
            target_hierarchy_build_id=args.hierarchy_build_id,
            eligibility_report_checksum=args.eligibility_checksum,
            oracle_policy=OraclePolicy(),
        )
        row = await persist_rebuild_plan(session, plan)
        await session.commit()
        return _json_print(
            {
                "command": "plan",
                "plan_id": row.id,
                "plan_checksum": row.plan_checksum,
                "graph_checksum": row.graph_checksum,
                "dirty_count": plan.change_summary.get("dirty_count"),
                "carried_count": plan.change_summary.get("carried_count"),
                "provider_calls": 0,
            }
        )


async def cmd_status(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
    from app.models.narrative_memory_rebuild import (
        NarrativeMemoryRebuildItem,
        NarrativeMemoryRebuildPlan,
    )

    factory = await _session_factory()
    async with factory() as session:
        plan = await session.scalar(
            select(NarrativeMemoryRebuildPlan).where(
                NarrativeMemoryRebuildPlan.owner_id == args.owner_id,
                NarrativeMemoryRebuildPlan.novel_id == args.novel_id,
                NarrativeMemoryRebuildPlan.parent_version_id == args.parent_version_id,
                NarrativeMemoryRebuildPlan.target_version_id == args.target_version_id,
            )
        )
        if plan is None:
            return _json_print({"command": "status", "error": "plan_not_found"}, exit_code=2)
        items = list(
            (
                await session.scalars(
                    select(NarrativeMemoryRebuildItem).where(
                        NarrativeMemoryRebuildItem.plan_id == plan.id
                    )
                )
            ).all()
        )
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == args.owner_id,
                NarrativeMemoryBuildRun.novel_id == args.novel_id,
                NarrativeMemoryBuildRun.version_id == args.target_version_id,
            )
        )
        return _json_print(
            {
                "command": "status",
                "plan_id": plan.id,
                "plan_checksum": plan.plan_checksum,
                "item_count": len(items),
                "decisions": {
                    d: sum(1 for i in items if i.decision == d)
                    for d in ("dirty", "carried", "stale_blocked", "not_applicable")
                },
                "run_status": run.status if run else None,
                "run_id": run.id if run else None,
            }
        )


async def cmd_execute(args: argparse.Namespace) -> int:
    """Carry + materialize dirty stages. Provider calls only via Phase 14 worker."""

    from sqlalchemy import select

    from app.models.narrative_memory_rebuild import NarrativeMemoryRebuildPlan
    from app.services.narrative_memory.builder_contracts import (
        BudgetPolicy,
        RunPolicy,
        StageKind,
    )
    from app.services.narrative_memory.contracts import ModelLineage
    from app.services.narrative_memory.rebuild_executor import (
        materialize_carry_and_dirty_stages,
    )

    factory = await _session_factory()
    async with factory() as session:
        plan = await session.scalar(
            select(NarrativeMemoryRebuildPlan).where(
                NarrativeMemoryRebuildPlan.owner_id == args.owner_id,
                NarrativeMemoryRebuildPlan.novel_id == args.novel_id,
                NarrativeMemoryRebuildPlan.parent_version_id == args.parent_version_id,
                NarrativeMemoryRebuildPlan.target_version_id == args.target_version_id,
            )
        )
        if plan is None:
            return _json_print({"command": "execute", "error": "plan_not_found"}, exit_code=2)
        hex64 = "a" * 64
        policy = RunPolicy(
            policy_version="builder-policy.v1",
            stage_order=(
                StageKind.CHAPTER_STATE,
                StageKind.ARC_VOLUME_PLAN,
                StageKind.ARC_VOLUME_AGGREGATE,
                StageKind.GLOBAL_AGGREGATE,
            ),
            max_schema_repairs=1,
            chapter_concurrency=1,
            budget=BudgetPolicy(
                max_calls=100,
                max_input_tokens=1_000_000,
                max_output_tokens=1_000_000,
                max_cost_usd="100.0",
            ),
            prompt_hash=hex64,
            schema_hash=hex64,
            model_lineage=ModelLineage(
                provider="test", model="m", deployment="fixed", revision="1"
            ),
            decoding_hash=hex64,
            config_hash=hex64,
            policy_hash=hex64,
        )
        carry, mask, run_id = await materialize_carry_and_dirty_stages(
            session,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            plan_id=plan.id,
            run_policy=policy,
            expected_plan_checksum=plan.plan_checksum,
        )
        await session.commit()
        return _json_print(
            {
                "command": "execute",
                "plan_id": plan.id,
                "run_id": run_id,
                "carried_nodes": list(carry.carried_node_keys),
                "dirty_stage_keys": list(mask.dirty_stage_keys),
                "provider_calls_in_oracle_or_carry": 0,
            }
        )


async def cmd_cancel(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models.narrative_memory_builder import NarrativeMemoryBuildRun

    factory = await _session_factory()
    async with factory() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == args.owner_id,
                NarrativeMemoryBuildRun.novel_id == args.novel_id,
                NarrativeMemoryBuildRun.version_id == args.target_version_id,
            )
        )
        if run is None:
            return _json_print({"command": "cancel", "error": "run_not_found"}, exit_code=2)
        run.cancel_requested = True
        await session.commit()
        return _json_print({"command": "cancel", "run_id": run.id, "cancel_requested": True})


async def cmd_resume(args: argparse.Namespace) -> int:
    return await cmd_execute(args)


async def cmd_report(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.models.narrative_memory_rebuild import NarrativeMemoryRebuildPlan
    from app.services.narrative_memory.reuse_report import (
        persist_reuse_report,
        recompute_reuse_report,
    )

    factory = await _session_factory()
    async with factory() as session:
        plan = await session.scalar(
            select(NarrativeMemoryRebuildPlan).where(
                NarrativeMemoryRebuildPlan.owner_id == args.owner_id,
                NarrativeMemoryRebuildPlan.novel_id == args.novel_id,
                NarrativeMemoryRebuildPlan.parent_version_id == args.parent_version_id,
                NarrativeMemoryRebuildPlan.target_version_id == args.target_version_id,
            )
        )
        if plan is None:
            return _json_print({"command": "report", "error": "plan_not_found"}, exit_code=2)
        body = await recompute_reuse_report(
            session,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            plan_id=plan.id,
            reservation_envelope_input=args.envelope_input,
            reservation_envelope_output=args.envelope_output,
            price_input_per_million=args.price_input,
            price_output_per_million=args.price_output,
        )
        row = await persist_reuse_report(
            session,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            plan_id=plan.id,
            body=body,
        )
        await session.commit()
        return _json_print(
            {
                "command": "report",
                "report_id": row.id,
                "report_checksum": row.report_checksum,
                "observed_actual": body["observed_actual"],
                "full_rebuild_upper_bound": body["full_rebuild_upper_bound"],
                "avoided_upper_bound": body["avoided_upper_bound"],
                "carry_reuse": body["carry_reuse"],
                "cache_reuse": body["cache_reuse"],
            }
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_narrative_memory_rebuild",
        description="Candidate-only local rebuild CLI (no promotion/pointer)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_scope(p: argparse.ArgumentParser) -> None:
        p.add_argument("--owner-id", type=int, required=True)
        p.add_argument("--novel-id", type=int, required=True)
        p.add_argument("--parent-version-id", type=int, required=True)
        p.add_argument("--target-version-id", type=int, required=True)

    p_plan = sub.add_parser("plan", help="Provider-free change oracle")
    add_scope(p_plan)
    p_plan.add_argument("--hierarchy-build-id", required=True)
    p_plan.add_argument("--eligibility-checksum", required=True)

    p_status = sub.add_parser("status")
    add_scope(p_status)

    p_exec = sub.add_parser("execute", help="Carry + dirty stage materialize")
    add_scope(p_exec)

    p_cancel = sub.add_parser("cancel")
    add_scope(p_cancel)

    p_resume = sub.add_parser("resume")
    add_scope(p_resume)

    p_report = sub.add_parser("report")
    add_scope(p_report)
    p_report.add_argument("--envelope-input", type=int, default=0)
    p_report.add_argument("--envelope-output", type=int, default=0)
    p_report.add_argument("--price-input", default=None)
    p_report.add_argument("--price-output", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lowered = " ".join(argv).lower()
    for forbidden in FORBIDDEN_OPTIONS:
        if f"--{forbidden}" in lowered or f" {forbidden}" in f" {lowered} ":
            print(
                json.dumps(
                    {
                        "error": "forbidden_option",
                        "option": forbidden,
                        "message": "Phase 16 CLI rejects promote/current/embedding/chat",
                    }
                ),
                file=sys.stderr,
            )
            return 3

    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "plan": cmd_plan,
        "status": cmd_status,
        "execute": cmd_execute,
        "cancel": cmd_cancel,
        "resume": cmd_resume,
        "report": cmd_report,
    }
    return asyncio.run(handlers[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
