"""Deterministic gate tests for knowledge relation judgments."""

import pytest

pytestmark = pytest.mark.unit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
    KnowledgeReviewQueue,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.knowledge.gates import knowledge_gate_service


async def _base_context(db: AsyncSession, username: str = "kg_gate_owner"):
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="hash",
    )
    db.add(user)
    await db.flush()

    novel = Novel(title=f"{username} novel", owner_id=user.id)
    db.add(novel)
    await db.flush()

    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="刘备与关羽结义，随后共同起兵。",
        word_count=18,
    )
    db.add(chapter)
    await db.flush()

    chunk = TextChunk(
        novel_id=novel.id,
        chapter_id=chapter.id,
        chunk_index=0,
        content="刘备与关羽结义，随后共同起兵。",
        chunk_type="narration",
        metadata_json={"characters": ["刘备", "关羽"]},
        word_count=18,
        embedding_status="embedded",
    )
    db.add(chunk)
    await db.flush()

    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="gate run",
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="running",
    )
    db.add(run)
    await db.flush()

    evidence = KnowledgeEvidenceRef(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        ref_key="ev-chunk-1",
        source_type="text_chunk",
        text_chunk_id=chunk.id,
        chapter_id=chapter.id,
        excerpt="刘备与关羽结义",
    )
    db.add(evidence)
    await db.flush()
    return user, novel, run, evidence


async def _entity(db: AsyncSession, *, user: User, novel: Novel, run: KnowledgeExtractionRun, name: str):
    entity = KnowledgeEntityCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        canonical_name=name,
        aliases=[],
        domain_profile="fiction",
        entity_type="character",
        evidence_refs=["ev-chunk-1"],
        source_refs=["ev-chunk-1"],
        confidence=0.9,
    )
    db.add(entity)
    await db.flush()
    return entity


async def _judgment(
    db: AsyncSession,
    *,
    user: User,
    novel: Novel,
    run: KnowledgeExtractionRun,
    relation_type: str = "ally",
    confidence: float = 0.9,
    evidence_refs: list[str] | None = None,
    risk_flags: list[str] | None = None,
    needs_human_review: bool = False,
    source_id: int = 1,
    target_id: int = 2,
):
    refs = evidence_refs or ["ev-chunk-1"]
    candidate = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="fiction",
        relation_type=relation_type,
        source_kind="entity_candidate",
        source_id=source_id,
        target_kind="entity_candidate",
        target_id=target_id,
        recall_signals={"adjacency": {"same_chapter": True}},
        package_snapshot={
            "allowed_evidence_ids": ["ev-chunk-1"],
            "candidate": {"evidence_refs": ["ev-chunk-1"]},
        },
        evidence_refs=["ev-chunk-1"],
        status="proposed",
    )
    db.add(candidate)
    await db.flush()

    judgment = KnowledgeRelationJudgment(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=candidate.id,
        prompt_version="knowledge-relation-judge.v1",
        model_name="test/model",
        relation_type=relation_type,
        confidence=confidence,
        evidence_refs=refs,
        rationale="The cited evidence supports the relation.",
        risk_flags=risk_flags or [],
        raw_output={"test": True},
        structured_output={"relation_type": relation_type},
        status="pending",
        gate_status="evidence_passed",
        needs_human_review=needs_human_review,
    )
    db.add(judgment)
    await db.flush()
    return candidate, judgment


@pytest.mark.asyncio
async def test_valid_judgment_reaches_accepted(db_session: AsyncSession):
    user, novel, run, _ = await _base_context(db_session)
    source = await _entity(db_session, user=user, novel=novel, run=run, name="刘备")
    target = await _entity(db_session, user=user, novel=novel, run=run, name="关羽")
    candidate, judgment = await _judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        source_id=source.id,
        target_id=target.id,
    )

    decision = await knowledge_gate_service.gate_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    await db_session.commit()

    assert decision.accepted is True
    assert judgment.status == "accepted"
    assert judgment.gate_status == "accepted"
    assert candidate.status == "accepted"
    assert run.accepted_count == 1


