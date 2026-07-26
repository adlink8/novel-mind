"""Server-side retrieval router: auto decision matrix, honest fallback,
citation back-link contract, and disabled narrative_memory layer (24-03)."""

import pytest

pytestmark = pytest.mark.unit

from sqlalchemy import delete

from app.main import app
from app.models.knowledge_unit import NarrativeUnitEvidenceLink
from app.schemas.novel import SearchRequest
from app.services.knowledge_units.indexing import NarrativeIndexingService
from app.services.knowledge_units.search import (
    RETRIEVAL_LAYERS,
    NarrativeRetrievalStrategy,
    NarrativeSearchService,
    UnitsIndexUnavailableError,
    production_retrieval_strategy,
    select_active_build,
    select_candidate_build,
)
from tests.test_knowledge_unit_indexing import FakeStore, _candidate_build

CHUNK_ROW = {
    "chunk_id": 1,
    "novel_id": 1,
    "chunk_index": 0,
    "content_snippet": "raw leaf text",
    "score": 0.8,
    "vector_score": 0.8,
    "bm25_score": 0.0,
}

UNIT_ROW = {
    "unit_id": 2,
    "chunk_id": 1,
    "novel_id": 1,
    "chunk_index": 0,
    "content_snippet": "cited fact",
    "score": 1.0,
    "vector_score": 1.0,
    "bm25_score": 0.0,
    "evidence_refs": ["ev-2"],
    "lifecycle": "current",
}


class FakeChunks:
    async def search_novel(self, db, *, novel_id, query, top_k):
        return [dict(CHUNK_ROW)]

    async def search_global(self, db, *, query, top_k, owner_id):
        return [dict(CHUNK_ROW)]


class FakeUnits:
    """Injectable units layer: 'available', 'unavailable' or 'failing'."""

    def __init__(self, behavior, rows=None):
        self.behavior = behavior
        self.rows = [dict(UNIT_ROW)] if rows is None else rows

    async def _rows(self):
        if self.behavior == "unavailable":
            raise UnitsIndexUnavailableError("no active pointer")
        if self.behavior == "failing":
            raise RuntimeError("chroma exploded")
        return [dict(row) for row in self.rows]

    async def search_units(self, db, **kwargs):
        return await self._rows()

    async def search_global_units(self, db, **kwargs):
        return await self._rows()


def _strategy(units_behavior, rows=None):
    return NarrativeRetrievalStrategy(
        chunks=FakeChunks(), units=FakeUnits(units_behavior, rows)
    )


def test_search_request_defaults_to_auto_intent():
    assert SearchRequest(query="test").mode == "auto"


# ── auto decision matrix: units availability × client intent ──────────────

MATRIX = [
    # (units_behavior, intent, resolved_mode, fallback_reason)
    ("available", "auto", "hybrid", None),
    ("available", "chunks", "chunks", None),
    ("available", "units", "units", None),
    ("available", "hybrid", "hybrid", None),
    ("unavailable", "auto", "chunks", "units_index_unavailable"),
    ("unavailable", "chunks", "chunks", None),
    ("unavailable", "units", "chunks", "units_index_unavailable"),
    ("unavailable", "hybrid", "chunks", "units_index_unavailable"),
    ("failing", "auto", "chunks", "units_query_failed"),
    ("failing", "chunks", "chunks", None),
    ("failing", "units", "chunks", "units_query_failed"),
    ("failing", "hybrid", "chunks", "units_query_failed"),
]


@pytest.mark.parametrize("units_behavior,intent,resolved,reason", MATRIX)
async def test_router_decision_matrix_novel(units_behavior, intent, resolved, reason):
    outcome = await _strategy(units_behavior).resolve_novel(
        object(),
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        query="q",
        mode=intent,
        top_k=5,
    )
    assert outcome.resolved_mode == resolved
    assert outcome.fallback_reason == reason
    if resolved == "chunks":
        assert [row["chunk_id"] for row in outcome.rows] == [1]
        assert all(row.get("unit_id") is None for row in outcome.rows)
    if resolved == "units":
        assert [row["unit_id"] for row in outcome.rows] == [2]
    if resolved == "hybrid":
        assert {row["source_type"] for row in outcome.rows} == {"chunk", "unit"}


