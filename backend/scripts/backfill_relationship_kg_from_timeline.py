#!/usr/bin/env python3
"""Backfill Characters + KG accepted judgments + relationship observations.

Usage:
  python scripts/backfill_relationship_kg_from_timeline.py --novel-id 91 --write
  python scripts/backfill_relationship_kg_from_timeline.py --novel-id 91 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory
from app.services.relationships.timeline_kg_backfill import timeline_kg_backfill_service


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Seed Phase 04 KG + Phase 09 observations from timeline."
    )
    p.add_argument("--novel-id", type=int, required=True)
    p.add_argument("--owner-id", type=int, default=None)
    p.add_argument("--max-characters", type=int, default=40)
    p.add_argument("--max-judgments", type=int, default=60)
    p.add_argument("--min-cooccur", type=int, default=3)
    p.add_argument(
        "--skip-relationship-worker",
        action="store_true",
        help="Only write KG/characters; do not run Phase 09 worker",
    )
    p.add_argument(
        "--llm-judge",
        action="store_true",
        help="Let Phase 09 worker call LLM (default: deterministic seed)",
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--write", action="store_true")
    return p


async def _main() -> int:
    args = _parser().parse_args()
    if not args.write and not args.dry_run:
        print("Specify --write or --dry-run", file=sys.stderr)
        return 2

    async with async_session_factory() as db:
        if args.dry_run:
            # Read-only probe: still runs logic but rolls back.
            try:
                result = await timeline_kg_backfill_service.backfill(
                    db,
                    novel_id=args.novel_id,
                    owner_id=args.owner_id,
                    max_characters=args.max_characters,
                    max_judgments=args.max_judgments,
                    min_cooccur=args.min_cooccur,
                    run_relationship_worker=not args.skip_relationship_worker,
                    use_deterministic_rel_judge=not args.llm_judge,
                )
            finally:
                await db.rollback()
            payload = result.to_dict()
            payload["mode"] = "dry-run-rolled-back"
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        result = await timeline_kg_backfill_service.backfill(
            db,
            novel_id=args.novel_id,
            owner_id=args.owner_id,
            max_characters=args.max_characters,
            max_judgments=args.max_judgments,
            min_cooccur=args.min_cooccur,
            run_relationship_worker=not args.skip_relationship_worker,
            use_deterministic_rel_judge=not args.llm_judge,
        )
        await db.commit()
        payload = result.to_dict()
        payload["mode"] = "write"
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not result.errors or result.judgments_created > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
