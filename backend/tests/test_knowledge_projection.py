"""Projection tests for accepted knowledge graph judgments."""

import pytest

pytestmark = pytest.mark.unit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.character import Character, CharacterRelation
from app.models.knowledge import (
    KnowledgeEntityCandidate,
    KnowledgeEventCandidate,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.timeline import TimelineEvent
from app.models.user import User
from app.services.knowledge.graph_sync import (
    GraphSyncConfig,
    knowledge_graph_sync_service,
)
from app.services.knowledge.projection import knowledge_projection_service


async def _context(
    db: AsyncSession,
    *,
    username: str = "kg_projection_owner",
    domain_profile: str = "fiction",
):
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
        metadata_json={"characters": ["刘备", "关羽"], "time_refs": ["190"]},
        word_count=18,
        embedding_status="embedded",
    )
    db.add(chunk)
    await db.flush()

    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="projection run",
        domain_profile=domain_profile,
        ontology_profile=f"{domain_profile}.v1",
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
    return user, novel, run


async def _entity(
    db: AsyncSession,
    *,
    user: User,
    novel: Novel,
    run: KnowledgeExtractionRun,
    name: str,
):
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
        status="accepted",
    )
    db.add(entity)
    await db.flush()
    return entity


async def _event(
    db: AsyncSession,
    *,
    user: User,
    novel: Novel,
    run: KnowledgeExtractionRun,
    title: str,
):
    event = KnowledgeEventCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        title=title,
        summary=f"{title} summary",
        domain_profile="history",
        event_type="political",
        time_refs=["190"],
        location_refs=["洛阳"],
        participant_refs=["刘备", "关羽"],
        evidence_refs=["ev-chunk-1"],
        source_refs=["ev-chunk-1"],
        confidence=0.88,
        status="accepted",
    )
    db.add(event)
    await db.flush()
    return event


async def _accepted_judgment(
    db: AsyncSession,
    *,
    user: User,
    novel: Novel,
    run: KnowledgeExtractionRun,
    domain_profile: str,
    relation_type: str,
    source_kind: str,
    source_id: int,
    target_kind: str,
    target_id: int,
):
    candidate = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile=domain_profile,
        relation_type=relation_type,
        source_kind=source_kind,
        source_id=source_id,
        target_kind=target_kind,
        target_id=target_id,
        recall_signals={"adjacency": {"same_chapter": True}},
        package_snapshot={"allowed_evidence_ids": ["ev-chunk-1"]},
        evidence_refs=["ev-chunk-1"],
        status="accepted",
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
        confidence=0.92,
        evidence_refs=["ev-chunk-1"],
        rationale="Accepted fixture.",
        risk_flags=[],
        raw_output={},
        structured_output={},
        status="accepted",
        gate_status="accepted",
    )
    db.add(judgment)
    await db.flush()
    return candidate, judgment


@pytest.mark.asyncio
async def test_fiction_projection_is_idempotent_for_character_relations(
    db_session: AsyncSession,
):
    user, novel, run = await _context(db_session)
    source = await _entity(db_session, user=user, novel=novel, run=run, name="刘备")
    target = await _entity(db_session, user=user, novel=novel, run=run, name="关羽")
    _, judgment = await _accepted_judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        domain_profile="fiction",
        relation_type="ally",
        source_kind="entity_candidate",
        source_id=source.id,
        target_kind="entity_candidate",
        target_id=target.id,
    )

    first = await knowledge_projection_service.project_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    second = await knowledge_projection_service.project_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    relation_count = (
        await db_session.execute(select(func.count()).select_from(CharacterRelation))
    ).scalar_one()
    character_count = (
        await db_session.execute(select(func.count()).select_from(Character))
    ).scalar_one()
    relation = (await db_session.execute(select(CharacterRelation))).scalar_one()

    assert first.status == "projected"
    assert second.status == "projected"
    assert first.character_relation_ids == second.character_relation_ids
    assert relation_count == 1
    assert character_count == 2
    assert relation.relation_type == "ally"
    assert f"kg_judgment_id={judgment.id}" in relation.description
    assert "ev-chunk-1" in relation.description


@pytest.mark.asyncio
async def test_text_chunk_relation_is_not_projected_without_entity_resolution(
    db_session: AsyncSession,
):
    user, novel, run = await _context(db_session)
    _, judgment = await _accepted_judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        domain_profile="fiction",
        relation_type="precedes",
        source_kind="text_chunk",
        source_id=1,
        target_kind="text_chunk",
        target_id=2,
    )

    result = await knowledge_projection_service.project_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    relation_count = (
        await db_session.execute(select(func.count()).select_from(CharacterRelation))
    ).scalar_one()

    assert result.status == "skipped"
    assert result.reason == "insufficient_entity_resolution"
    assert relation_count == 0


@pytest.mark.asyncio
async def test_history_projection_is_idempotent_for_timeline_events(
    db_session: AsyncSession,
):
    user, novel, run = await _context(db_session, domain_profile="history")
    source = await _event(db_session, user=user, novel=novel, run=run, title="结义")
    target = await _event(db_session, user=user, novel=novel, run=run, title="起兵")
    _, judgment = await _accepted_judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        domain_profile="history",
        relation_type="preceded",
        source_kind="event_candidate",
        source_id=source.id,
        target_kind="event_candidate",
        target_id=target.id,
    )

    first = await knowledge_projection_service.project_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    second = await knowledge_projection_service.project_judgment(
        db_session,
        judgment_id=judgment.id,
    )
    event_count = (
        await db_session.execute(select(func.count()).select_from(TimelineEvent))
    ).scalar_one()
    events = (
        (
            await db_session.execute(
                select(TimelineEvent).order_by(TimelineEvent.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )

    assert first.status == "projected"
    assert second.status == "projected"
    assert first.timeline_event_ids == second.timeline_event_ids
    assert event_count == 2
    assert [event.event_title for event in events] == ["结义", "起兵"]
    assert f"kg_judgment_id={judgment.id}" in events[0].event_description
    assert events[0].time_reference == "190"


@pytest.mark.asyncio
async def test_neo4j_disabled_sync_reads_accepted_rows_without_state_change(
    db_session: AsyncSession,
):
    user, novel, run = await _context(db_session)
    source = await _entity(db_session, user=user, novel=novel, run=run, name="刘备")
    target = await _entity(db_session, user=user, novel=novel, run=run, name="关羽")
    _, judgment = await _accepted_judgment(
        db_session,
        user=user,
        novel=novel,
        run=run,
        domain_profile="fiction",
        relation_type="ally",
        source_kind="entity_candidate",
        source_id=source.id,
        target_kind="entity_candidate",
        target_id=target.id,
    )

    result = await knowledge_graph_sync_service.sync_run(
        db_session,
        run_id=run.id,
        owner_id=user.id,
        config=GraphSyncConfig(enabled=False),
    )
    persisted = await db_session.get(KnowledgeRelationJudgment, judgment.id)

    assert result.status == "skipped"
    assert result.reason == "neo4j_sync_disabled"
    assert result.accepted_rows_seen == 1
    assert persisted.status == "accepted"
    assert persisted.gate_status == "accepted"