@pytest.mark.parametrize("units_behavior,intent,resolved,reason", MATRIX)
async def test_router_decision_matrix_global(units_behavior, intent, resolved, reason):
    outcome = await _strategy(units_behavior).resolve_global(
        object(), owner_id=1, query="q", mode=intent, top_k=5
    )
    assert (outcome.resolved_mode, outcome.fallback_reason) == (resolved, reason)


async def test_degraded_units_request_returns_chunks_not_error_or_empty():
    """Explicit units intent on a broken index degrades honestly to chunks."""
    outcome = await _strategy("unavailable").resolve_novel(
        object(),
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        query="q",
        mode="units",
        top_k=5,
    )
    assert outcome.rows and outcome.rows[0]["content_snippet"] == "raw leaf text"
    assert outcome.resolved_mode == "chunks"
    assert outcome.fallback_reason == "units_index_unavailable"


async def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        await _strategy("available").resolve_global(
            object(), owner_id=1, query="q", mode="quantum", top_k=5
        )


# ── narrative_memory layer: registered but disabled (ADR-0002 §2/§4) ──────


def test_narrative_memory_layer_registered_but_disabled():
    assert RETRIEVAL_LAYERS["narrative_memory"] == "disabled"
    assert RETRIEVAL_LAYERS["chunks"] == "enabled"
    assert RETRIEVAL_LAYERS["units"] == "enabled"


async def test_narrative_memory_mode_cannot_be_routed():
    with pytest.raises(ValueError):
        await _strategy("available").resolve_novel(
            object(),
            owner_id=1,
            novel_id=1,
            domain_profile="fiction",
            query="q",
            mode="narrative_memory",
            top_k=5,
        )


# ── citation contract: unit hits must carry raw-chunk evidence back-links ──


async def test_unit_hits_without_evidence_backlinks_are_dropped():
    uncited = dict(UNIT_ROW, unit_id=9, evidence_refs=[])
    strategy = _strategy("available", rows=[dict(UNIT_ROW), uncited])
    outcome = await strategy.resolve_novel(
        object(),
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        query="q",
        mode="units",
        top_k=5,
    )
    assert [row["unit_id"] for row in outcome.rows] == [2]
    assert all(row["evidence_refs"] for row in outcome.rows)


async def test_fused_unit_hits_all_carry_evidence_backlinks():
    uncited = dict(UNIT_ROW, unit_id=9, evidence_refs=[])
    strategy = _strategy("available", rows=[dict(UNIT_ROW), uncited])
    outcome = await strategy.resolve_novel(
        object(),
        owner_id=1,
        novel_id=1,
        domain_profile="fiction",
        query="q",
        mode="hybrid",
        top_k=10,
    )
    unit_rows = [row for row in outcome.rows if row["source_type"] == "unit"]
    assert [row["unit_id"] for row in unit_rows] == [2]
    assert all(row["evidence_refs"] for row in unit_rows)


async def test_search_units_raises_unavailable_without_eligible_build(db_session):
    build = await _candidate_build(db_session)  # draft, no collection
    service = NarrativeSearchService(store=FakeStore(), embeddings=object())
    with pytest.raises(UnitsIndexUnavailableError):
        # No active pointer exists for this novel.
        await service.search_units(
            db_session,
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            query="q",
            build_selector=select_active_build,
        )
    with pytest.raises(UnitsIndexUnavailableError):
        # Draft build without a collection is not a usable index either.
        await service.search_units(
            db_session,
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            query="q",
            build_selector=select_candidate_build(build),
        )


