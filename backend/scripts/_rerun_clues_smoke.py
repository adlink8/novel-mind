#!/usr/bin/env python3
"""Reset active clue run for a novel, smoke candidate build, then full dispatch."""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.clue import (
    ClueAnalysisRun,
    ClueLifecycleEvent,
    ClueLink,
)
from app.services.clues.worker import (
    _build_candidates,
    _claim_run,
    _prepare_run,
    dispatch_clue_run,
    production_runtime,
)


async def prepare_run(owner_id: int, novel_id: int) -> int:
    async with async_session_factory() as s:
        async with s.begin():
            run = await s.scalar(
                select(ClueAnalysisRun)
                .where(
                    ClueAnalysisRun.owner_id == owner_id,
                    ClueAnalysisRun.novel_id == novel_id,
                    ClueAnalysisRun.active_key == "active",
                )
                .with_for_update()
            )
            if run is None:
                run = ClueAnalysisRun(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    active_key="active",
                    status="pending",
                    progress={},
                )
                s.add(run)
                await s.flush()
            else:
                run.status = "pending"
                run.status_reason = None
                run.cancel_requested = False
                run.lease_id = None
                run.lease_expires_at = None
                run.version_id = None
                run.checkpoint = {}
                run.progress = {}
            return int(run.id)


async def smoke_candidates(run_id: int) -> int:
    runtime = production_runtime()
    lease = uuid.uuid4().hex
    claimed = await _claim_run(runtime.sessions, run_id, lease)
    print("claimed", claimed)
    if not claimed:
        raise RuntimeError("could not claim run for smoke")
    run, version, build = await _prepare_run(runtime, run_id)
    print(
        "prepared",
        "version",
        getattr(version, "id", None),
        "build",
        getattr(build, "build_id", None),
    )
    drafts = await _build_candidates(runtime, run, version, build)
    print("candidates", len(drafts))
    for i, d in enumerate(drafts[:8]):
        later_chs = sorted({u.narrative_chapter_number for u in d.package.later_units})
        cue_chs = sorted({u.narrative_chapter_number for u in d.package.cue_units})
        span = (max(later_chs) - min(later_chs) + 1) if later_chs else 0
        print(
            f"  [{i}] cue={cue_chs} later_chapters={later_chs} "
            f"later_units={len(d.package.later_units)} chapter_span={span}"
        )
    # release claim for full dispatch
    async with runtime.sessions.begin() as session:
        r = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        assert r is not None
        r.status = "pending"
        r.status_reason = None
        r.lease_id = None
        r.lease_expires_at = None
        r.progress = {"stage": "candidate_smoke_ok", "total_candidates": len(drafts)}
    return len(drafts)


async def report(novel_id: int, run_id: int) -> None:
    async with async_session_factory() as s:
        run = await s.get(ClueAnalysisRun, run_id)
        print(
            "final_run",
            run.id if run else None,
            "status",
            getattr(run, "status", None),
            "reason",
            (getattr(run, "status_reason", None) or "")[:200],
            "progress",
            getattr(run, "progress", None),
            "version_id",
            getattr(run, "version_id", None),
        )
        links = await s.scalar(
            select(func.count()).select_from(ClueLink).where(ClueLink.novel_id == novel_id)
        )
        events = await s.scalar(
            select(func.count())
            .select_from(ClueLifecycleEvent)
            .where(ClueLifecycleEvent.novel_id == novel_id)
        )
        print("clue_links", links, "lifecycle_events", events)
        # sample links
        sample = list(
            (
                await s.scalars(
                    select(ClueLink)
                    .where(ClueLink.novel_id == novel_id)
                    .order_by(ClueLink.id.desc())
                    .limit(5)
                )
            ).all()
        )
        for link in sample:
            print(
                "  link",
                link.id,
                "state",
                getattr(link, "state", None) or getattr(link, "status", None),
                "title",
                (getattr(link, "title", None) or getattr(link, "summary", None) or "")[:80],
            )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-id", type=int, default=91)
    parser.add_argument("--owner-id", type=int, default=2)
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--full", action="store_true", help="run full worker with LLM")
    args = parser.parse_args()

    run_id = await prepare_run(args.owner_id, args.novel_id)
    print("run_id", run_id)
    n = await smoke_candidates(run_id)
    print("SMOKE_OK candidates", n)
    if args.smoke_only or not args.full:
        if not args.full:
            print("skip full dispatch (pass --full to run LLM worker)")
        return 0

    print("dispatching full clue worker...")
    try:
        await dispatch_clue_run(run_id)
        print("dispatch_finished")
    except Exception as exc:
        print("dispatch_error", type(exc).__name__, exc)
    await report(args.novel_id, run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
