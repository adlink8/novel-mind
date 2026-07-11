"""Narrative unit lifecycle propagation without physical audit deletion."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeRelationJudgment
from app.models.knowledge_unit import NarrativeUnit


async def sync_unit_lifecycle(db: AsyncSession, *, snapshot_id: int) -> dict[str, int]:
    rows = (
        await db.execute(
            select(NarrativeUnit, KnowledgeRelationJudgment)
            .join(
                KnowledgeRelationJudgment,
                KnowledgeRelationJudgment.id == NarrativeUnit.source_judgment_id,
            )
            .where(NarrativeUnit.source_snapshot_id == snapshot_id)
        )
    ).all()
    counts = {"current": 0, "disputed": 0, "deprecated": 0, "deleted": 0}
    for unit, judgment in rows:
        if judgment.status in {"rejected", "deprecated", "deleted"}:
            unit.lifecycle_status = "deprecated"
            unit.status = "deprecated"
            counts["deprecated"] += 1
        elif judgment.risk_flags or judgment.needs_human_review:
            unit.lifecycle_status = "disputed"
            counts["disputed"] += 1
        else:
            unit.lifecycle_status = "current"
            counts["current"] += 1
    await db.flush()
    return counts
