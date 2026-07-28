"""Batch resume loop for long NM candidate builds (candidate-only, no promote)."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

logging.disable(logging.WARNING)

# scripts/ → backend on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def status_snapshot(owner_id: int, novel_id: int, version_id: int) -> dict:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.narrative_memory import NarrativeMemoryNode
    from app.models.narrative_memory_builder import (
        NarrativeMemoryBuildRun,
        NarrativeMemoryBuildStage,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    async with sessions() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == owner_id,
                NarrativeMemoryBuildRun.novel_id == novel_id,
                NarrativeMemoryBuildRun.version_id == version_id,
            )
        )
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run.id
                )
            )
        ).all() if run else []
        by_kind: dict[str, dict[str, int]] = {}
        for s in stages:
            bucket = by_kind.setdefault(s.stage_kind, {})
            bucket[s.status] = bucket.get(s.status, 0) + 1
        node_rows = (
            await session.execute(
                select(NarrativeMemoryNode.node_kind, func.count())
                .where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == version_id,
                )
                .group_by(NarrativeMemoryNode.node_kind)
            )
        ).all()
        nodes = {kind: int(cnt) for kind, cnt in node_rows}
    await eng.dispose()
    return {
        "run_status": run.status if run else None,
        "run_reason": run.status_reason if run else None,
        "stage_counts_by_kind": by_kind,
        "nodes_by_kind": nodes,
    }


async def heal_partial_stages(owner_id: int, novel_id: int, version_id: int) -> int:
    """Mark failed chapter stages completed when authority node already exists."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.narrative_memory import NarrativeMemoryNode
    from app.models.narrative_memory_builder import (
        NarrativeMemoryBuildRun,
        NarrativeMemoryBuildStage,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    healed = 0
    async with sessions() as session:
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(
                NarrativeMemoryBuildRun.owner_id == owner_id,
                NarrativeMemoryBuildRun.novel_id == novel_id,
                NarrativeMemoryBuildRun.version_id == version_id,
            )
        )
        if run is None:
            await eng.dispose()
            return 0
        failed = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run.id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                    NarrativeMemoryBuildStage.status == "failed",
                )
            )
        ).all()
        for st in failed:
            ch_num = st.chapter_start
            if ch_num is None:
                continue
            node_key = f"chapter_state:{ch_num}"
            exists = await session.scalar(
                select(NarrativeMemoryNode.id).where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == version_id,
                    NarrativeMemoryNode.node_key == node_key,
                )
            )
            if exists is not None:
                st.status = "completed"
                st.status_reason = "healed_existing_node"
                healed += 1
            # Leave other failed stages failed (worker skips them so batches advance).
        if run.status in {"failed", "paused_budget"}:
            run.status = "partial"
            run.status_reason = "heal_pass"
        await session.commit()
    await eng.dispose()
    return healed


async def one_batch(
    owner_id: int, novel_id: int, version_id: int, max_stages: int
) -> dict:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
    from scripts.run_narrative_memory_build import (
        _SessionInventorySource,
        _production_transport_and_deployment,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    transport, deployment = _production_transport_and_deployment(sessions, noop=False)
    worker = NarrativeMemoryBuilderWorker(
        sessions,
        inventory_source=_SessionInventorySource(sessions),
        transport=transport,
        deployment=deployment,
    )
    try:
        result = await worker.process_run(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            max_stages=max_stages,
        )
        return {
            "status": result.status,
            "status_reason": result.status_reason,
            "transport_calls": result.transport_calls,
            "completed_stages": len(result.completed_stages),
            "failed_stages": len(result.failed_stages),
        }
    finally:
        await eng.dispose()


async def main() -> int:
    owner_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    novel_id = int(sys.argv[2]) if len(sys.argv) > 2 else 91
    version_id = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    max_stages = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    max_batches = int(sys.argv[5]) if len(sys.argv) > 5 else 200

    terminal = {"completed", "cancelled", "failed"}
    log_path = Path(__file__).resolve().parents[2] / ".planning" / "phases" / (
        "20-structure-workspace-multilayer-presentation"
    ) / "20-NM-BUILD-LOOP.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for batch in range(1, max_batches + 1):
        t0 = time.time()
        try:
            healed = await heal_partial_stages(owner_id, novel_id, version_id)
            result = await one_batch(owner_id, novel_id, version_id, max_stages)
            if isinstance(result, dict):
                result["healed"] = healed
        except Exception as exc:  # noqa: BLE001
            result = {"error": type(exc).__name__, "message": str(exc)}
        snap = await status_snapshot(owner_id, novel_id, version_id)
        line = {
            "batch": batch,
            "elapsed_s": round(time.time() - t0, 1),
            "result": result,
            "snapshot": snap,
        }
        text = json.dumps(line, ensure_ascii=False)
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

        status = result.get("status") if isinstance(result, dict) else None
        if status in terminal:
            return 0 if status == "completed" else 1
        if "error" in result:
            return 1
        # If no progress on stages (paused budget etc.) stop.
        if status == "paused_budget":
            return 1
        ch = snap.get("stage_counts_by_kind", {}).get("chapter_state", {})
        completed = int(ch.get("completed", 0))
        pending = int(ch.get("pending", 0))
        # Chapters drained (pending only — failed skipped) → parent/global pass.
        if pending == 0 and completed > 0:
            break
    else:
        # max_batches exhausted with chapters still pending
        snap = await status_snapshot(owner_id, novel_id, version_id)
        print(json.dumps({"batch": "exhausted", "snapshot": snap}, ensure_ascii=False), flush=True)
        return 1

    # Unlimited final drain for arc/global/manifest once chapters are done.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
    from scripts.run_narrative_memory_build import (
        _SessionInventorySource,
        _production_transport_and_deployment,
    )

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url, pool_pre_ping=True)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    transport, deployment = _production_transport_and_deployment(sessions, noop=False)
    worker = NarrativeMemoryBuilderWorker(
        sessions,
        inventory_source=_SessionInventorySource(sessions),
        transport=transport,
        deployment=deployment,
    )
    try:
        final = await worker.process_run(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            max_stages=None,
        )
        snap = await status_snapshot(owner_id, novel_id, version_id)
        line = {
            "batch": "final",
            "result": {
                "status": final.status,
                "status_reason": final.status_reason,
                "transport_calls": final.transport_calls,
                "completed_stages": len(final.completed_stages),
                "failed_stages": len(final.failed_stages),
            },
            "snapshot": snap,
        }
        text = json.dumps(line, ensure_ascii=False)
        print(text, flush=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
        return 0 if final.status == "completed" else 1
    finally:
        await eng.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
