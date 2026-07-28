#!/usr/bin/env python3
"""Ops: invalidate novel-scoped clue judge exact-cache, then re-run worker.

Does NOT promote narrative memory. Only touches clue runs for the given novel
and ClueModelCallAttempt rows belonging to that novel's runs.

Cache path (worker._judge_and_persist):
  load_exact_cache(cache_key) where status==succeeded and usage.validated_output
  is a dict. There is no force=true flag; force re-call by stripping
  validated_output (or status) on prior succeeded attempts for this novel.

Usage:
  python scripts/_clue_live_rejudge.py --novel-id 91 --owner-id 2 --limit 3 --full
  python scripts/_clue_live_rejudge.py --novel-id 91 --owner-id 2 --limit 0 --full
      # limit 0 = invalidate all novel succeeded caches
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select

from app.core.database import async_session_factory
from app.models.chunk_build import ChunkActivePointer
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueModelCallAttempt,
    MachineClue,
)
from app.services.clues.budget import BudgetExceeded, BudgetGate, UnknownPricing
from app.services.clues.worker import (
    ClueCancellationRequested,
    DependencyPaused,
    _build_candidates,
    _claim_run,
    _finish_run,
    _judge_and_persist,
    _prepare_run,
    _raise_if_cancel_requested,
    _update_progress,
    _validate_and_promote,
    dispatch_clue_run,
    production_runtime,
)


async def inspect(novel_id: int) -> None:
    async with async_session_factory() as s:
        ptr = await s.scalar(
            select(ClueActivePointer).where(ClueActivePointer.novel_id == novel_id)
        )
        hier = await s.scalar(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id)
        )
        print(
            "active_clue_version",
            ptr.version_id if ptr else None,
            "revision",
            getattr(ptr, "revision", None) if ptr else None,
        )
        print(
            "active_hierarchy",
            getattr(hier, "build_id", None) if hier else None,
        )
        for vid in filter(None, [getattr(ptr, "version_id", None), 21, 22]):
            v = await s.get(ClueAnalysisVersion, int(vid))
            if v:
                print(
                    f"version {v.id} status={v.status} hier={v.hierarchy_build_id} "
                    f"tl={v.timeline_version_id}"
                )
        runs = (
            await s.scalars(
                select(ClueAnalysisRun)
                .where(ClueAnalysisRun.novel_id == novel_id)
                .order_by(ClueAnalysisRun.id.desc())
                .limit(5)
            )
        ).all()
        print("recent_runs", [(r.id, r.status, r.version_id, r.active_key) for r in runs])
        run_ids = [
            int(x)
            for x in (
                await s.scalars(
                    select(ClueAnalysisRun.id).where(ClueAnalysisRun.novel_id == novel_id)
                )
            ).all()
        ]
        if run_ids:
            rows = (
                await s.execute(
                    select(ClueModelCallAttempt.status, func.count())
                    .where(ClueModelCallAttempt.run_id.in_(run_ids))
                    .group_by(ClueModelCallAttempt.status)
                )
            ).all()
            print("attempt_status", dict(rows))
            n_vo = 0
            succ = (
                await s.scalars(
                    select(ClueModelCallAttempt).where(
                        ClueModelCallAttempt.run_id.in_(run_ids),
                        ClueModelCallAttempt.status == "succeeded",
                    )
                )
            ).all()
            for a in succ:
                if isinstance((a.usage or {}).get("validated_output"), dict):
                    n_vo += 1
            print("succeeded_with_validated_output", n_vo, "of", len(succ))


async def invalidate_cache(*, novel_id: int, limit: int) -> list[int]:
    """Strip validated_output from succeeded attempts for this novel only.

    limit: max attempts to invalidate; 0 means all.
    Returns attempt ids touched.
    """
    async with async_session_factory() as s:
        async with s.begin():
            run_ids = list(
                (
                    await s.scalars(
                        select(ClueAnalysisRun.id).where(
                            ClueAnalysisRun.novel_id == novel_id
                        )
                    )
                ).all()
            )
            if not run_ids:
                print("no runs for novel; nothing to invalidate")
                return []
            q = (
                select(ClueModelCallAttempt)
                .where(
                    ClueModelCallAttempt.run_id.in_(run_ids),
                    ClueModelCallAttempt.status == "succeeded",
                )
                .order_by(ClueModelCallAttempt.id.asc())
            )
            attempts = list((await s.scalars(q)).all())
            # Only those with cache payload
            with_vo = [
                a
                for a in attempts
                if isinstance((a.usage or {}).get("validated_output"), dict)
            ]
            if limit and limit > 0:
                with_vo = with_vo[:limit]
            touched: list[int] = []
            for a in with_vo:
                usage = dict(a.usage or {})
                usage.pop("validated_output", None)
                usage["ops_cache_invalidated"] = True
                usage["ops_reason"] = "live_rejudge_novel_91"
                a.usage = usage
                # Keep status=succeeded for audit, but null response_hash so
                # load_exact_cache filter (response_hash IS NOT NULL) also misses.
                a.response_hash = None
                a.error_code = "ops_cache_invalidated"
                touched.append(int(a.id))
            print(
                "invalidated",
                len(touched),
                "attempt_ids",
                touched[:10],
                ("..." if len(touched) > 10 else ""),
            )
            return touched


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


async def smoke(run_id: int, *, max_candidates: int | None = None) -> int:
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
    if max_candidates and max_candidates > 0:
        drafts = drafts[:max_candidates]
    print("candidates", len(drafts), "max_cap", max_candidates)
    async with runtime.sessions.begin() as session:
        r = await session.get(ClueAnalysisRun, run_id, with_for_update=True)
        assert r is not None
        r.status = "pending"
        r.status_reason = None
        r.lease_id = None
        r.lease_expires_at = None
        r.progress = {
            "stage": "candidate_smoke_ok",
            "total_candidates": len(drafts),
        }
    return len(drafts)


async def dispatch_subset(run_id: int, *, max_candidates: int) -> None:
    """Like run_clue_worker but caps candidates for low-cost live probe."""
    runtime = production_runtime()
    lease_id = uuid.uuid4().hex
    if not await _claim_run(runtime.sessions, run_id, lease_id):
        print("claim_failed")
        return
    try:
        run, version, build = await _prepare_run(runtime, run_id)
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        budget = BudgetGate(runtime.budget_policy)
        # Prefer slice after default build to avoid deep import churn.
        # Note: package_hash/candidate_id still match full-path candidates.
        drafts = await _build_candidates(runtime, run, version, build)
        drafts = drafts[: max(1, int(max_candidates))]
        print("subset_judging", len(drafts), "version", version.id)
        await _update_progress(
            runtime.sessions,
            run.id,
            completed=0,
            total=len(drafts),
            stage="judging",
        )
        for index, draft in enumerate(drafts, start=1):
            await _raise_if_cancel_requested(runtime.sessions, run_id)
            await _judge_and_persist(runtime, budget, run, version, draft)
            await _update_progress(
                runtime.sessions,
                run.id,
                completed=index,
                total=len(drafts),
                stage="judging",
            )
            print(
                "judged",
                index,
                "/",
                len(drafts),
                draft.candidate_id,
            )
        await _raise_if_cancel_requested(runtime.sessions, run_id)
        await _validate_and_promote(runtime.sessions, run, version)
    except ClueCancellationRequested:
        await _finish_run(runtime.sessions, run_id, "cancelled", "cancel requested")
        return
    except DependencyPaused as exc:
        await _finish_run(
            runtime.sessions, run_id, "paused_dependency", str(exc)[:160]
        )
        return
    except (BudgetExceeded, UnknownPricing) as exc:
        await _finish_run(runtime.sessions, run_id, "paused_budget", str(exc)[:160])
        return
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:160]
        await _finish_run(runtime.sessions, run_id, "failed", detail)
        raise


async def report(novel_id: int, run_id: int) -> None:
    async with async_session_factory() as s:
        run = await s.get(ClueAnalysisRun, run_id)
        print(
            "final_run",
            run.id if run else None,
            "status",
            getattr(run, "status", None),
            "reason",
            (getattr(run, "status_reason", None) or "")[:240],
            "progress",
            getattr(run, "progress", None),
            "version_id",
            getattr(run, "version_id", None),
        )
        vid = getattr(run, "version_id", None)
        if run is not None:
            rows = (
                await s.execute(
                    select(ClueModelCallAttempt.status, func.count())
                    .where(ClueModelCallAttempt.run_id == run.id)
                    .group_by(ClueModelCallAttempt.status)
                )
            ).all()
            print("this_run_attempts", dict(rows))
            failed = (
                await s.scalars(
                    select(ClueModelCallAttempt)
                    .where(
                        ClueModelCallAttempt.run_id == run.id,
                        ClueModelCallAttempt.status.in_(
                            ["failed", "outcome_unknown"]
                        ),
                    )
                    .limit(5)
                )
            ).all()
            for a in failed:
                print(
                    "  fail",
                    a.id,
                    a.status,
                    a.error_code,
                    (a.usage or {}),
                )
        if vid:
            n_clues = await s.scalar(
                select(func.count())
                .select_from(MachineClue)
                .where(MachineClue.version_id == int(vid))
            )
            roles = (
                await s.execute(
                    select(ClueEvidenceRef.role, func.count())
                    .where(ClueEvidenceRef.version_id == int(vid))
                    .group_by(ClueEvidenceRef.role)
                )
            ).all()
            life = (
                await s.execute(
                    select(ClueLifecycleEvent.to_status, func.count())
                    .where(ClueLifecycleEvent.version_id == int(vid))
                    .group_by(ClueLifecycleEvent.to_status)
                )
            ).all()
            print("machine_clues", n_clues, "roles", dict(roles), "lifecycle", dict(life))
            samples = (
                await s.scalars(
                    select(MachineClue)
                    .where(MachineClue.version_id == int(vid))
                    .order_by(MachineClue.id.asc())
                    .limit(8)
                )
            ).all()
            for c in samples:
                print(
                    "  clue",
                    c.id,
                    "cue_ch",
                    c.first_cue_chapter,
                    "conf",
                    float(c.confidence),
                    "pub",
                    c.publication_status,
                    "title",
                    (c.title or "")[:60],
                    "sum",
                    (c.summary or "")[:80].replace("\n", " "),
                )
        ptr = await s.scalar(
            select(ClueActivePointer).where(ClueActivePointer.novel_id == novel_id)
        )
        print("active_pointer_version", ptr.version_id if ptr else None)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-id", type=int, default=91)
    parser.add_argument("--owner-id", type=int, default=2)
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="max succeeded caches to invalidate (0=all for this novel)",
    )
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--invalidate-only", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--skip-invalidate",
        action="store_true",
        help="re-run without touching cache (probe natural miss after hierarchy change)",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=3,
        help="cap candidates for subset live probe (0 = production full 32 path)",
    )
    args = parser.parse_args()

    await inspect(args.novel_id)
    if args.inspect_only:
        return 0

    if not args.skip_invalidate:
        # When hierarchy build_id changed, keys already miss; invalidation is
        # belt-and-suspenders for same-hierarchy re-runs.
        inv_limit = args.limit if args.limit > 0 else 0
        await invalidate_cache(novel_id=args.novel_id, limit=inv_limit)
    if args.invalidate_only:
        return 0

    run_id = await prepare_run(args.owner_id, args.novel_id)
    print("run_id", run_id)
    cap = args.max_candidates if args.max_candidates > 0 else None
    n = await smoke(run_id, max_candidates=cap)
    print("SMOKE_OK candidates", n)
    if args.smoke_only or not args.full:
        if not args.full:
            print("skip full dispatch (pass --full)")
        return 0

    try:
        if args.max_candidates and args.max_candidates > 0:
            print(
                f"dispatching SUBSET live judge max_candidates={args.max_candidates}..."
            )
            await dispatch_subset(run_id, max_candidates=args.max_candidates)
        else:
            print("dispatching FULL clue worker (live path when cache miss)...")
            await dispatch_clue_run(run_id)
        print("dispatch_finished")
    except Exception as exc:
        print("dispatch_error", type(exc).__name__, exc)
    await report(args.novel_id, run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
