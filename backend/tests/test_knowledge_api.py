"""Knowledge API authentication and owner-isolation tests."""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User


async def _register_and_login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "correct-horse-123",
        },
    )
    assert response.status_code == 201
    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "correct-horse-123"},
    )
    assert response.status_code == 200
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


async def _user(db: AsyncSession, username: str) -> User:
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one()


async def _create_knowledge_records(db: AsyncSession, owner: User):
    novel = Novel(title=f"{owner.username} graph novel", owner_id=owner.id)
    db.add(novel)
    await db.flush()

    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="刘备与关羽结义。",
        word_count=9,
    )
    db.add(chapter)
    await db.flush()

    chunk = TextChunk(
        novel_id=novel.id,
        chapter_id=chapter.id,
        chunk_index=0,
        content="刘备与关羽结义。",
        chunk_type="narration",
        metadata_json={"characters": ["刘备", "关羽"]},
        word_count=9,
        embedding_status="embedded",
    )
    db.add(chunk)
    await db.flush()

    run = KnowledgeExtractionRun(
        owner_id=owner.id,
        novel_id=novel.id,
        run_name="api graph run",
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="running",
    )
    db.add(run)
    await db.flush()

    evidence = KnowledgeEvidenceRef(
        owner_id=owner.id,
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

    candidate = KnowledgeRelationCandidate(
        owner_id=owner.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="fiction",
        relation_type="ally",
        source_kind="text_chunk",
        source_id=chunk.id,
        target_kind="text_chunk",
        target_id=chunk.id,
        recall_signals={"adjacency": {"same_chapter": True}},
        package_snapshot={"allowed_evidence_ids": ["ev-chunk-1"]},
        evidence_refs=["ev-chunk-1"],
        status="proposed",
    )
    db.add(candidate)
    await db.flush()

    judgment = KnowledgeRelationJudgment(
        owner_id=owner.id,
        novel_id=novel.id,
        run_id=run.id,
        relation_candidate_id=candidate.id,
        prompt_version="knowledge-relation-judge.v1",
        model_name="test/model",
        relation_type="ally",
        confidence=0.61,
        evidence_refs=["ev-chunk-1"],
        rationale="Weak but evidence-backed relation.",
        risk_flags=["ambiguous_direction"],
        raw_output={},
        structured_output={},
        status="pending",
        gate_status="evidence_passed",
    )
    db.add(judgment)
    await db.flush()
    return novel, run, candidate, judgment


@pytest.mark.asyncio
async def test_knowledge_endpoints_require_authentication(client: AsyncClient):
    for method, path in [
        ("GET", "/api/knowledge/runs"),
        ("POST", "/api/knowledge/runs"),
        ("GET", "/api/knowledge/novels/1/graph"),
    ]:
        response = await client.request(method, path, json={})
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_knowledge_run_start_and_review_flow(
    client: AsyncClient, db_session: AsyncSession
):
    await _register_and_login(client, "kgapiowner")
    owner = await _user(db_session, "kgapiowner")
    novel, run, _, judgment = await _create_knowledge_records(db_session, owner)

    response = await client.post(
        "/api/knowledge/runs",
        json={
            "novel_id": novel.id,
            "run_name": "created from api",
            "domain_profile": "fiction",
            "ontology_profile": "fiction.v1",
        },
    )
    assert response.status_code == 200
    assert response.json()["owner_id"] == owner.id

    assert len((await client.get("/api/knowledge/runs")).json()) == 2
    assert (
        len((await client.get(f"/api/knowledge/runs/{run.id}/candidates")).json()) == 1
    )
    assert (
        len((await client.get(f"/api/knowledge/runs/{run.id}/judgments")).json()) == 1
    )

    response = await client.post(f"/api/knowledge/runs/{run.id}/gate")
    assert response.status_code == 200
    assert response.json()["needs_human_review"] == 1
    assert len((await client.get(f"/api/knowledge/runs/{run.id}/review")).json()) == 1

    response = await client.post(
        f"/api/knowledge/judgments/{judgment.id}/accept",
        json={"reviewer_notes": "accepted after review"},
    )
    assert response.status_code == 200
    assert response.json()["decision"]["status"] == "accepted"
    assert response.json()["projection"]["status"] == "skipped"

    graph = (await client.get(f"/api/knowledge/novels/{novel.id}/graph")).json()
    assert graph["accepted_judgments"][0]["id"] == judgment.id


@pytest.mark.asyncio
async def test_knowledge_resources_are_isolated_by_owner(
    client: AsyncClient,
    db_session: AsyncSession,
):
    await _register_and_login(client, "kgapiprivate")
    owner = await _user(db_session, "kgapiprivate")
    novel, run, _, judgment = await _create_knowledge_records(db_session, owner)

    await _register_and_login(client, "kgapiother")

    assert (await client.get("/api/knowledge/runs")).json() == []
    assert (
        await client.get(f"/api/knowledge/runs/{run.id}/candidates")
    ).status_code == 404
    assert (
        await client.get(f"/api/knowledge/runs/{run.id}/judgments")
    ).status_code == 404
    assert (await client.get(f"/api/knowledge/runs/{run.id}/review")).status_code == 404
    assert (await client.post(f"/api/knowledge/runs/{run.id}/gate")).status_code == 404
    assert (
        await client.post(f"/api/knowledge/judgments/{judgment.id}/accept")
    ).status_code == 404
    assert (
        await client.post(f"/api/knowledge/judgments/{judgment.id}/reject")
    ).status_code == 404
    assert (
        await client.get(f"/api/knowledge/novels/{novel.id}/graph")
    ).status_code == 404
