"""Prepare or commit an exact narrative candidate promotion journal."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.promotion import narrative_promotion_service  # noqa: E402


async def _run(args) -> dict:
    if args.dry_run:
        return {"dry_run": True, "action": "prepare" if args.prepare else "commit", "candidate_checksum": args.checksum}
    async with async_session_factory() as db:
        if args.prepare:
            eval_report = json.loads(open(args.eval_report, encoding="utf-8").read())
            reconcile = json.loads(open(args.reconcile_report, encoding="utf-8").read())
            journal = await narrative_promotion_service.prepare(db, candidate_build_id=args.candidate, candidate_checksum=args.checksum, eval_report=eval_report, reconcile_report=reconcile, approved_by=args.approved_by)
            await db.commit()
            return {"journal_id": journal.id, "status": journal.status}
        pointer = await narrative_promotion_service.commit(db, journal_id=args.commit, candidate_checksum=args.checksum)
        await db.commit()
        return {"build_id": pointer.build_id, "pointer_version": pointer.pointer_version}


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--commit", type=int)
    parser.add_argument("--candidate", type=int)
    parser.add_argument("--checksum", required=True)
    parser.add_argument("--eval-report")
    parser.add_argument("--reconcile-report")
    parser.add_argument("--approved-by", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
