#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory
from app.models.clue import ClueAnalysisRun


async def main() -> None:
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='clue_model_call_attempts' ORDER BY 1"
            )
        )
        print("cols", [x[0] for x in r])

        r = await s.execute(
            text(
                "SELECT error_code, count(*) FROM clue_model_call_attempts "
                "GROUP BY 1 ORDER BY 2 DESC"
            )
        )
        print("errors", r.fetchall())

        r = await s.execute(
            text(
                "SELECT id, run_id, status, error_code, latency_ms, "
                "left(coalesce(response_hash,''), 20) "
                "FROM clue_model_call_attempts ORDER BY id DESC LIMIT 8"
            )
        )
        print("samples:")
        for row in r:
            print(row)

        run = await s.get(ClueAnalysisRun, 16)
        ck = run.checkpoint or {}
        classes: dict[str, int] = {}
        failed = 0
        for _k, v in ck.items():
            if not isinstance(v, dict):
                continue
            if v.get("status") == "judgment_failed":
                failed += 1
            c = v.get("classification")
            if c:
                classes[c] = classes.get(c, 0) + 1
        print("classes", classes, "judgment_failed", failed, "checkpoint_entries", len(ck))
        shown = 0
        for k, v in ck.items():
            if shown >= 8:
                break
            if isinstance(v, dict):
                print(k, v)
                shown += 1


if __name__ == "__main__":
    asyncio.run(main())
