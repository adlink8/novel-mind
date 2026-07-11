"""Prepare an affected-subject narrative refresh; no-change is zero-write."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.incremental import (  # noqa: E402
    complete_refresh,
    execute_refresh,
    prepare_delta,
)


async def _run(args) -> dict:
    async with async_session_factory() as db:
        plan = await prepare_delta(
            db,
            owner_id=args.owner_id,
            novel_id=args.novel_id,
            domain_profile=args.domain,
            after_snapshot_id=args.snapshot_id,
        )
        report = (
            await execute_refresh(
                db,
                plan=plan,
                owner_id=args.owner_id,
                novel_id=args.novel_id,
                domain_profile=args.domain,
            )
            if args.dry_run
            else await complete_refresh(
                db,
                plan=plan,
                owner_id=args.owner_id,
                novel_id=args.novel_id,
                domain_profile=args.domain,
                approved_by=args.approved_by,
                evidence_secret=args.evidence_secret,
                fixture_path=args.fixture,
            )
        )
        if not args.dry_run:
            await db.commit()
        return {
            "status": report.status,
            "run_id": report.run_id,
            "added": list(plan.added),
            "changed": list(plan.changed),
            "removed": list(plan.removed),
            "affected_subjects": list(plan.affected_subjects),
            "writes": report.writes,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", type=int)
    parser.add_argument("--novel-id", type=int)
    parser.add_argument("--domain", choices=("fiction", "history"), default="fiction")
    parser.add_argument("--snapshot-id", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fixture")
    parser.add_argument("--approved-by")
    parser.add_argument(
        "--evidence-secret", default=os.environ.get("NARRATIVE_EVAL_SIGNING_SECRET", "")
    )
    args = parser.parse_args()
    if None in (args.owner_id, args.novel_id, args.snapshot_id):
        parser.error(
            "refresh requires --owner-id, --novel-id, and --snapshot-id even in dry-run"
        )
    if not args.dry_run and (
        not args.fixture or not args.approved_by or not args.evidence_secret
    ):
        parser.error(
            "write mode requires --fixture, --approved-by and NARRATIVE_EVAL_SIGNING_SECRET"
        )
    print(json.dumps(asyncio.run(_run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
