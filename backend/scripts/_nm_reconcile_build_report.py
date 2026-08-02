#!/usr/bin/env python3
"""Reconcile the append-only builder report from current candidate authority."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _run(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.narrative_memory_builder import (
        NarrativeMemoryBuildRun,
        NarrativeMemoryBuildStage,
    )
    from app.services.narrative_memory.builder_report import write_build_report
    from app.services.narrative_memory.manifests import compute_manifest_from_snapshot
    from app.services.narrative_memory.manifests import load_candidate_snapshot

    async with async_session_factory() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == args.owner_id,
                NarrativeMemoryBuildRun.novel_id == args.novel_id,
                NarrativeMemoryBuildRun.version_id == args.version_id,
            )
        )
        if run is None:
            print(json.dumps({"error": "run_not_found"}, ensure_ascii=False))
            return 2
        stages = list(
            (
                await session.scalars(
                    select(NarrativeMemoryBuildStage).where(
                        NarrativeMemoryBuildStage.run_id == run.id
                    )
                )
            ).all()
        )
        if run.status != "completed" or any(s.status != "completed" for s in stages):
            print(
                json.dumps(
                    {
                        "error": "candidate_not_complete",
                        "run_status": run.status,
                        "non_completed": [s.stage_key for s in stages if s.status != "completed"],
                    },
                    ensure_ascii=False,
                )
            )
            return 3
        snapshot = await load_candidate_snapshot(
            session,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            version_id=args.version_id,
        )
        manifest_checksum = compute_manifest_from_snapshot(snapshot).manifest_checksum
        if not args.apply:
            print(json.dumps({"applied": False, "manifest_checksum": manifest_checksum}, ensure_ascii=False))
            return 0
        report = await write_build_report(
            session,
            run_id=run.id,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            version_id=args.version_id,
            worker_artifact_checksum=manifest_checksum,
            database_manifest_checksum=manifest_checksum,
        )
        await session.commit()
        print(
            json.dumps(
                {
                    "applied": True,
                    "report_id": report.id,
                    "outcome": report.outcome,
                    "report_checksum": report.report_checksum,
                    "manifest_checksum": manifest_checksum,
                },
                ensure_ascii=False,
            )
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="_nm_reconcile_build_report")
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--novel-id", type=int, required=True)
    parser.add_argument("--version-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
