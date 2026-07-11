"""Rollback or restore an exact committed narrative promotion journal."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import async_session_factory  # noqa: E402
from app.services.knowledge_units.rollback import rollback_journal, restore_journal  # noqa: E402


async def _run(args) -> dict:
    if args.dry_run:
        return {"dry_run": True, "journal_id": args.journal_id, "action": "restore" if args.restore else "rollback"}
    async with async_session_factory() as db:
        pointer = await (restore_journal(db, journal_id=args.journal_id) if args.restore else rollback_journal(db, journal_id=args.journal_id))
        await db.commit()
        return {"build_id": pointer.build_id if pointer else None, "pointer_version": pointer.pointer_version if pointer else None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal-id", type=int, required=True)
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
