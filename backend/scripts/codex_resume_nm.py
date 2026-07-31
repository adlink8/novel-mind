from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from app.models.full_analysis import FullAnalysisRun
from app.services.analysis_orchestrator import _set_run_status
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.builder_worker import NarrativeMemoryBuilderWorker
from scripts.run_narrative_memory_build import (
    _SessionInventorySource,
    _production_transport_and_deployment,
)


async def main() -> None:
    owner_id = 2
    novel_id = 216
    version_id = 17
    build_run_id = 17
    full_run_id = 33
    sessions = async_session_factory
    transport, deployment = _production_transport_and_deployment(
        sessions, noop=False
    )
    worker = NarrativeMemoryBuilderWorker(
        sessions,
        inventory_source=_SessionInventorySource(sessions),
        transport=transport,
        deployment=deployment,
    )
    lease_id = uuid4().hex
    terminal = {
        "completed",
        "partial",
        "failed",
        "paused_budget",
        "paused_dependency",
        "cancelled",
    }
    while True:
        result = await worker.process_run(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            lease_id=lease_id,
            max_stages=1,
        )
        async with sessions() as session:
            repo = BuilderRepository(session)
            stages = await repo.list_stages(build_run_id)
            done = sum(stage.status == "completed" for stage in stages)
            total = len(stages)
            full = await session.get(FullAnalysisRun, full_run_id)
            active = next(
                (
                    stage
                    for stage in stages
                    if stage.status
                    in {"pending", "running", "blocked_dependency"}
                ),
                None,
            )
            stage_name = {
                "chapter_state": "nm_chapter_state",
                "arc_volume_plan": "nm_arc_plan",
                "arc_volume_aggregate": "nm_aggregate",
                "global_aggregate": "nm_aggregate",
                "manifest_validation": "nm_aggregate",
            }.get(active.stage_kind if active else "manifest_validation", "nm_aggregate")
            if full is not None:
                full.status = "running"
                full.current_stage = stage_name
                full.progress = {
                    "stage": stage_name,
                    "progress": f"{done}/{total}",
                    "status": "running",
                    "stage_index": 6,
                    "stage_total": 8,
                }
            await session.commit()
        if result.status in terminal:
            if result.status == "completed":
                await _set_run_status(full_run_id, "completed")
            else:
                await _set_run_status(
                    full_run_id,
                    "failed",
                    reason=result.status_reason or result.status,
                )
            print(
                {
                    "build_run_id": build_run_id,
                    "full_run_id": full_run_id,
                    "status": result.status,
                    "completed": done,
                    "total": total,
                }
            )
            return


if __name__ == "__main__":
    asyncio.run(main())
