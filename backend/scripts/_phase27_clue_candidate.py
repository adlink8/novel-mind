#!/usr/bin/env python3
"""Run complete-book clue judging as a validated candidate without promotion."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.clue import ClueAnalysisRun
from app.services.clues.worker import production_runtime, run_clue_worker


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner-id", type=int, required=True)
    p.add_argument("--novel-id", type=int, required=True)
    p.add_argument("--run-id", type=int, default=None)
    return p


async def _create_run(owner_id: int, novel_id: int) -> int:
    async with async_session_factory.begin() as db:
        run = ClueAnalysisRun(
            owner_id=owner_id,
            novel_id=novel_id,
            active_key="phase27",
            status="pending",
            checkpoint={},
            progress={},
        )
        db.add(run)
        await db.flush()
        return run.id


async def main() -> int:
    args = _parser().parse_args()
    run_id = args.run_id or await _create_run(args.owner_id, args.novel_id)
    runtime = production_runtime()
    runtime.promote_candidate = False
    await run_clue_worker(run_id, runtime=runtime)
    async with async_session_factory() as db:
        run = await db.scalar(
            select(ClueAnalysisRun).where(
                ClueAnalysisRun.id == run_id,
                ClueAnalysisRun.owner_id == args.owner_id,
                ClueAnalysisRun.novel_id == args.novel_id,
            )
        )
        if run is None:
            raise RuntimeError("clue candidate run disappeared")
        payload = {
            "status": run.status,
            "status_reason": run.status_reason,
            "run_id": run.id,
            "version_id": run.version_id,
            "progress": run.progress,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if run.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
