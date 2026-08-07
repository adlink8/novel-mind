"""Owner-safe narrative unit retrieval and unit/chunk fusion."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
from app.services.canon_space_policy import ORIGINAL_CANON, assert_pipeline_input
from app.services.vector_store import vector_store


logger = logging.getLogger(__name__)

BuildSelector = Callable[
    [AsyncSession, int, int, str], Awaitable[NarrativeIndexBuild | None]
]

# ── Retrieval layer registry (server-side router authority; NM-DATA-010) ──
# "narrative_memory" is deliberately listed but DISABLED: NM is candidate-only
# (no active pointer, no promotion journal) and MUST NOT enter production
# retrieval before an explicit Phase 30 authorization. See ADR-0002
# (docs/adr/0002-narrative-unit-vs-narrative-memory.md) §2/§4 — any router
# change that enables this layer is an architecture violation until then.
RETRIEVAL_LAYERS: dict[str, str] = {
    "chunks": "enabled",
    "units": "enabled",
    "narrative_memory": "disabled",
}

# Fallback-reason enum surfaced verbatim in SearchResponse.fallback_reason.
FALLBACK_UNITS_INDEX_UNAVAILABLE = "units_index_unavailable"
FALLBACK_UNITS_QUERY_FAILED = "units_query_failed"


class UnitsIndexUnavailableError(RuntimeError):
    """The units layer has no usable index (no pointer/build or collection)."""


@dataclass(frozen=True)
class RetrievalOutcome:
    """Honest retrieval result: rows plus the layer that actually executed."""

    rows: list[dict[str, Any]]
    resolved_mode: str
    fallback_reason: str | None = None


def _citation_backed(unit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enforce the citation contract on unit hits (ADR-0002 §2).

    Search evidence/citations may only come from the raw chunk (leaf) layer:
    a Narrative Unit hit is only presentable together with its evidence
    back-links into that layer. Units without ``evidence_refs`` are dropped
    fail-closed instead of being shown as uncited claims.
    """
    backed: list[dict[str, Any]] = []
    for row in unit_rows:
        if row.get("evidence_refs"):
            backed.append(row)
        else:
            logger.warning(
                "dropping unit hit without evidence back-links: unit_id=%s",
                row.get("unit_id"),
            )
    return backed


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
        units_layer_available = False
        for novel_id in novel_ids:
            try:
                rows = await self.search_units(
                    db,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    query=query,
                    top_k=top_k,
                )
            except UnitsIndexUnavailableError:
                # Novels without an active units index don't poison the whole
                # owner-scope query; only a fully index-less owner is treated
                # as "units layer unavailable".
                continue
            units_layer_available = True
            results.extend(rows)
        if not units_layer_available:
            raise UnitsIndexUnavailableError(
                f"no eligible narrative index build for owner {owner_id}"
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
        space: str = ORIGINAL_CANON,
    ) -> list[dict[str, Any]]:
        # Canon-space boundary: original retrieval consumers must not read a
        # non-original space (Phase 35 three-space isolation).
        assert_pipeline_input(space, "original_retrieval")
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
            raise UnitsIndexUnavailableError(
                f"no eligible narrative index build for novel {novel_id}"
            )
        try:
            collection = await asyncio.to_thread(
                self.store.get_named_collection, build.collection_name
            )
        except Exception as exc:
            raise UnitsIndexUnavailableError(
                f"narrative index collection missing: {build.collection_name}"
            ) from exc
        embedding = (await self.embeddings.embedding(texts=[query]))[0]
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
            if not evidence:
                # Citation contract (ADR-0002 §2): a unit may only surface with
                # its evidence back-links into the raw chunk layer. An
                # evidence-less unit is never shown as a search result.
                logger.warning(
                    "dropping unit %s: no evidence back-links to raw chunks",
                    unit.id,
                )
                continue
            refs = list(
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
    """Server-side retrieval router shared by API and frozen eval (NM-DATA-010).

    The client ``mode`` is an *intent* only; this router decides which layer
    actually executes and reports it honestly:

    - ``auto`` prefers ``hybrid`` (units + chunks fusion);
    - when the units index is unavailable (missing collection, no active
      pointer) the router degrades to raw ``chunks`` with
      ``fallback_reason=units_index_unavailable`` — including when ``units``
      was requested explicitly (degrade, never error/empty);
    - a failing units query degrades likewise with
      ``fallback_reason=units_query_failed``;
    - ``narrative_memory`` is registered in RETRIEVAL_LAYERS but disabled
      (candidate-only until Phase 30, ADR-0002 §2/§4) and is not a valid mode.
    """

    def __init__(self, *, chunks: Any, units: NarrativeSearchService):
        self.chunks = chunks
        self.units = units

    async def _resolve(
        self,
        intent: str,
        *,
        top_k: int,
        fetch_units: Callable[[], Awaitable[list[dict[str, Any]]]],
        fetch_chunks: Callable[[], Awaitable[list[dict[str, Any]]]],
    ) -> RetrievalOutcome:
        if intent not in {"auto", "chunks", "units", "hybrid"}:
            # Disabled layers (narrative_memory) intentionally land here:
            # candidate-only layers cannot be routed to (ADR-0002 §4).
            raise ValueError(f"unsupported retrieval mode: {intent}")
        target = "hybrid" if intent == "auto" else intent
        if target == "chunks":
            return RetrievalOutcome(await fetch_chunks(), "chunks")
        try:
            unit_rows = _citation_backed(await fetch_units())
        except UnitsIndexUnavailableError:
            return RetrievalOutcome(
                await fetch_chunks(), "chunks", FALLBACK_UNITS_INDEX_UNAVAILABLE
            )
        except Exception:
            logger.exception("units retrieval failed; degrading to raw chunks")
            return RetrievalOutcome(
                await fetch_chunks(), "chunks", FALLBACK_UNITS_QUERY_FAILED
            )
        if target == "units":
            return RetrievalOutcome(unit_rows, "units")
        chunk_rows = await fetch_chunks()
        return RetrievalOutcome(
            fuse_results(chunk_rows, unit_rows, top_k=top_k), "hybrid"
        )

    async def resolve_global(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        query: str,
        mode: str,
        top_k: int,
    ) -> RetrievalOutcome:
        return await self._resolve(
            mode,
            top_k=top_k,
            fetch_units=lambda: self.units.search_global_units(
                db, owner_id=owner_id, query=query, top_k=top_k
            ),
            fetch_chunks=lambda: self.chunks.search_global(
                db, query=query, top_k=top_k, owner_id=owner_id
            ),
        )

    async def resolve_novel(
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
    ) -> RetrievalOutcome:
        return await self._resolve(
            mode,
            top_k=top_k,
            fetch_units=lambda: self.units.search_units(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile=domain_profile,
                query=query,
                top_k=top_k,
                build_selector=build_selector,
            ),
            fetch_chunks=lambda: self.chunks.search_novel(
                db, novel_id=novel_id, query=query, top_k=top_k
            ),
        )

    async def search_global(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        query: str,
        mode: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        outcome = await self.resolve_global(
            db, owner_id=owner_id, query=query, mode=mode, top_k=top_k
        )
        return outcome.rows

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
        outcome = await self.resolve_novel(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            domain_profile=domain_profile,
            query=query,
            mode=mode,
            top_k=top_k,
            build_selector=build_selector,
        )
        return outcome.rows


def production_retrieval_strategy() -> NarrativeRetrievalStrategy:
    # Lazy import avoids the hybrid service importing this module during API startup.
    from app.services.hybrid_search import hybrid_search_service

    return NarrativeRetrievalStrategy(
        chunks=hybrid_search_service, units=narrative_search_service
    )
