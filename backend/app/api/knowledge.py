"""Knowledge graph audit, gate, review, and projection API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import require_user
from app.models.character import Character, CharacterRelation
from app.models.knowledge import (
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
    KnowledgeReviewQueue,
)
from app.models.novel import Novel
from app.models.timeline import TimelineEvent
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeExtractionRunResponse,
    KnowledgeRelationCandidateResponse,
    KnowledgeRelationJudgmentResponse,
    KnowledgeReviewActionRequest,
    KnowledgeReviewQueueResponse,
    KnowledgeRunStartRequest,
)
from app.services.knowledge.gates import knowledge_gate_service
from app.services.knowledge.projection import knowledge_projection_service


router = APIRouter(prefix="/api/knowledge", tags=["知识图谱"])


def _owned_novel_clause(current_user: User):
    if current_user.is_superuser:
        return True
    return Novel.owner_id == current_user.id


async def _require_owned_novel(
    db: AsyncSession,
    *,
    novel_id: int,
    current_user: User,
) -> Novel:
    result = await db.execute(
        select(Novel).where(
            Novel.id == novel_id,
            _owned_novel_clause(current_user),
        )
    )
    novel = result.scalar_one_or_none()
    if novel is None:
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


async def _require_owned_run(
    db: AsyncSession,
    *,
    run_id: int,
    current_user: User,
) -> KnowledgeExtractionRun:
    result = await db.execute(
        select(KnowledgeExtractionRun)
        .join(Novel, Novel.id == KnowledgeExtractionRun.novel_id)
        .where(
            KnowledgeExtractionRun.id == run_id,
            _owned_novel_clause(current_user),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="知识图谱运行不存在")
    return run


async def _require_owned_judgment(
    db: AsyncSession,
    *,
    judgment_id: int,
    current_user: User,
) -> KnowledgeRelationJudgment:
    result = await db.execute(
        select(KnowledgeRelationJudgment)
        .join(Novel, Novel.id == KnowledgeRelationJudgment.novel_id)
        .where(
            KnowledgeRelationJudgment.id == judgment_id,
            _owned_novel_clause(current_user),
        )
    )
    judgment = result.scalar_one_or_none()
    if judgment is None:
        raise HTTPException(status_code=404, detail="知识图谱判定不存在")
    return judgment


@router.post("/runs", response_model=KnowledgeExtractionRunResponse)
async def start_knowledge_run(
    body: KnowledgeRunStartRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Create a persisted extraction run; long work is handled outside HTTP."""

    novel = await _require_owned_novel(
        db,
        novel_id=body.novel_id,
        current_user=current_user,
    )
    run = KnowledgeExtractionRun(
        owner_id=novel.owner_id,
        novel_id=novel.id,
        run_name=body.run_name,
        domain_profile=body.domain_profile,
        ontology_profile=body.ontology_profile,
        status="pending",
        prompt_version=body.prompt_version,
        config_snapshot=body.config_snapshot,
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


@router.get("/runs", response_model=list[KnowledgeExtractionRunResponse])
async def list_knowledge_runs(
    novel_id: int | None = Query(None, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List extraction runs visible to the current user."""

    stmt = (
        select(KnowledgeExtractionRun)
        .join(Novel, Novel.id == KnowledgeExtractionRun.novel_id)
        .where(_owned_novel_clause(current_user))
        .order_by(KnowledgeExtractionRun.created_at.desc())
    )
    if novel_id is not None:
        stmt = stmt.where(KnowledgeExtractionRun.novel_id == novel_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get(
    "/runs/{run_id}/candidates",
    response_model=list[KnowledgeRelationCandidateResponse],
)
async def list_relation_candidates(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List relation candidates for a visible run."""

    await _require_owned_run(db, run_id=run_id, current_user=current_user)
    result = await db.execute(
        select(KnowledgeRelationCandidate)
        .where(KnowledgeRelationCandidate.run_id == run_id)
        .order_by(KnowledgeRelationCandidate.id.asc())
    )
    return result.scalars().all()


@router.get(
    "/runs/{run_id}/judgments",
    response_model=list[KnowledgeRelationJudgmentResponse],
)
async def list_relation_judgments(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List relation judgments for a visible run."""

    await _require_owned_run(db, run_id=run_id, current_user=current_user)
    result = await db.execute(
        select(KnowledgeRelationJudgment)
        .where(KnowledgeRelationJudgment.run_id == run_id)
        .order_by(KnowledgeRelationJudgment.id.asc())
    )
    return result.scalars().all()


@router.get(
    "/runs/{run_id}/review",
    response_model=list[KnowledgeReviewQueueResponse],
)
async def list_review_queue(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List review queue items for a visible run."""

    await _require_owned_run(db, run_id=run_id, current_user=current_user)
    result = await db.execute(
        select(KnowledgeReviewQueue)
        .where(KnowledgeReviewQueue.run_id == run_id)
        .order_by(KnowledgeReviewQueue.created_at.asc())
    )
    return result.scalars().all()


@router.post("/runs/{run_id}/gate", response_model=dict)
async def gate_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Apply deterministic gates to all judgments in a visible run."""

    run = await _require_owned_run(db, run_id=run_id, current_user=current_user)
    return await knowledge_gate_service.gate_run(
        db,
        run_id=run.id,
        owner_id=None if current_user.is_superuser else current_user.id,
    )


@router.post("/judgments/{judgment_id}/accept", response_model=dict)
async def accept_judgment(
    judgment_id: int,
    body: KnowledgeReviewActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Manually accept a reviewed judgment and replay projection."""

    judgment = await _require_owned_judgment(
        db,
        judgment_id=judgment_id,
        current_user=current_user,
    )
    decision = await knowledge_gate_service.accept_reviewed_judgment(
        db,
        judgment_id=judgment.id,
        reviewer_notes=body.reviewer_notes if body else None,
    )
    projection = None
    if decision.accepted:
        projection = await knowledge_projection_service.project_judgment(
            db,
            judgment_id=judgment.id,
        )
    return {
        "decision": decision,
        "projection": projection,
    }


@router.post("/judgments/{judgment_id}/reject", response_model=dict)
async def reject_judgment(
    judgment_id: int,
    body: KnowledgeReviewActionRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Manually reject a reviewed judgment."""

    judgment = await _require_owned_judgment(
        db,
        judgment_id=judgment_id,
        current_user=current_user,
    )
    decision = await knowledge_gate_service.reject_reviewed_judgment(
        db,
        judgment_id=judgment.id,
        reviewer_notes=body.reviewer_notes if body else None,
    )
    return {"decision": decision}


@router.get("/novels/{novel_id}/graph", response_model=dict)
async def get_graph_neighborhood(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Return accepted PostgreSQL judgments and replayed graph records."""

    novel = await _require_owned_novel(
        db,
        novel_id=novel_id,
        current_user=current_user,
    )
    judgments_result = await db.execute(
        select(KnowledgeRelationJudgment)
        .options(selectinload(KnowledgeRelationJudgment.candidate))
        .where(
            KnowledgeRelationJudgment.novel_id == novel.id,
            KnowledgeRelationJudgment.status == "accepted",
            KnowledgeRelationJudgment.gate_status == "accepted",
        )
        .order_by(KnowledgeRelationJudgment.id.asc())
    )
    accepted_judgments = judgments_result.scalars().all()

    characters = (
        await db.execute(select(Character).where(Character.novel_id == novel.id))
    ).scalars().all()
    character_names = {character.id: character.name for character in characters}
    relations = (
        await db.execute(
            select(CharacterRelation)
            .where(CharacterRelation.novel_id == novel.id)
            .order_by(CharacterRelation.id.asc())
        )
    ).scalars().all()
    timeline_events = (
        await db.execute(
            select(TimelineEvent)
            .where(TimelineEvent.novel_id == novel.id)
            .order_by(TimelineEvent.sort_order.asc(), TimelineEvent.id.asc())
        )
    ).scalars().all()

    return {
        "novel_id": novel.id,
        "accepted_judgments": [
            {
                "id": item.id,
                "relation_type": item.relation_type,
                "confidence": item.confidence,
                "evidence_refs": item.evidence_refs,
                "source": {
                    "kind": item.candidate.source_kind,
                    "id": item.candidate.source_id,
                },
                "target": {
                    "kind": item.candidate.target_kind,
                    "id": item.candidate.target_id,
                },
            }
            for item in accepted_judgments
        ],
        "character_relations": [
            {
                "id": relation.id,
                "source": character_names.get(relation.source_character_id),
                "target": character_names.get(relation.target_character_id),
                "relation_type": relation.relation_type,
                "description": relation.description,
            }
            for relation in relations
        ],
        "timeline_events": [
            {
                "id": event.id,
                "title": event.event_title,
                "event_type": event.event_type,
                "description": event.event_description,
                "time_reference": event.time_reference,
            }
            for event in timeline_events
        ],
    }
