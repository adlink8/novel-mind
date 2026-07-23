"""Owner-safe narrative unit retrieval and unit/chunk fusion."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeEvidenceRef
from app.models.knowledge_unit import (
    NarrativeActivePointer,
    NarrativeIndexBuild,
    NarrativeUnit,
    NarrativeUnitEvidenceLink,
)
from app.models.novel import Novel
from app.services.ai_service import ai_service
from app.services.vector_store import vector_store


BuildSelector = Callable[
    [AsyncSession, int, int, str], Awaitable[NarrativeIndexBuild | None]
]


async def select_active_build(
    db: AsyncSession, owner_id: int, novel_id: int, domain_profile: str
) -> NarrativeIndexBuild | None:
    pointer = await db.scalar(
        select(NarrativeActivePointer).where(
            NarrativeActivePointer.owner_id == owner_id,
            NarrativeActivePointer.novel_id == novel_id,
            NarrativeActivePointer.domain_profile == domain_profile,
        )
    )
    return await db.get(NarrativeIndexBuild, pointer.build_id) if pointer else None


def select_candidate_build(build: NarrativeIndexBuild) -> BuildSelector:
    """Bind admin/eval retrieval to one candidate without moving active state."""

    async def select_candidate(
        db: AsyncSession, owner_id: int, novel_id: int, domain_profile: str
    ) -> NarrativeIndexBuild | None:
        persisted = await db.get(NarrativeIndexBuild, build.id)
        if persisted is None or (
            persisted.owner_id,
            persisted.novel_id,
            persisted.domain_profile,
        ) != (owner_id, novel_id, domain_profile):
            return None
        return persisted

    return select_candidate


class NarrativeSearchService:
    def __init__(self, *, store: Any = vector_store, embeddings: Any = ai_service):
        self.store = store
        self.embeddings = embeddings

    async def search_global_units(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        query: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        novel_ids = list(
            (await db.scalars(select(Novel.id).where(Novel.owner_id == owner_id))).all()
        )
        results: list[dict[str, Any]] = []
        for novel_id in novel_ids:
            results.extend(
                await self.search_units(
                    db,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    query=query,
                    top_k=top_k,
                )
            )
        return sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]

    async def search_units(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        query: str,
        top_k: int = 5,
        domain_profile: str = "fiction",
        build_selector: BuildSelector = select_active_build,
    ) -> list[dict[str, Any]]:
        owned = await db.scalar(
            select(Novel.id).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if owned is None:
            return []
        build = await build_selector(db, owner_id, novel_id, domain_profile)
        if (
            build is None
            or build.status not in {"candidate", "active"}
            or not build.collection_name
        ):
            return []
        embedding = (await self.embeddings.embedding(texts=[query]))[0]
        collection = await asyncio.to_thread(
            self.store.get_named_collection, build.collection_name
        )
        raw = await asyncio.to_thread(
            collection.query, query_embeddings=[embedding], n_results=top_k
        )
        results: list[dict[str, Any]] = []
        for index, raw_id in enumerate((raw.get("ids") or [[]])[0]):
            metadata = (raw.get("metadatas") or [[]])[0][index] or {}
            if (
                int(metadata.get("owner_id", -1)) != owner_id
                or int(metadata.get("novel_id", -1)) != novel_id
            ):
                continue
            unit = await db.get(NarrativeUnit, int(metadata["unit_id"]))
            if (
                unit is None
                or unit.status not in {"candidate", "active"}
                or unit.lifecycle_status not in {"current", "disputed"}
            ):
                continue
            evidence = list(
                (
                    await db.scalars(
                        select(NarrativeUnitEvidenceLink).where(
                            NarrativeUnitEvidenceLink.unit_id == unit.id
                        )
                    )
                ).all()
            )
            refs = (
                list(
                    (
                        await db.scalars(
                            select(KnowledgeEvidenceRef).where(
                                KnowledgeEvidenceRef.id.in_(
                                    [link.source_evidence_id for link in evidence]
                                )
                            )
                        )
                    ).all()
                )
                if evidence
                else []
            )
            distance = (raw.get("distances") or [[]])[0][index]
            results.append(
                {
                    "source_type": "unit",
                    "unit_id": unit.id,
                    "novel_id": novel_id,
                    "chunk_id": next(
                        (ref.text_chunk_id for ref in refs if ref.text_chunk_id), 0
                    ),
                    "chunk_index": 0,
                    "chapter_id": next(
                        (ref.chapter_id for ref in refs if ref.chapter_id), None
                    ),
                    "content_snippet": unit.answer,
                    "score": max(0.0, min(1.0, 1.0 - float(distance or 0.0))),
                    "vector_score": max(0.0, min(1.0, 1.0 - float(distance or 0.0))),
                    "bm25_score": 0.0,
                    "evidence_refs": [link.ref_key for link in evidence],
                    "lifecycle": unit.lifecycle_status,
                    "build_id": build.id,
                    "manifest_checksum": build.manifest_checksum,
                }
            )
        return results


def fuse_results(
    chunks: list[dict[str, Any]],
    units: list[dict[str, Any]],
    *,
    top_k: int,
    chunk_weight: float = 0.5,
    unit_weight: float = 0.5,
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for source, rows, weight in (
        ("chunk", chunks, chunk_weight),
        ("unit", units, unit_weight),
    ):
        for row in rows:
            key = (source, int(row.get("unit_id") or row.get("chunk_id") or 0))
            item = dict(row)
            item["source_type"] = source
            item["score"] = round(float(item.get("score", 0.0)) * weight, 6)
            merged[key] = item
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]


narrative_search_service = NarrativeSearchService()


class NarrativeRetrievalStrategy:
    """Production chunks/units/hybrid policy shared by API and frozen eval."""

    def __init__(self, *, chunks: Any, units: NarrativeSearchService):
        self.chunks = chunks
        self.units = units

    async def search_global(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        query: str,
        mode: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        unit_rows: list[dict[str, Any]] = []
        if mode in {"units", "hybrid"}:
            unit_rows = await self.units.search_global_units(
                db, owner_id=owner_id, query=query, top_k=top_k
            )
        if mode == "units":
            return unit_rows
        chunk_rows = await self.chunks.search_global(
            db, query=query, top_k=top_k, owner_id=owner_id
        )
        if mode == "chunks":
            return chunk_rows
        return fuse_results(chunk_rows, unit_rows, top_k=top_k)

    async def search_novel(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        domain_profile: str,
        query: str,
        mode: str,
        top_k: int,
        build_selector: BuildSelector = select_active_build,
    ) -> list[dict[str, Any]]:
        unit_rows: list[dict[str, Any]] = []
        if mode in {"units", "hybrid"}:
            unit_rows = await self.units.search_units(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile=domain_profile,
                query=query,
                top_k=top_k,
                build_selector=build_selector,
            )
        if mode == "units":
            return unit_rows
        chunk_rows = await self.chunks.search_novel(
            db, novel_id=novel_id, query=query, top_k=top_k
        )
        if mode == "chunks":
            return chunk_rows
        return fuse_results(chunk_rows, unit_rows, top_k=top_k)


def production_retrieval_strategy() -> NarrativeRetrievalStrategy:
    # Lazy import avoids the hybrid service importing this module during API startup.
    from app.services.hybrid_search import hybrid_search_service

    return NarrativeRetrievalStrategy(
        chunks=hybrid_search_service, units=narrative_search_service
    )
