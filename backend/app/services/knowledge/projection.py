"""Replay accepted knowledge judgments into existing graph-facing tables."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.character import Character, CharacterRelation
from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEventCandidate,
    KnowledgeEvidenceRef,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter
from app.models.timeline import TimelineEvent


@dataclass(slots=True)
class ProjectionResult:
    """Result of replaying one accepted judgment."""

    judgment_id: int
    status: str
    reason: str | None = None
    character_relation_ids: list[int] = field(default_factory=list)
    timeline_event_ids: list[int] = field(default_factory=list)


class KnowledgeProjectionService:
    """Project accepted PostgreSQL judgment rows into queryable app tables."""

    async def project_judgment(
        self,
        db: AsyncSession,
        *,
        judgment_id: int,
    ) -> ProjectionResult:
        """Project one accepted judgment. The operation is idempotent."""

        judgment = await self._load_judgment(db, judgment_id)
        if judgment.status != "accepted" or judgment.gate_status != "accepted":
            return ProjectionResult(
                judgment_id=judgment_id,
                status="skipped",
                reason="judgment_not_accepted",
            )
        if not judgment.evidence_refs:
            return ProjectionResult(
                judgment_id=judgment_id,
                status="skipped",
                reason="missing_evidence_refs",
            )

        candidate = judgment.candidate
        if candidate.domain_profile == "fiction":
            return await self._project_fiction_relation(db, judgment=judgment)
        if candidate.domain_profile == "history":
            return await self._project_history_events(db, judgment=judgment)
        return ProjectionResult(
            judgment_id=judgment_id,
            status="skipped",
            reason=f"unsupported_domain_profile:{candidate.domain_profile}",
        )

    async def project_run(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        owner_id: int | None = None,
    ) -> list[ProjectionResult]:
        """Replay all accepted judgments in a run."""

        stmt = (
            select(KnowledgeRelationJudgment.id)
            .where(
                KnowledgeRelationJudgment.run_id == run_id,
                KnowledgeRelationJudgment.status == "accepted",
                KnowledgeRelationJudgment.gate_status == "accepted",
            )
            .order_by(KnowledgeRelationJudgment.id.asc())
        )
        if owner_id is not None:
            stmt = stmt.where(KnowledgeRelationJudgment.owner_id == owner_id)
        result = await db.execute(stmt)
        return [
            await self.project_judgment(db, judgment_id=judgment_id)
            for judgment_id in result.scalars().all()
        ]

    async def _project_fiction_relation(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
    ) -> ProjectionResult:
        candidate = judgment.candidate
        if (
            candidate.source_kind != "entity_candidate"
            or candidate.target_kind != "entity_candidate"
        ):
            return ProjectionResult(
                judgment_id=judgment.id,
                status="skipped",
                reason="insufficient_entity_resolution",
            )

        source_entity = await self._load_entity_endpoint(
            db,
            candidate=candidate,
            entity_id=candidate.source_id,
        )
        target_entity = await self._load_entity_endpoint(
            db,
            candidate=candidate,
            entity_id=candidate.target_id,
        )
        if source_entity is None or target_entity is None:
            return ProjectionResult(
                judgment_id=judgment.id,
                status="skipped",
                reason="entity_endpoint_missing",
            )

        source_character = await self._get_or_create_character(
            db,
            entity=source_entity,
        )
        target_character = await self._get_or_create_character(
            db,
            entity=target_entity,
        )
        relation = await self._get_or_create_character_relation(
            db,
            judgment=judgment,
            source_character=source_character,
            target_character=target_character,
        )
        await db.flush()
        return ProjectionResult(
            judgment_id=judgment.id,
            status="projected",
            character_relation_ids=[relation.id],
        )

    async def _project_history_events(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
    ) -> ProjectionResult:
        candidate = judgment.candidate
        if (
            candidate.source_kind != "event_candidate"
            or candidate.target_kind != "event_candidate"
        ):
            return ProjectionResult(
                judgment_id=judgment.id,
                status="skipped",
                reason="insufficient_event_resolution",
            )

        event_ids: list[int] = []
        for event_id in (candidate.source_id, candidate.target_id):
            event_candidate = await self._load_event_endpoint(
                db,
                candidate=candidate,
                event_id=event_id,
            )
            if event_candidate is None:
                return ProjectionResult(
                    judgment_id=judgment.id,
                    status="skipped",
                    reason="event_endpoint_missing",
                )
            timeline_event = await self._get_or_create_timeline_event(
                db,
                judgment=judgment,
                event=event_candidate,
            )
            event_ids.append(timeline_event.id)

        await db.flush()
        return ProjectionResult(
            judgment_id=judgment.id,
            status="projected",
            timeline_event_ids=event_ids,
        )

    async def _load_judgment(
        self,
        db: AsyncSession,
        judgment_id: int,
    ) -> KnowledgeRelationJudgment:
        result = await db.execute(
            select(KnowledgeRelationJudgment)
            .options(selectinload(KnowledgeRelationJudgment.candidate))
            .where(KnowledgeRelationJudgment.id == judgment_id)
        )
        judgment = result.scalar_one_or_none()
        if judgment is None:
            raise ValueError("Knowledge relation judgment not found")
        return judgment

    async def _load_entity_endpoint(
        self,
        db: AsyncSession,
        *,
        candidate: KnowledgeRelationCandidate,
        entity_id: int,
    ) -> KnowledgeEntityCandidate | None:
        result = await db.execute(
            select(KnowledgeEntityCandidate).where(
                KnowledgeEntityCandidate.id == entity_id,
                KnowledgeEntityCandidate.owner_id == candidate.owner_id,
                KnowledgeEntityCandidate.novel_id == candidate.novel_id,
                KnowledgeEntityCandidate.run_id == candidate.run_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_event_endpoint(
        self,
        db: AsyncSession,
        *,
        candidate: KnowledgeRelationCandidate,
        event_id: int,
    ) -> KnowledgeEventCandidate | None:
        result = await db.execute(
            select(KnowledgeEventCandidate).where(
                KnowledgeEventCandidate.id == event_id,
                KnowledgeEventCandidate.owner_id == candidate.owner_id,
                KnowledgeEventCandidate.novel_id == candidate.novel_id,
                KnowledgeEventCandidate.run_id == candidate.run_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_character(
        self,
        db: AsyncSession,
        *,
        entity: KnowledgeEntityCandidate,
    ) -> Character:
        result = await db.execute(
            select(Character).where(
                Character.novel_id == entity.novel_id,
                Character.name == entity.canonical_name,
            )
        )
        character = result.scalar_one_or_none()
        if character is not None:
            return character

        character = Character(
            novel_id=entity.novel_id,
            name=entity.canonical_name,
            aliases=", ".join(entity.aliases or []) or None,
            role="supporting",
            description=(
                f"Projected from knowledge entity candidate {entity.id}; "
                f"evidence={','.join(entity.evidence_refs or [])}"
            ),
        )
        db.add(character)
        await db.flush()
        return character

    async def _get_or_create_character_relation(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        source_character: Character,
        target_character: Character,
    ) -> CharacterRelation:
        marker = _judgment_marker(judgment.id)
        result = await db.execute(
            select(CharacterRelation).where(
                CharacterRelation.novel_id == judgment.novel_id,
                CharacterRelation.source_character_id == source_character.id,
                CharacterRelation.target_character_id == target_character.id,
                CharacterRelation.relation_type == judgment.relation_type,
                CharacterRelation.description.contains(marker),
            )
        )
        relation = result.scalar_one_or_none()
        if relation is not None:
            return relation

        chapter_number = await self._first_evidence_chapter_number(db, judgment)
        relation = CharacterRelation(
            novel_id=judgment.novel_id,
            source_character_id=source_character.id,
            target_character_id=target_character.id,
            relation_type=judgment.relation_type,
            description=_projection_description(judgment),
            strength=max(1, min(10, round(judgment.confidence * 10))),
            chapter_first_seen=chapter_number,
            intake_kind=_judgment_intake_kind(judgment),
        )
        db.add(relation)
        await db.flush()
        return relation

    async def _get_or_create_timeline_event(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        event: KnowledgeEventCandidate,
    ) -> TimelineEvent:
        marker = f"kg_event_candidate_id={event.id}"
        result = await db.execute(
            select(TimelineEvent).where(
                TimelineEvent.novel_id == event.novel_id,
                TimelineEvent.event_description.contains(marker),
            )
        )
        timeline_event = result.scalar_one_or_none()
        if timeline_event is not None:
            return timeline_event

        chapter_id = await self._first_evidence_chapter_id(
            db,
            event.evidence_refs,
            owner_id=event.owner_id,
            novel_id=event.novel_id,
            run_id=event.run_id,
        )
        timeline_event = TimelineEvent(
            novel_id=event.novel_id,
            chapter_id=chapter_id,
            event_title=event.title,
            event_description=(
                f"{event.summary or ''}\n"
                f"kg_event_candidate_id={event.id}; "
                f"{_judgment_marker(judgment.id)}; "
                f"evidence={','.join(event.evidence_refs or judgment.evidence_refs)}"
            ).strip(),
            event_type=event.event_type,
            sort_order=float(event.id),
            characters_involved=json.dumps(
                event.participant_refs or [],
                ensure_ascii=False,
            ),
            location=(event.location_refs or [None])[0],
            time_reference=(event.time_refs or [None])[0],
        )
        db.add(timeline_event)
        await db.flush()
        return timeline_event

    async def _first_evidence_chapter_number(
        self,
        db: AsyncSession,
        judgment: KnowledgeRelationJudgment,
    ) -> int | None:
        chapter_id = await self._first_evidence_chapter_id(
            db,
            judgment.evidence_refs,
            owner_id=judgment.owner_id,
            novel_id=judgment.novel_id,
            run_id=judgment.run_id,
        )
        if chapter_id is None:
            return None
        chapter = await db.get(Chapter, chapter_id)
        return chapter.chapter_number if chapter else None

    async def _first_evidence_chapter_id(
        self,
        db: AsyncSession,
        evidence_refs: list[str],
        *,
        owner_id: int,
        novel_id: int,
        run_id: int,
    ) -> int | None:
        if not evidence_refs:
            return None
        stmt = (
            select(KnowledgeEvidenceRef.chapter_id)
            .where(
                KnowledgeEvidenceRef.owner_id == owner_id,
                KnowledgeEvidenceRef.novel_id == novel_id,
                KnowledgeEvidenceRef.run_id == run_id,
                KnowledgeEvidenceRef.ref_key.in_(evidence_refs),
                KnowledgeEvidenceRef.chapter_id.is_not(None),
            )
            .order_by(KnowledgeEvidenceRef.id.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


def _judgment_marker(judgment_id: int) -> str:
    return f"kg_judgment_id={judgment_id}"


def _judgment_intake_kind(judgment: KnowledgeRelationJudgment) -> str:
    """Provenance for projected rows: seed backfill vs real LLM judgment."""
    if (judgment.model_name or "") == "timeline_cooccur_heuristic":
        return "timeline_seed_backfill"
    for payload in (judgment.structured_output, judgment.raw_output):
        if (
            isinstance(payload, dict)
            and payload.get("source") == "timeline_kg_backfill"
        ):
            return "timeline_seed_backfill"
    return "llm_judgment"


def _projection_description(judgment: KnowledgeRelationJudgment) -> str:
    return (
        f"{_judgment_marker(judgment.id)}; "
        f"evidence={','.join(judgment.evidence_refs or [])}; "
        f"confidence={judgment.confidence:.2f}; "
        f"rationale={judgment.rationale or ''}"
    )


knowledge_projection_service = KnowledgeProjectionService()
