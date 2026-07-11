"""Narrative knowledge-unit ORM and strict-schema contract tests."""

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base
from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.knowledge_unit import (
    NARRATIVE_BUILD_STATUSES,
    NARRATIVE_UNIT_STATUSES,
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
    NarrativeUnit,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.schemas.knowledge_unit import NarrativeUnitCreate


async def _accepted_lineage(
    db: AsyncSession,
    *,
    username: str,
) -> tuple[User, Novel, KnowledgeRelationCandidate, KnowledgeRelationJudgment, KnowledgeEvidenceRef]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password="hash",
    )
    db.add(user)
    await db.flush()
    novel = Novel(title=f"{username} work", owner_id=user.id)
    db.add(novel)
    await db.flush()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="刘备与关羽共同起兵。",
        word_count=11,
    )
    db.add(chapter)
    await db.flush()
    chunk = TextChunk(
        novel_id=novel.id,
        chapter_id=chapter.id,
        chunk_index=0,
        content=chapter.content,
        chunk_type="narration",
        word_count=11,
        embedding_status="embedded",
    )
    db.add(chunk)
    await db.flush()
    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="accepted source",
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="completed",
    )
    db.add(run)
    await db.flush()
    evidence = KnowledgeEvidenceRef(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        ref_key="ev-1",
        source_type="text_chunk",
        text_chunk_id=chunk.id,
        chapter_id=chapter.id,
        excerpt=chapter.content,
    )
    db.add(evidence)
    candidate = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="fiction",
        relation_type="ally",
        source_kind="text_chunk",
        source_id=chunk.id,
        target_kind="text_chunk",
        target_id=chunk.id,
        evidence_refs=["ev-1"],
        status="accepted",
    )
    db.add(candidate)
    await db.flush()
    judgment = KnowledgeRelationJudgment(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=candidate.id,
        prompt_version="judge.v1",
        model_name="test/model",
        relation_type="ally",
        confidence=0.9,
        evidence_refs=["ev-1"],
        status="accepted",
        gate_status="accepted",
    )
    db.add(judgment)
    await db.flush()
    return user, novel, candidate, judgment, evidence


def test_metadata_exposes_all_postgres_truth_contracts() -> None:
    tables = Base.metadata.tables
    assert {
        "narrative_source_snapshots",
        "narrative_source_snapshot_items",
        "narrative_units",
        "narrative_unit_evidence_links",
        "narrative_index_builds",
        "narrative_active_pointers",
        "narrative_promotion_journals",
    } <= set(tables)
    assert "raw_output" not in tables["narrative_units"].c
    assert "rationale" not in tables["narrative_units"].c
    assert tables["narrative_units"].c.source_judgment_id.nullable is False
    assert tables["narrative_units"].c.primary_evidence_id.nullable is False


def test_status_contracts_cover_publication_and_rollback_states() -> None:
    required = {"draft", "candidate", "active", "failed", "deprecated", "rolled_back"}
    assert required == set(NARRATIVE_UNIT_STATUSES)
    assert required == set(NARRATIVE_BUILD_STATUSES)


def test_unit_schema_requires_evidence_and_forbids_raw_llm_audit() -> None:
    payload = {
        "owner_id": 1,
        "novel_id": 1,
        "source_snapshot_id": 1,
        "source_judgment_id": 1,
        "source_candidate_id": 1,
        "domain_profile": "fiction",
        "ontology_profile": "fiction.v1",
        "subject_key": "character:刘备",
        "relation_type": "ally",
        "question": "刘备的盟友是谁？",
        "answer": "关羽。",
        "confidence": 0.9,
        "content_hash": "a" * 64,
        "evidence": [],
    }
    with pytest.raises(ValidationError):
        NarrativeUnitCreate.model_validate(payload)

    payload["evidence"] = [
        {"source_evidence_id": 1, "ref_key": "ev-1", "content_hash": "b" * 64}
    ]
    payload["raw_output"] = {"unsupported": True}
    with pytest.raises(ValidationError):
        NarrativeUnitCreate.model_validate(payload)


@pytest.mark.asyncio
async def test_snapshot_item_rejects_cross_owner_judgment(
    db_session: AsyncSession,
) -> None:
    _, _, candidate_a, judgment_a, _ = await _accepted_lineage(
        db_session, username="unit_owner_a"
    )
    user_b, novel_b, _, _, _ = await _accepted_lineage(
        db_session, username="unit_owner_b"
    )
    snapshot = NarrativeSourceSnapshot(
        owner_id=user_b.id,
        novel_id=novel_b.id,
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="frozen",
        source_watermark="watermark",
        manifest_checksum="a" * 64,
        item_count=1,
    )
    db_session.add(snapshot)
    await db_session.flush()
    db_session.add(
        NarrativeSourceSnapshotItem(
            owner_id=user_b.id,
            novel_id=novel_b.id,
            snapshot_id=snapshot.id,
            source_judgment_id=judgment_a.id,
            source_candidate_id=candidate_a.id,
            judgment_content_hash="b" * 64,
            candidate_content_hash="c" * 64,
            evidence_content_hash="d" * 64,
            item_content_hash="e" * 64,
            evidence_manifest=[{"ref_key": "ev-1"}],
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_unit_cannot_exist_without_primary_evidence(
    db_session: AsyncSession,
) -> None:
    user, novel, candidate, judgment, _ = await _accepted_lineage(
        db_session, username="unit_lineage_owner"
    )
    snapshot = NarrativeSourceSnapshot(
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="frozen",
        source_watermark="watermark",
        manifest_checksum="a" * 64,
        item_count=1,
    )
    db_session.add(snapshot)
    await db_session.flush()
    db_session.add(
        NarrativeUnit(
            owner_id=user.id,
            novel_id=novel.id,
            source_snapshot_id=snapshot.id,
            source_judgment_id=judgment.id,
            source_candidate_id=candidate.id,
            primary_evidence_id=None,
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            subject_key="character:刘备",
            relation_type="ally",
            question="刘备的盟友是谁？",
            answer="关羽。",
            confidence=0.9,
            evidence_count=1,
            content_hash="b" * 64,
            evidence_manifest_checksum="c" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
