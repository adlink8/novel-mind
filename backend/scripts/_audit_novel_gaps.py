#!/usr/bin/env python3
"""Quick gap audit for one novel's analysis stack."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text
from app.core.database import async_session_factory
from app.models.novel import Novel, Chapter
from app.models.character import Character, CharacterRelation
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineCausalEdge,
)
from app.models.knowledge import (
    KnowledgeRelationJudgment,
    KnowledgeEntityCandidate,
    KnowledgeExtractionRun,
)
from app.models.relationship import RelationshipObservation
from app.models.clue import ClueAnalysisRun
from app.models.chunk_build import ChunkActivePointer
from app.models.text_chunk import TextChunk


async def main(novel_id: int) -> None:
    async with async_session_factory() as s:
        n = await s.get(Novel, novel_id)
        print(f"=== novel {novel_id} {n.title if n else '?'} owner={getattr(n,'owner_id',None)}")
        print("chapters_db", await s.scalar(select(func.count()).select_from(Chapter).where(Chapter.novel_id == novel_id)))
        print("text_chunks", await s.scalar(select(func.count()).select_from(TextChunk).where(TextChunk.novel_id == novel_id)))
        cap = await s.scalar(select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id))
        print("chunk_active_build", cap.build_id if cap else None)
        print("characters", await s.scalar(select(func.count()).select_from(Character).where(Character.novel_id == novel_id)))
        try:
            legacy = await s.scalar(
                select(func.count())
                .select_from(CharacterRelation)
                .join(Character, Character.id == CharacterRelation.source_id)
                .where(Character.novel_id == novel_id)
            )
            print("legacy_character_relations", legacy)
        except Exception as e:
            print("legacy_character_relations", type(e).__name__, e)
            await s.rollback()

        tptr = await s.scalar(select(TimelineActivePointer).where(TimelineActivePointer.novel_id == novel_id))
        print("timeline_active_version", tptr.version_id if tptr else None)
        print(
            "timeline_events",
            await s.scalar(
                select(func.count()).select_from(MachineTimelineEvent).where(MachineTimelineEvent.novel_id == novel_id)
            ),
        )
        print(
            "timeline_causal_edges",
            await s.scalar(
                select(func.count())
                .select_from(TimelineCausalEdge)
                .join(MachineTimelineEvent, MachineTimelineEvent.id == TimelineCausalEdge.source_event_id)
                .where(MachineTimelineEvent.novel_id == novel_id)
            ),
        )

        print(
            "kg_runs",
            await s.scalar(
                select(func.count()).select_from(KnowledgeExtractionRun).where(KnowledgeExtractionRun.novel_id == novel_id)
            ),
        )
        print(
            "kg_entity_candidates",
            await s.scalar(
                select(func.count())
                .select_from(KnowledgeEntityCandidate)
                .where(KnowledgeEntityCandidate.novel_id == novel_id)
            ),
        )
        print(
            "kg_accepted",
            await s.scalar(
                select(func.count())
                .select_from(KnowledgeRelationJudgment)
                .where(
                    KnowledgeRelationJudgment.novel_id == novel_id,
                    KnowledgeRelationJudgment.status == "accepted",
                )
            ),
        )
        print(
            "rel_obs_accepted",
            await s.scalar(
                select(func.count())
                .select_from(RelationshipObservation)
                .where(
                    RelationshipObservation.novel_id == novel_id,
                    RelationshipObservation.status == "accepted",
                )
            ),
        )
        print(
            "rel_obs_with_valid_to",
            await s.scalar(
                select(func.count())
                .select_from(RelationshipObservation)
                .where(
                    RelationshipObservation.novel_id == novel_id,
                    RelationshipObservation.status == "accepted",
                    RelationshipObservation.valid_to_chapter.is_not(None),
                )
            ),
        )
        transitions = (
            await s.execute(
                select(RelationshipObservation.transition, func.count())
                .where(
                    RelationshipObservation.novel_id == novel_id,
                    RelationshipObservation.status == "accepted",
                )
                .group_by(RelationshipObservation.transition)
            )
        ).all()
        print("rel_transitions", dict(transitions))

        clue = await s.scalar(
            select(ClueAnalysisRun)
            .where(ClueAnalysisRun.novel_id == novel_id)
            .order_by(ClueAnalysisRun.id.desc())
            .limit(1)
        )
        if clue:
            print("clue_run", clue.id, clue.status, (clue.status_reason or "")[:160], clue.active_key)
        else:
            print("clue_run", None)

        tables = (
            await s.execute(
                text(
                    """
                    SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r'
                      AND (c.relname LIKE 'clue%' OR c.relname LIKE '%narrative%' OR c.relname LIKE '%memory%')
                    ORDER BY 1
                    """
                )
            )
        ).fetchall()
        print("related_tables", [r[0] for r in tables])

        for tname in [
            "clue_cards",
            "clue_items",
            "clue_observations",
            "clue_analysis_versions",
            "narrative_memory_units",
            "narrative_unit_nodes",
        ]:
            try:
                c = await s.scalar(text(f"SELECT count(*) FROM {tname} WHERE novel_id = :nid"), {"nid": novel_id})
                print(f"count:{tname}", c)
            except Exception as e:
                print(f"count:{tname}", "NA", type(e).__name__)
                await s.rollback()


if __name__ == "__main__":
    nid = int(sys.argv[1]) if len(sys.argv) > 1 else 91
    asyncio.run(main(nid))
