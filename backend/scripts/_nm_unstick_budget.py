"""Operator helper: clear paused_budget on NM build run after under-reserve settle."""
from __future__ import annotations

import asyncio
import logging
import sys

logging.disable(logging.WARNING)


async def main(run_id: int) -> None:
    from decimal import Decimal

    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.narrative_memory_builder import (
        NarrativeMemoryBuildBudgetLedger,
        NarrativeMemoryBuildBudgetReservation,
        NarrativeMemoryBuildRun,
        NarrativeMemoryBuildStage,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    async with sessions() as session:
        run = await session.get(NarrativeMemoryBuildRun, run_id)
        if run is None:
            print({"error": "run_not_found"})
            await eng.dispose()
            return
        ledger = await session.scalar(
            select(NarrativeMemoryBuildBudgetLedger).where(
                NarrativeMemoryBuildBudgetLedger.run_id == run_id
            )
        )
        assert ledger is not None
        open_res = (
            await session.scalars(
                select(NarrativeMemoryBuildBudgetReservation).where(
                    NarrativeMemoryBuildBudgetReservation.ledger_id == ledger.id,
                    NarrativeMemoryBuildBudgetReservation.status.in_(
                        ("reserved", "released", "failed")
                    ),
                )
            )
        ).all()
        released_n = 0
        for res in open_res:
            if res.status == "reserved":
                ledger.reserved_calls = max(0, ledger.reserved_calls - res.calls)
                ledger.reserved_input_tokens = max(
                    0, ledger.reserved_input_tokens - res.input_tokens
                )
                ledger.reserved_output_tokens = max(
                    0, ledger.reserved_output_tokens - res.output_tokens
                )
                ledger.reserved_cost_usd = max(
                    Decimal(0),
                    Decimal(ledger.reserved_cost_usd) - Decimal(res.cost_usd),
                )
            await session.delete(res)
            released_n += 1
        paused_stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.status.in_(
                        ("paused_budget", "failed", "running")
                    ),
                )
            )
        ).all()
        for st in paused_stages:
            st.status = "pending"
            st.status_reason = "budget_unstuck_operator"
        if run.status in {"paused_budget", "failed"}:
            run.status = "partial"
            run.status_reason = "budget_unstuck_operator"
        await session.commit()
        print(
            {
                "run_id": run_id,
                "status": run.status,
                "deleted_reservations": released_n,
                "reset_stages": len(paused_stages),
                "reserved_calls": ledger.reserved_calls,
                "settled_calls": ledger.settled_calls,
            }
        )
    await eng.dispose()


if __name__ == "__main__":
    rid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    asyncio.run(main(rid))
