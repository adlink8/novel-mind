"""Deterministic accepted-source snapshot tests."""

from dataclasses import replace

import pytest

pytestmark = pytest.mark.unit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.knowledge_unit import (
    NarrativeSourceSnapshot,
    NarrativeSourceSnapshotItem,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.knowledge_units.source_snapshot import (
    InvalidSourceLineageError,
    MovingSourceInputsError,
    NoAcceptedJudgmentsError,
    SourceSnapshotService,
    source_snapshot_service,
)


async def _source_context(
    db: AsyncSession,
    *,
    username: str = "snapshot_owner",
    accepted: bool = True,
    evidence_source_type: str = "text_chunk",
) -> tuple[
    User,
    Novel,
    TextChunk,
    KnowledgeEvidenceRef,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
]:
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
        content=chapter.content,
        chunk_type="narration",
        word_count=18,
        embedding_status="embedded",
    )
    db.add(chunk)
    await db.flush()
    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="snapshot source",
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
        source_type=evidence_source_type,
        text_chunk_id=chunk.id if evidence_source_type == "text_chunk" else None,
        chapter_id=chapter.id if evidence_source_type != "accepted_relation" else None,
        accepted_relation_id=99
        if evidence_source_type == "accepted_relation"
        else None,
        excerpt="刘备与关羽结义",
        source_locator={"chapter": 1},
    )
    db.add(evidence)
    await db.flush()
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
        recall_signals={"adjacency": True},
        package_snapshot={"allowed_evidence_refs": ["ev-1"]},
        evidence_refs=["ev-1"],
        status="accepted" if accepted else "candidate",
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
        confidence=0.92,
        evidence_refs=["ev-1"],
        raw_output={"audit_only": True},
        structured_output={"relation_type": "ally"},
        status="accepted" if accepted else "rejected",
        gate_status="accepted" if accepted else "rejected",
    )
    db.add(judgment)
    await db.flush()
    return user, novel, chunk, evidence, candidate, judgment


@pytest.mark.asyncio
async def test_same_accepted_set_returns_same_snapshot(
    db_session: AsyncSession,
) -> None:
    user, novel, _, _, _, _ = await _source_context(db_session)
    first = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    second = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    count = await db_session.scalar(
        select(func.count()).select_from(NarrativeSourceSnapshot)
    )
    item = await db_session.scalar(select(NarrativeSourceSnapshotItem))

    assert first.id == second.id
    assert first.manifest_checksum == second.manifest_checksum
    assert first.source_watermark == second.source_watermark
    assert count == 1
    assert item is not None
    assert item.source_judgment_id > 0
    assert item.evidence_manifest[0]["source_content"]


@pytest.mark.asyncio
async def test_source_content_change_produces_new_checksum(
    db_session: AsyncSession,
) -> None:
    user, novel, chunk, _, _, _ = await _source_context(db_session)
    first = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    chunk.content = "刘备与关羽结义，证据文本发生变化。"
    await db_session.flush()
    second = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    assert second.id != first.id
    assert second.manifest_checksum != first.manifest_checksum


@pytest.mark.asyncio
async def test_raw_llm_audit_change_does_not_change_snapshot_truth(
    db_session: AsyncSession,
) -> None:
    user, novel, _, _, _, judgment = await _source_context(db_session)
    first = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    judgment.raw_output = {"audit_only": "changed"}
    judgment.rationale = "Audit explanation changed."
    await db_session.flush()
    second = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    assert second.id == first.id
    assert second.manifest_checksum == first.manifest_checksum
    assert second.source_watermark == first.source_watermark


