#!/usr/bin/env python3
"""Explicitly requeue failed candidate-builder chapter stages.

Dry-run is the default. ``--apply`` only changes failed chapter stages in the
explicit owner/novel/version scope to ``pending``; it never changes candidate
nodes, active pointers, or Reader Chat state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _run(args: argparse.Namespace) -> int:
    from app.core.database import async_session_factory
    from app.models.narrative_memory_builder import (
        NarrativeMemoryBuildRun,
        NarrativeMemoryBuildStage,
    )

    async with async_session_factory() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun)
            .where(
                NarrativeMemoryBuildRun.owner_id == args.owner_id,
                NarrativeMemoryBuildRun.novel_id == args.novel_id,
                NarrativeMemoryBuildRun.version_id == args.version_id,
            )
            .with_for_update()
        )
        if run is None:
            print(json.dumps({"error": "run_not_found"}, ensure_ascii=False))
            return 2
        if run.cancel_requested:
            print(json.dumps({"error": "run_cancel_requested"}, ensure_ascii=False))
            return 3
        now = datetime.now(timezone.utc)
        if run.lease_expires_at is not None and run.lease_expires_at > now:
            print(json.dumps({"error": "active_worker_lease"}, ensure_ascii=False))
            return 4

        stages = list(
            (
                await session.scalars(
                    select(NarrativeMemoryBuildStage)
                    .where(
                        NarrativeMemoryBuildStage.run_id == run.id,
                        NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                        NarrativeMemoryBuildStage.status == "failed",
                    )
                    .order_by(NarrativeMemoryBuildStage.id.asc())
                    .limit(args.limit)
                    .with_for_update()
                )
            ).all()
        )
        reason = f"operator_requeue:{args.reason}"[:160]
        if args.apply:
            for stage in stages:
                stage.status = "pending"
                stage.status_reason = reason
            if stages:
                run.status = "partial"
                run.status_reason = "operator_requeue"
                run.lease_id = None
                run.lease_expires_at = None
                run.heartbeat_at = None
            await session.commit()

        print(
            json.dumps(
                {
                    "command": "requeue-failed",
                    "applied": bool(args.apply),
                    "owner_id": args.owner_id,
                    "novel_id": args.novel_id,
                    "version_id": args.version_id,
                    "run_id": run.id,
                    "count": len(stages),
                    "stage_keys": [stage.stage_key for stage in stages],
                    "reason": reason,
                },
                ensure_ascii=False,
            )
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="_nm_requeue_failed")
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--reason", default="provider-recovery")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