@pytest.mark.asyncio
async def test_out_of_package_evidence_is_rejected(db_session: AsyncSession):
    user, novel, run, _ = await _base_context(db_session)
    _, judgment = await _judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        evidence_refs=["ev-chunk-999"],
    )

    decision = await knowledge_gate_service.gate_judgment(
        db_session,
        judgment_id=judgment.id,
    )

    assert decision.rejected is True
    assert judgment.status == "rejected"
    assert judgment.gate_status == "rejected"
    assert judgment.gate_failures == ["out_of_package_evidence:ev-chunk-999"]


@pytest.mark.asyncio
async def test_low_confidence_and_risk_flags_route_to_review_queue(
    db_session: AsyncSession,
):
    user, novel, run, _ = await _base_context(db_session)
    _, judgment = await _judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        confidence=0.61,
        risk_flags=["ambiguous_direction"],
    )

    decision = await knowledge_gate_service.gate_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    review_items = (
        await db_session.execute(select(KnowledgeReviewQueue))
    ).scalars().all()

    assert decision.needs_review is True
    assert judgment.status == "needs_human_review"
    assert judgment.gate_status == "needs_human_review"
    assert len(review_items) == 1
    assert "low_confidence:0.61" in review_items[0].reason
    assert "risk_flags:ambiguous_direction" in review_items[0].reason


@pytest.mark.asyncio
async def test_conflicting_accepted_judgment_routes_to_review(
    db_session: AsyncSession,
):
    user, novel, run, _ = await _base_context(db_session)
    source = await _entity(db_session, user=user, novel=novel, run=run, name="刘备")
    target = await _entity(db_session, user=user, novel=novel, run=run, name="关羽")
    _, accepted = await _judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        relation_type="ally",
        source_id=source.id,
        target_id=target.id,
    )
    accepted.status = "accepted"
    accepted.gate_status = "accepted"

    _, competing = await _judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        relation_type="enemy",
        source_id=source.id,
        target_id=target.id,
    )

    decision = await knowledge_gate_service.gate_judgment(
        db_session,
        judgment_id=competing.id,
    )

    assert decision.needs_review is True
    assert competing.status == "needs_human_review"
    assert competing.gate_failures == [
        f"conflicting_accepted_judgment:{accepted.id}"
    ]


@pytest.mark.asyncio
async def test_cross_owner_evidence_refs_fail_closed(db_session: AsyncSession):
    user, novel, run, _ = await _base_context(db_session)
    other_user, other_novel, other_run, other_evidence = await _base_context(
        db_session,
        username="kg_gate_other",
    )
    other_evidence.ref_key = "ev-cross"
    await db_session.flush()

    candidate = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="fiction",
        relation_type="ally",
        source_kind="entity_candidate",
        source_id=1,
        target_kind="entity_candidate",
        target_id=2,
        recall_signals={"adjacency": {"same_chapter": True}},
        package_snapshot={"allowed_evidence_ids": ["ev-cross"]},
        evidence_refs=["ev-cross"],
        status="proposed",
    )
    db_session.add(candidate)
    await db_session.flush()

    judgment = KnowledgeRelationJudgment(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=candidate.id,
        prompt_version="knowledge-relation-judge.v1",
        model_name="test/model",
        relation_type="ally",
        confidence=0.91,
        evidence_refs=["ev-cross"],
        rationale="Cross owner evidence should not be accepted.",
        risk_flags=[],
        raw_output={},
        structured_output={},
        status="pending",
        gate_status="evidence_passed",
    )
    db_session.add(judgment)
    await db_session.flush()

    decision = await knowledge_gate_service.gate_judgment(
        db_session,
        judgment_id=judgment.id,
    )

    assert other_user.id != user.id
    assert other_novel.id != novel.id
    assert other_run.id != run.id
    assert decision.rejected is True
    assert judgment.gate_failures == ["missing_evidence:ev-cross"]