@pytest.mark.asyncio
async def test_owner_scope_does_not_mix_accepted_sets(db_session: AsyncSession) -> None:
    user_a, novel_a, _, _, _, _ = await _source_context(
        db_session, username="snapshot_owner_a"
    )
    await _source_context(db_session, username="snapshot_owner_b")
    snapshot = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user_a.id,
        novel_id=novel_a.id,
        domain_profile="fiction",
    )
    items = (
        (
            await db_session.execute(
                select(NarrativeSourceSnapshotItem).where(
                    NarrativeSourceSnapshotItem.snapshot_id == snapshot.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(items) == 1
    assert all(item.owner_id == user_a.id for item in items)


@pytest.mark.asyncio
async def test_missing_evidence_row_is_rejected(db_session: AsyncSession) -> None:
    user, novel, _, evidence, _, _ = await _source_context(db_session)
    await db_session.delete(evidence)
    await db_session.flush()
    with pytest.raises(InvalidSourceLineageError, match="missing evidence rows"):
        await source_snapshot_service.create_snapshot(
            db_session,
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
        )


@pytest.mark.asyncio
async def test_projected_graph_evidence_is_rejected(db_session: AsyncSession) -> None:
    user, novel, _, _, _, _ = await _source_context(
        db_session, evidence_source_type="accepted_relation"
    )
    with pytest.raises(InvalidSourceLineageError, match="projected graph row"):
        await source_snapshot_service.create_snapshot(
            db_session,
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
        )


@pytest.mark.asyncio
async def test_candidate_without_accepted_judgment_is_rejected(
    db_session: AsyncSession,
) -> None:
    user, novel, _, _, candidate, judgment = await _source_context(db_session)
    judgment.status = "rejected"
    judgment.gate_status = "rejected"
    await db_session.flush()
    assert candidate.status == "accepted"
    with pytest.raises(InvalidSourceLineageError, match="no doubly accepted judgment"):
        await source_snapshot_service.create_snapshot(
            db_session,
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
        )


@pytest.mark.asyncio
async def test_nonaccepted_judgments_cannot_seed_snapshot(
    db_session: AsyncSession,
) -> None:
    user, novel, _, _, _, _ = await _source_context(db_session, accepted=False)
    with pytest.raises(NoAcceptedJudgmentsError):
        await source_snapshot_service.create_snapshot(
            db_session,
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
        )


@pytest.mark.asyncio
async def test_cross_owner_evidence_is_rejected(db_session: AsyncSession) -> None:
    user_a, novel_a, _, evidence_a, _, _ = await _source_context(
        db_session, username="lineage_owner_a"
    )
    user_b, novel_b, _, _, _, _ = await _source_context(
        db_session, username="lineage_owner_b"
    )
    evidence_a.owner_id = user_b.id
    evidence_a.novel_id = novel_b.id
    await db_session.flush()
    with pytest.raises(InvalidSourceLineageError, match="outside owner/work scope"):
        await source_snapshot_service.create_snapshot(
            db_session,
            owner_id=user_a.id,
            novel_id=novel_a.id,
            domain_profile="fiction",
        )


@pytest.mark.asyncio
async def test_moving_inputs_abort_before_snapshot_write(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, novel, _, _, _, _ = await _source_context(db_session)
    service = SourceSnapshotService()
    original = service._build_manifest
    calls = 0

    async def moving_manifest(*args, **kwargs):
        nonlocal calls
        calls += 1
        manifest = await original(*args, **kwargs)
        if calls == 2:
            return replace(manifest, source_watermark="f" * 64)
        return manifest

    monkeypatch.setattr(service, "_build_manifest", moving_manifest)
    with pytest.raises(MovingSourceInputsError):
        await service.create_snapshot(
            db_session,
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
        )
    count = await db_session.scalar(
        select(func.count()).select_from(NarrativeSourceSnapshot)
    )
    assert count == 0


@pytest.mark.asyncio
async def test_persisted_snapshot_is_immutable(db_session: AsyncSession) -> None:
    user, novel, _, _, _, _ = await _source_context(db_session)
    snapshot = await source_snapshot_service.create_snapshot(
        db_session,
        owner_id=user.id,
        novel_id=novel.id,
        domain_profile="fiction",
    )
    snapshot.status = "changed"
    with pytest.raises(ValueError, match="immutable"):
        await db_session.flush()
