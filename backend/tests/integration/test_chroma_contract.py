"""
Chroma store contract against CI-locked chromadb/chroma:1.5.9.

Fixed vectors only — validates store shape/CRUD/reconcile, never semantic quality.
"""

from __future__ import annotations

import importlib.metadata
import re
import uuid

import httpx
import pytest

from app.services.vector_store import VectorStore, VectorStoreError
from tests.integration.conftest import fixed_embedding

pytestmark = pytest.mark.integration


def test_service_lock_chroma_digest_and_client(service_lock):
    """Image digest + Python client pin must be present and consistent (fail closed)."""
    chroma = service_lock["chroma"]
    digest = chroma["digest"]
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
    assert chroma["tag"] == "1.5.9"
    assert digest in chroma["image_ref"]
    assert chroma["health_path"] == "/api/v2/heartbeat"
    assert chroma["python_client"] == "chromadb==1.5.9"
    installed = importlib.metadata.version("chromadb")
    assert installed == "1.5.9", f"chromadb client drift: installed={installed}"


def test_chroma_heartbeat_health(chroma_health_url, require_chroma):
    """Health endpoint /api/v2/heartbeat returns success."""
    with httpx.Client(timeout=5.0) as client:
        resp = client.get(chroma_health_url)
    assert resp.status_code == 200
    # Body may be JSON nanosecond timestamp or plain text depending on version.
    assert resp.text or resp.content


@pytest.mark.asyncio
async def test_fixed_vector_crud_contract(vector_store_ci: VectorStore):
    """CRUD with fixed vectors: add → count → search → delete."""
    novel_id = 900_000 + (uuid.uuid4().int % 50_000)
    emb_a = fixed_embedding(1)
    emb_b = fixed_embedding(2)
    chunks = [
        {
            "id": 1,
            "content": "fixed-vector-chunk-one",
            "embedding": emb_a,
            "metadata": {
                "chapter_id": 1,
                "chunk_index": 0,
                "chunk_type": "paragraph",
                "word_count": 3,
            },
        },
        {
            "id": 2,
            "content": "fixed-vector-chunk-two",
            "embedding": emb_b,
            "metadata": {
                "chapter_id": 1,
                "chunk_index": 1,
                "chunk_type": "dialogue",
                "word_count": 3,
            },
        },
    ]
    try:
        await vector_store_ci.add_chunks(novel_id=novel_id, chunks=chunks)
        count = await vector_store_ci.get_chunk_count(novel_id)
        assert count == 2

        results = await vector_store_ci.search(
            novel_id=novel_id, query_embedding=emb_a, top_k=2
        )
        assert len(results) >= 1
        assert results[0]["chunk_id"] in {"chunk_1", "chunk_2"}
        assert 0.0 <= results[0]["score"] <= 1.0
        # Exact self-query of emb_a should prefer chunk_1.
        assert results[0]["chunk_id"] == "chunk_1"

        filtered = await vector_store_ci.search(
            novel_id=novel_id,
            query_embedding=emb_b,
            top_k=2,
            filters={"chunk_type": "dialogue"},
        )
        assert len(filtered) == 1
        assert filtered[0]["chunk_id"] == "chunk_2"
    finally:
        try:
            await vector_store_ci.delete_novel_chunks(novel_id)
        except VectorStoreError:
            pass
    assert await vector_store_ci.get_chunk_count(novel_id) == 0


@pytest.mark.asyncio
async def test_named_collection_reconcile_contract(vector_store_ci: VectorStore):
    """Named collection add + get IDs form a clean expected/actual reconcile set."""
    name = f"ci_contract_{uuid.uuid4().hex[:12]}"
    emb = fixed_embedding(7)
    expected_ids = ("unit_canon_a_1", "unit_canon_b_2")

    collection = vector_store_ci.get_named_collection(name, create=True)
    assert collection.count() == 0
    collection.add(
        ids=list(expected_ids),
        documents=["Q: a\nA: a", "Q: b\nA: b"],
        embeddings=[emb, fixed_embedding(8)],
        metadatas=[
            {
                "owner_id": 1,
                "novel_id": 1,
                "build_id": 42,
                "manifest_checksum": "d" * 64,
            },
            {
                "owner_id": 1,
                "novel_id": 1,
                "build_id": 42,
                "manifest_checksum": "d" * 64,
            },
        ],
    )
    actual = collection.get(include=["metadatas"])
    actual_ids = tuple(sorted(actual.get("ids", [])))
    assert actual_ids == tuple(sorted(expected_ids))
    missing = tuple(sorted(set(expected_ids) - set(actual_ids)))
    orphan = tuple(sorted(set(actual_ids) - set(expected_ids)))
    assert missing == () and orphan == ()
    assert collection.count() == 2

    # Idempotent re-read of same collection must not invent residues.
    again = collection.get(include=[])
    assert tuple(sorted(again.get("ids", []))) == actual_ids

    vector_store_ci.client.delete_collection(name=name)
