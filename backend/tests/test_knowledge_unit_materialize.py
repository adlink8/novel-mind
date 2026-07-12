"""Narrative unit materialization tests."""

import pytest

pytestmark = pytest.mark.unit

from sqlalchemy import select

from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.knowledge_unit import NarrativeUnit, NarrativeUnitEvidenceLink
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.knowledge_units.materialize import narrative_unit_materializer
from app.services.knowledge_units.source_snapshot import source_snapshot_service


async def _accepted_source(db, *, risk_flags=None):
    user = User(username="unit_owner", email="unit@example.com", hashed_password="x")
    db.add(user)
    await db.flush()
    novel = Novel(owner_id=user.id, title="Unit novel")
    db.add(novel)
    await db.flush()
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="One", content="甲帮助乙。", word_count=5)
    db.add(chapter)
    await db.flush()
    chunk = TextChunk(novel_id=novel.id, chapter_id=chapter.id, chunk_index=0, content=chapter.content, chunk_type="narration", word_count=5, embedding_status="embedded")
    db.add(chunk)
    await db.flush()
    run = KnowledgeExtractionRun(owner_id=user.id, novel_id=novel.id, run_name="run", status="completed", domain_profile="fiction", ontology_profile="fiction.v1")
    db.add(run)
    await db.flush()
    evidence = KnowledgeEvidenceRef(owner_id=user.id, novel_id=novel.id, run_id=run.id, ref_key="ev-1", source_type="text_chunk", text_chunk_id=chunk.id, chapter_id=chapter.id, excerpt=chapter.content)
    db.add(evidence)
    await db.flush()
    candidate = KnowledgeRelationCandidate(owner_id=user.id, novel_id=novel.id, run_id=run.id, domain_profile="fiction", relation_type="supports", source_kind="entity_candidate", source_id=1, target_kind="entity_candidate", target_id=2, recall_signals={}, package_snapshot={}, evidence_refs=["ev-1"], status="accepted")
    db.add(candidate)
    await db.flush()
    judgment = KnowledgeRelationJudgment(owner_id=user.id, novel_id=novel.id, run_id=run.id, relation_candidate_id=candidate.id, prompt_version="v1", model_name="test", relation_type="supports", confidence=0.9, evidence_refs=["ev-1"], structured_output={}, raw_output={}, status="accepted", gate_status="accepted", risk_flags=risk_flags or [])
    db.add(judgment)
    await db.flush()
    snapshot = await source_snapshot_service.create_snapshot(db, owner_id=user.id, novel_id=novel.id, domain_profile="fiction")
    return snapshot


async def test_materializes_grounded_unit_and_links(db_session):
    snapshot = await _accepted_source(db_session)
    report = await narrative_unit_materializer.materialize_snapshot(db_session, snapshot_id=snapshot.id)
    unit = await db_session.scalar(select(NarrativeUnit))
    link = await db_session.scalar(select(NarrativeUnitEvidenceLink))
    assert report.created == 1
    assert unit is not None and unit.evidence_count == 1
    assert unit.question and unit.answer and unit.lifecycle_status == "current"
    assert link is not None and link.unit_id == unit.id


async def test_materialization_is_idempotent(db_session):
    snapshot = await _accepted_source(db_session)
    first = await narrative_unit_materializer.materialize_snapshot(db_session, snapshot_id=snapshot.id)
    second = await narrative_unit_materializer.materialize_snapshot(db_session, snapshot_id=snapshot.id)
    assert first.created == 1
    assert second.created == 0 and second.reused == 1


async def test_risk_flag_materializes_disputed(db_session):
    snapshot = await _accepted_source(db_session, risk_flags=["conflict"])
    await narrative_unit_materializer.materialize_snapshot(db_session, snapshot_id=snapshot.id)
    unit = await db_session.scalar(select(NarrativeUnit))
    assert unit is not None and unit.lifecycle_status == "disputed"


async def test_dry_run_does_not_write(db_session):
    snapshot = await _accepted_source(db_session)
    report = await narrative_unit_materializer.materialize_snapshot(db_session, snapshot_id=snapshot.id, write=False)
    assert report.created == 1
    assert await db_session.scalar(select(NarrativeUnit)) is None
