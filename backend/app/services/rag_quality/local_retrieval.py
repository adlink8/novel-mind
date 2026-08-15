"""Controlled local retrieval adapter for durable RAG quality jobs.

This adapter is intentionally local-only: sentence-transformers or Ollama for
embeddings, and the existing Chroma HTTP store for retrieval.  Missing local
dependencies raise ``DependencyOutage`` so the quality job records a blocked,
non-comparable result instead of silently using the offline oracle stub.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from typing import Any

from app.config import settings
from app.schemas.eval import EvalCase, SourceSnapshot

from .types import DependencyOutage


def _embed(text: str) -> list[float]:
    provider = (settings.embedding_provider or "local_st").lower()
    if provider in {"local_st", "local", "sentence_transformers", "bge"}:
        from app.services.local_embed import embed

        return embed(text, model_path=settings.embedding_model_path)
    if provider == "ollama":
        model = settings.embedding_model.replace("ollama/", "")
        request = urllib.request.Request(
            f"{settings.ollama_base_url.rstrip('/')}/api/embed",
            data=json.dumps({"model": model, "input": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - live dependency branch
            raise DependencyOutage(f"Ollama embedding unavailable: {exc}") from exc
        embeddings = payload.get("embeddings") or []
        if not embeddings or not embeddings[0]:
            raise DependencyOutage("Ollama returned no embedding")
        return [float(value) for value in embeddings[0]]
    raise DependencyOutage(
        f"unsupported non-local embedding provider: {settings.embedding_provider}"
    )


def retrieve_local(
    case: EvalCase, snapshot: SourceSnapshot, top_k: int = 5
) -> list[dict[str, Any]]:
    """Retrieve from the existing Chroma collection for the frozen work id."""

    try:
        query_embedding = _embed(case.question)
        from app.services.vector_store import vector_store

        collection = vector_store.get_named_collection(
            f"novel_{snapshot.work_id}", create=False
        )
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
    except DependencyOutage:
        raise
    except Exception as exc:  # pragma: no cover - live dependency branch
        raise DependencyOutage(f"local Chroma retrieval unavailable: {exc}") from exc

    ids = (result.get("ids") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    retrieved: list[dict[str, Any]] = []
    for index, document_id in enumerate(ids):
        document = documents[index] if index < len(documents) else ""
        metadata = metadatas[index] if index < len(metadatas) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        content_hash = metadata.get("content_hash")
        if not content_hash and isinstance(document, str):
            content_hash = hashlib.sha256(document.encode("utf-8")).hexdigest()
        distance = distances[index] if index < len(distances) else 1.0
        retrieved.append(
            {
                "chunk_id": str(document_id),
                "chunk_content_hash": content_hash,
                "quote_text": document,
                "content": document,
                "score": max(0.0, min(1.0, 1.0 - float(distance))),
                "metadata": metadata,
            }
        )
    return retrieved


__all__ = ["retrieve_local"]
