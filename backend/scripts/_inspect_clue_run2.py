#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory


async def main() -> None:
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='clue_model_call_attempts' ORDER BY 1"
            )
        )
        print("attempt_cols", [x[0] for x in r])

        r = await s.execute(text("SELECT count(*) FROM clue_model_call_attempts"))
        print("attempts_total", r.scalar())

        r = await s.execute(
            text("SELECT status, count(*) FROM clue_model_call_attempts GROUP BY 1")
        )
        print("by_status", r.fetchall())

        r = await s.execute(
            text(
                "SELECT id, run_id, status, error_code, "
                "left(coalesce(error_detail,''), 140) AS detail "
                "FROM clue_model_call_attempts ORDER BY id DESC LIMIT 12"
            )
        )
        print("samples:")
        for row in r.fetchall():
            print(row)

        from app.models.clue import ClueAnalysisRun

        run = await s.get(ClueAnalysisRun, 16)
        print("checkpoint_keys", list((run.checkpoint or {}).keys())[:20] if run else None)
        ck = run.checkpoint or {}
        # summarize classifications from checkpoint stage marks
        classes = {}
        failed = 0
        for k, v in ck.items():
            if not isinstance(v, dict):
                continue
            if v.get("status") == "judgment_failed":
                failed += 1
            c = v.get("classification")
            if c:
                classes[c] = classes.get(c, 0) + 1
        print("checkpoint_classifications", classes, "judgment_failed", failed)
        # print a few checkpoint entries
        shown = 0
        for k, v in ck.items():
            if shown >= 6:
                break
            if isinstance(v, dict) and ("classification" in v or "status" in v):
                print(" ck", k, json.dumps(v, ensure_ascii=False)[:200])
                shown += 1

        # machine clues via version
        r = await s.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name LIKE 'clue%' ORDER BY 1"
            )
        )
        print("tables", [x[0] for x in r])

        for t in [
            "clue_evidence_refs",
            "clue_lifecycle_events",
            "clue_links",
            "clue_overrides",
        ]:
            r = await s.execute(
                text(f"SELECT count(*) FROM {t} WHERE version_id=18")
            )
            print(t, "v18", r.scalar())


if __name__ == "__main__":
    asyncio.run(main())
