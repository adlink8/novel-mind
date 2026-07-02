"""
Knowledge graph contract tests.

These tests use the existing SQLite fixture to validate ORM relationships,
JSON persistence, strict Pydantic schemas, and evidence-first constraints.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import Base
from app.models.knowledge import (
    RELATION_TYPES_BY_DOMAIN_PROFILE,
    KnowledgeEntityCandidate,
    KnowledgeEventCandidate,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
    KnowledgeReviewQueue,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeLLMRelationJudgmentOutput,
    KnowledgeRelationCandidateCreate,
)


async def _create_context(db_session: AsyncSession):
    user = User(
        username="kg_owner",
        email="kg_owner@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()

    novel = Novel(title="证据门控测试文本", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()

    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="刘备与关羽结义，随后共同起兵。",
        word_count=18,
    )
    db_session.add(chapter)
    await db_session.flush()

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
    db_session.add(chunk)
    await db_session.flush()

    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="kg contract run",
        domain_profile="history",
        ontology_profile="history.v1",
        status="running",
        config_snapshot={"top_k": 5},
    )
    db_session.add(run)
    await db_session.flush()

    evidence = KnowledgeEvidenceRef(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        ref_key="ev-1",
        source_type="text_chunk",
        text_chunk_id=chunk.id,
        chapter_id=chapter.id,
        source_locator={"chunk_index": 0},
        excerpt="刘备与关羽结义",
        char_start=0,
        char_end=8,
        metadata_json={"source": "fixture"},
    )
    db_session.add(evidence)
    await db_session.flush()

    return user, novel, chapter, chunk, run, evidence


@pytest.mark.asyncio
async def test_knowledge_contracts_persist_relationships_and_json(
    db_session: AsyncSession,
):
    user, novel, _, _, run, evidence = await _create_context(db_session)

    source = KnowledgeEntityCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        canonical_name="刘备",
        aliases=["玄德"],
        domain_profile="history",
        entity_type="person",
        evidence_refs=[evidence.ref_key],
        source_refs=[evidence.ref_key],
        confidence=0.91,
    )
    target = KnowledgeEntityCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        canonical_name="关羽",
        aliases=["云长"],
        domain_profile="history",
        entity_type="person",
        evidence_refs=[evidence.ref_key],
        source_refs=[evidence.ref_key],
        confidence=0.89,
    )
    event = KnowledgeEventCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        title="桃园结义",
        summary="刘备与关羽结成同盟。",
        domain_profile="history",
        event_type="political",
        participant_refs=["刘备", "关羽"],
        evidence_refs=[evidence.ref_key],
        source_refs=[evidence.ref_key],
    )
    db_session.add_all([source, target, event])
    await db_session.flush()

    relation = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="history",
        relation_type="allied_with",
        source_kind="entity_candidate",
        source_id=source.id,
        target_kind="entity_candidate",
        target_id=target.id,
        recall_signals={"same_chunk": True, "bm25_rank": 1},
        package_snapshot={"allowed_evidence_refs": [evidence.ref_key]},
        evidence_refs=[evidence.ref_key],
        status="proposed",
    )
    db_session.add(relation)
    await db_session.flush()

    judgment = KnowledgeRelationJudgment(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=relation.id,
        prompt_version="kg-relation-v1",
        model_name="test/provider-model",
        relation_type="allied_with",
        confidence=0.83,
        evidence_refs=[evidence.ref_key],
        rationale="Evidence states they joined together.",
        risk_flags=[],
        raw_output={"relation_type": "allied_with"},
        structured_output={"confidence": 0.83},
        status="needs_human_review",
        gate_status="evidence_passed",
        needs_human_review=True,
    )
    db_session.add(judgment)
    await db_session.flush()

    review = KnowledgeReviewQueue(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=relation.id,
        judgment_id=judgment.id,
        review_type="low_confidence",
        reason="Confidence below auto-accept threshold.",
        evidence_refs=[evidence.ref_key],
    )
    db_session.add(review)
    await db_session.commit()

    persisted_run = (
        await db_session.execute(
            select(KnowledgeExtractionRun)
            .options(
                selectinload(KnowledgeExtractionRun.evidence_refs),
                selectinload(KnowledgeExtractionRun.entity_candidates),
                selectinload(KnowledgeExtractionRun.event_candidates),
                selectinload(KnowledgeExtractionRun.relation_candidates),
                selectinload(KnowledgeExtractionRun.judgments),
                selectinload(KnowledgeExtractionRun.review_items),
            )
            .where(KnowledgeExtractionRun.id == run.id)
        )
    ).scalar_one()
    assert persisted_run is not None
    assert persisted_run.domain_profile == "history"
    assert persisted_run.config_snapshot["top_k"] == 5
    assert len(persisted_run.evidence_refs) == 1
    assert len(persisted_run.entity_candidates) == 2
    assert len(persisted_run.event_candidates) == 1
    assert len(persisted_run.relation_candidates) == 1
    assert len(persisted_run.judgments) == 1
    assert len(persisted_run.review_items) == 1

    persisted_relation = (
        await db_session.execute(
            select(KnowledgeRelationCandidate)
            .options(
                selectinload(KnowledgeRelationCandidate.judgments),
                selectinload(KnowledgeRelationCandidate.review_items),
            )
            .where(KnowledgeRelationCandidate.id == relation.id)
        )
    ).scalar_one()
    assert persisted_relation is not None
    assert persisted_relation.recall_signals["same_chunk"] is True
    assert persisted_relation.package_snapshot["allowed_evidence_refs"] == ["ev-1"]
    assert persisted_relation.evidence_refs == ["ev-1"]
    assert persisted_relation.judgments[0].gate_status == "evidence_passed"
    assert persisted_relation.review_items[0].status == "open"


@pytest.mark.asyncio
async def test_knowledge_contracts_cascade_when_novel_is_deleted(
    db_session: AsyncSession,
):
    _, novel, _, _, run, evidence = await _create_context(db_session)
    run_id = run.id
    evidence_id = evidence.id

    await db_session.delete(novel)
    await db_session.commit()
    db_session.expunge_all()

    assert await db_session.get(KnowledgeExtractionRun, run_id) is None
    assert await db_session.get(KnowledgeEvidenceRef, evidence_id) is None


def test_knowledge_llm_output_is_strict_and_requires_evidence_refs():
    valid = KnowledgeLLMRelationJudgmentOutput.model_validate(
        {
            "candidate_id": 1,
            "relation_type": "allied_with",
            "confidence": 0.75,
            "evidence_refs": ["ev-1"],
            "rationale": "The cited chunk supports the relation.",
            "risk_flags": [],
            "needs_human_review": False,
        }
    )
    assert valid.evidence_refs == ["ev-1"]

    with pytest.raises(ValidationError):
        KnowledgeLLMRelationJudgmentOutput.model_validate(
            {
                "candidate_id": 1,
                "relation_type": "allied_with",
                "confidence": 0.75,
                "evidence_refs": [],
                "rationale": "No evidence should fail.",
            }
        )

    with pytest.raises(ValidationError):
        KnowledgeLLMRelationJudgmentOutput.model_validate(
            {
                "candidate_id": 1,
                "relation_type": "allied_with",
                "confidence": 0.75,
                "evidence_refs": ["ev-1"],
                "rationale": "Extra model claims are rejected.",
                "unsupported_claim": "not allowed",
            }
        )


def test_relation_candidate_schema_requires_real_evidence_refs():
    with pytest.raises(ValidationError):
        KnowledgeRelationCandidateCreate(
            owner_id=1,
            novel_id=1,
            run_id=1,
            relation_type="allied_with",
            source_kind="entity_candidate",
            source_id=1,
            target_kind="entity_candidate",
            target_id=2,
            evidence_refs=[],
        )

    candidate = KnowledgeRelationCandidateCreate(
        owner_id=1,
        novel_id=1,
        run_id=1,
        domain_profile="history",
        relation_type="allied_with",
        source_kind="entity_candidate",
        source_id=1,
        target_kind="entity_candidate",
        target_id=2,
        recall_signals={"vector_rank": 3},
        package_snapshot={"allowed_evidence_refs": ["ev-1"]},
        evidence_refs=["ev-1"],
    )
    assert candidate.domain_profile == "history"
    assert candidate.evidence_refs == ["ev-1"]


def test_knowledge_metadata_has_audit_tables_but_no_accepted_graph_table():
    tables = Base.metadata.tables
    assert "knowledge_extraction_runs" in tables
    assert "knowledge_relation_candidates" in tables
    assert "knowledge_relation_judgments" in tables
    assert "knowledge_evidence_refs" in tables
    assert "knowledge_graph_edges" not in tables
    assert "knowledge_accepted_relations" not in tables

    assert "evidence_refs" in KnowledgeEntityCandidate.__table__.c
    assert "evidence_refs" in KnowledgeEventCandidate.__table__.c
    assert "evidence_refs" in KnowledgeRelationCandidate.__table__.c
    assert "evidence_refs" in KnowledgeRelationJudgment.__table__.c
    assert KnowledgeRelationCandidate.__table__.c.evidence_refs.nullable is False
    assert KnowledgeRelationJudgment.__table__.c.evidence_refs.nullable is False


def test_knowledge_indexes_cover_required_review_and_gate_queries():
    index_names = {
        index.name
        for table_name in (
            "knowledge_extraction_runs",
            "knowledge_relation_candidates",
            "knowledge_relation_judgments",
        )
        for index in Base.metadata.tables[table_name].indexes
    }

    assert "idx_knowledge_runs_domain_profile" in index_names
    assert "idx_knowledge_rel_candidates_run_id" in index_names
    assert "idx_knowledge_rel_candidates_status" in index_names
    assert "idx_knowledge_rel_candidates_relation_type" in index_names
    assert "idx_knowledge_judgments_gate_status" in index_names
    assert "idx_knowledge_judgments_relation_type" in index_names


@pytest.mark.asyncio
async def test_evidence_ref_keys_are_unique_per_run(db_session: AsyncSession):
    user, novel, chapter, chunk, run, _ = await _create_context(db_session)
    run_id = run.id
    await db_session.commit()

    duplicate = KnowledgeEvidenceRef(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        ref_key="ev-1",
        source_type="text_chunk",
        text_chunk_id=chunk.id,
        chapter_id=chapter.id,
    )
    db_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()
    existing_refs = (
        await db_session.execute(select(KnowledgeEvidenceRef).where(
            KnowledgeEvidenceRef.run_id == run_id
        ))
    ).scalars().all()
    assert [ref.ref_key for ref in existing_refs] == ["ev-1"]


def test_ontology_profiles_separate_fiction_and_history_relation_types():
    assert "romantic" in RELATION_TYPES_BY_DOMAIN_PROFILE["fiction"]
    assert "allied_with" in RELATION_TYPES_BY_DOMAIN_PROFILE["history"]
    assert "romantic" not in RELATION_TYPES_BY_DOMAIN_PROFILE["history"]
