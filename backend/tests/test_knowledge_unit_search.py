"""Narrative unit/chunk retrieval mode tests."""

from unittest.mock import AsyncMock, patch

from app.schemas.novel import SearchRequest
from app.services.knowledge_units.search import (
    NarrativeRetrievalStrategy,
    NarrativeSearchService,
    fuse_results,
    select_candidate_build,
)
from app.services.knowledge_units.indexing import NarrativeIndexingService
from tests.test_knowledge_unit_indexing import FakeStore, _candidate_build


def test_search_request_defaults_to_chunks():
    assert SearchRequest(query="test").mode == "chunks"


def test_fusion_keeps_source_identity_and_rank():
    chunks = [{"chunk_id": 1, "score": 0.8, "content_snippet": "raw"}]
    units = [{"unit_id": 2, "chunk_id": 1, "score": 1.0, "content_snippet": "fact"}]
    result = fuse_results(chunks, units, top_k=2, chunk_weight=0.4, unit_weight=0.6)
    assert [item["source_type"] for item in result] == ["unit", "chunk"]
    assert result[0]["score"] == 0.6


async def test_candidate_selection_preserves_owner_lifecycle_and_citations(db_session):
    build = await _candidate_build(db_session)
    store = FakeStore()

    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]

    await NarrativeIndexingService(store).build_candidate(
        db_session, build_id=build.id, embedder=embed
    )
    collection = store.get_named_collection(build.collection_name)

    def query(**kwargs):
        wrong_owner = dict(collection.metadatas[0], owner_id=build.owner_id + 1)
        return {
            "ids": [[collection.ids[0], "cross-owner"]],
            "metadatas": [[collection.metadatas[0], wrong_owner]],
            "distances": [[0.1, 0.0]],
        }

    collection.query = query

    class Embeddings:
        async def embedding(self, *, texts):
            return [[0.1, 0.2]]

    service = NarrativeSearchService(store=store, embeddings=Embeddings())
    rows = await service.search_units(
        db_session,
        owner_id=build.owner_id,
        novel_id=build.novel_id,
        domain_profile=build.domain_profile,
        query="关系",
        build_selector=select_candidate_build(build),
    )
    assert len(rows) == 1
    assert rows[0]["evidence_refs"]
    assert rows[0]["lifecycle"] == "current"

    from app.models.knowledge_unit import NarrativeUnit
    from sqlalchemy import select

    unit = await db_session.scalar(select(NarrativeUnit))
    unit.lifecycle_status = "deprecated"
    await db_session.flush()
    assert (
        await service.search_units(
            db_session,
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            query="关系",
            build_selector=select_candidate_build(build),
        )
        == []
    )


async def test_production_hybrid_falls_back_to_chunks():
    class Chunks:
        async def search_novel(self, *args, **kwargs):
            return [{"chunk_id": 1, "score": 0.7}]

    class Units:
        async def search_units(self, *args, **kwargs):
            return []

    rows = await NarrativeRetrievalStrategy(
        chunks=Chunks(), units=Units()
    ).search_novel(
        object(),
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        query="query",
        mode="hybrid",
        top_k=5,
    )
    assert rows == [{"chunk_id": 1, "score": 0.35, "source_type": "chunk"}]


async def test_units_api_uses_unit_strategy(auth_client):
    import io

    response = await auth_client.post(
        "/api/novels/upload",
        files={"file": ("units.txt", io.BytesIO("第一章\n证据".encode()), "text/plain")},
    )
    novel_id = response.json()["id"]
    unit = {
        "source_type": "unit",
        "unit_id": 3,
        "novel_id": novel_id,
        "chunk_id": 1,
        "chunk_index": 0,
        "chapter_id": None,
        "content_snippet": "甲帮助乙",
        "score": 0.9,
        "vector_score": 0.9,
        "bm25_score": 0.0,
        "evidence_refs": ["ev-1"],
        "lifecycle": "current",
    }
    with patch(
        "app.services.knowledge_units.search.narrative_search_service.search_units",
        new_callable=AsyncMock,
        return_value=[unit],
    ) as search:
        result = await auth_client.post(
            f"/api/search/novels/{novel_id}",
            json={"query": "关系", "mode": "units", "top_k": 5},
        )
    assert result.status_code == 200
    assert result.json()["results"][0]["source_type"] == "unit"
    search.assert_awaited_once()


async def test_global_units_api_uses_owner_scoped_strategy(auth_client):
    with patch(
        "app.services.knowledge_units.search.narrative_search_service.search_global_units",
        new_callable=AsyncMock,
        return_value=[],
    ) as search:
        result = await auth_client.post(
            "/api/search", json={"query": "关系", "mode": "units", "top_k": 5}
        )
    assert result.status_code == 200
    assert result.json()["results"] == []
    assert search.await_args.kwargs["owner_id"] > 0


async def test_unauthenticated_units_are_rejected(client):
    # An unknown work remains 404; a real public work cannot expose owner units.
    result = await client.post(
        "/api/search/novels/999", json={"query": "x", "mode": "units"}
    )
    assert result.status_code == 404