async def test_search_units_drops_units_whose_evidence_links_were_removed(db_session):
    build = await _candidate_build(db_session)
    store = FakeStore()

    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]

    await NarrativeIndexingService(store).build_candidate(
        db_session, build_id=build.id, embedder=embed
    )
    collection = store.get_named_collection(build.collection_name)
    collection.query = lambda **kwargs: {
        "ids": [[collection.ids[0]]],
        "metadatas": [[collection.metadatas[0]]],
        "distances": [[0.1]],
    }

    class Embeddings:
        async def embedding(self, *, texts):
            return [[0.1, 0.2]]

    service = NarrativeSearchService(store=store, embeddings=Embeddings())
    selector = select_candidate_build(build)
    rows = await service.search_units(
        db_session,
        owner_id=build.owner_id,
        novel_id=build.novel_id,
        domain_profile=build.domain_profile,
        query="q",
        build_selector=selector,
    )
    assert rows and rows[0]["evidence_refs"]

    await db_session.execute(delete(NarrativeUnitEvidenceLink))
    await db_session.flush()
    assert (
        await service.search_units(
            db_session,
            owner_id=build.owner_id,
            novel_id=build.novel_id,
            domain_profile=build.domain_profile,
            query="q",
            build_selector=selector,
        )
        == []
    )


async def test_search_global_units_unavailable_when_no_novel_has_index(db_session):
    build = await _candidate_build(db_session)
    service = NarrativeSearchService(store=FakeStore(), embeddings=object())
    with pytest.raises(UnitsIndexUnavailableError):
        await service.search_global_units(
            db_session, owner_id=build.owner_id, query="q"
        )


# ── API surface: honest metadata in SearchResponse ────────────────────────


async def _upload(auth_client, name):
    import io

    response = await auth_client.post(
        "/api/novels/upload",
        files={"file": (name, io.BytesIO("第一章\n证据".encode()), "text/plain")},
    )
    return response.json()["id"]


async def test_api_reports_resolved_mode_and_fallback_reason(auth_client):
    novel_id = await _upload(auth_client, "router.txt")
    strategy = _strategy("unavailable")
    app.dependency_overrides[production_retrieval_strategy] = lambda: strategy
    try:
        response = await auth_client.post(
            f"/api/search/novels/{novel_id}",
            json={"query": "q", "mode": "auto", "top_k": 5},
        )
    finally:
        app.dependency_overrides.pop(production_retrieval_strategy, None)

    assert response.status_code == 200
    body = response.json()
    assert body["resolved_mode"] == "chunks"
    assert body["fallback_reason"] == "units_index_unavailable"
    assert body["results"][0]["source_type"] == "chunk"


async def test_api_auto_reports_hybrid_when_units_available(auth_client):
    novel_id = await _upload(auth_client, "router2.txt")
    strategy = _strategy("available")
    app.dependency_overrides[production_retrieval_strategy] = lambda: strategy
    try:
        response = await auth_client.post(
            f"/api/search/novels/{novel_id}", json={"query": "q", "top_k": 5}
        )
    finally:
        app.dependency_overrides.pop(production_retrieval_strategy, None)

    body = response.json()
    assert body["resolved_mode"] == "hybrid"
    assert body["fallback_reason"] is None


async def test_api_unauthenticated_auto_resolves_to_chunks(auth_client):
    novel_id = await _upload(auth_client, "public.txt")
    strategy = _strategy("available")
    app.dependency_overrides[production_retrieval_strategy] = lambda: strategy
    token = auth_client.headers.pop("Authorization")
    auth_client.cookies.clear()
    try:
        auto = await auth_client.post(
            f"/api/search/novels/{novel_id}", json={"query": "q"}
        )
        explicit = await auth_client.post(
            f"/api/search/novels/{novel_id}", json={"query": "q", "mode": "units"}
        )
    finally:
        auth_client.headers["Authorization"] = token
        app.dependency_overrides.pop(production_retrieval_strategy, None)

    assert auto.status_code == 200
    assert auto.json()["resolved_mode"] == "chunks"
    assert auto.json()["fallback_reason"] == "units_requires_auth"
    # Explicit unit-layer intents keep the 401 contract for anonymous callers.
    assert explicit.status_code == 401


async def test_global_api_reports_units_query_failure(auth_client):
    strategy = _strategy("failing")
    app.dependency_overrides[production_retrieval_strategy] = lambda: strategy
    try:
        response = await auth_client.post(
            "/api/search", json={"query": "q", "mode": "hybrid", "top_k": 5}
        )
    finally:
        app.dependency_overrides.pop(production_retrieval_strategy, None)

    body = response.json()
    assert body["resolved_mode"] == "chunks"
    assert body["fallback_reason"] == "units_query_failed"
