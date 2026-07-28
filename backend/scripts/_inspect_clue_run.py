#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory


async def main(novel_id: int = 91, run_id: int = 16) -> None:
    async with async_session_factory() as s:
        queries = [
            "SELECT status, count(*) FROM clue_analysis_runs WHERE novel_id=:n GROUP BY 1",
            "SELECT id, status, version_id, progress, status_reason FROM clue_analysis_runs WHERE id=:r",
            "SELECT id, status FROM clue_analysis_versions WHERE novel_id=:n ORDER BY id DESC LIMIT 5",
            "SELECT version_id, count(*) FROM clue_evidence_refs WHERE novel_id=:n GROUP BY 1 ORDER BY 1 DESC LIMIT 5",
            "SELECT version_id, count(*) FROM clue_lifecycle_events WHERE novel_id=:n GROUP BY 1",
            "SELECT version_id, count(*) FROM clue_links WHERE novel_id=:n GROUP BY 1",
            "SELECT status, count(*) FROM clue_model_call_attempts WHERE novel_id=:n GROUP BY 1",
            "SELECT coalesce(error_code,'(null)'), count(*) FROM clue_model_call_attempts WHERE novel_id=:n GROUP BY 1 ORDER BY 2 DESC LIMIT 15",
            "SELECT version_id, revision, manifest_checksum FROM clue_active_pointers WHERE novel_id=:n",
        ]
        for q in queries:
            r = await s.execute(text(q), {"n": novel_id, "r": run_id})
            print(q)
            print(" =>", r.fetchall())
            print()

        r = await s.execute(
            text(
                """
                SELECT id, status, error_code,
                       left(coalesce(error_detail,''), 160) AS detail
                FROM clue_model_call_attempts
                WHERE novel_id=:n
                ORDER BY id DESC
                LIMIT 8
                """
            ),
            {"n": novel_id},
        )
        print("attempt samples:")
        for row in r.fetchall():
            print(row)

        # machine clues table?
        r = await s.execute(
            text(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name LIKE 'clue%'
                ORDER BY 1
                """
            )
        )
        print("clue tables:", [x[0] for x in r.fetchall()])

        # try machine clues if exists
        for t in ("clue_machine_clues", "machine_clues", "clue_items"):
            try:
                r = await s.execute(
                    text(f"SELECT count(*) FROM {t} WHERE novel_id=:n"),
                    {"n": novel_id},
                )
                print(t, r.scalar())
            except Exception as e:
                print(t, "NA", type(e).__name__)
                await s.rollback()


if __name__ == "__main__":
    asyncio.run(main())
