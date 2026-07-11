"""Immutable Chroma candidate projection for canonical narrative units."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import NarrativeIndexBuild, NarrativeUnit
from app.services.ai_service import ai_service
from app.services.knowledge_units.materialize import stable_hash
from app.services.vector_store import VectorStore, VectorStoreError, vector_store


class CandidateIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateBuildReport:
    build_id: int
    collection_name: str
    expected_ids: tuple[str, ...]
    actual_ids: tuple[str, ...]
    missing: tuple[str, ...]
    orphan: tuple[str, ...]
    manifest_checksum: str
    status: str


class NarrativeIndexingService:
    def __init__(self, store: VectorStore = vector_store):
        self.store = store

    async def build_candidate(
        self,
        db: AsyncSession,
        *,
        build_id: int,
        embedder: Callable[[list[str]], Awaitable[list[list[float]]]] | None = None,
    ) -> CandidateBuildReport:
        build = await db.get(NarrativeIndexBuild, build_id)
        if build is None or build.status not in {"draft", "failed"}:
            raise CandidateIndexError("build is missing or not buildable")
        units = list(
            (
                await db.scalars(
                    select(NarrativeUnit)
                    .where(
                        NarrativeUnit.owner_id == build.owner_id,
                        NarrativeUnit.novel_id == build.novel_id,
                        NarrativeUnit.source_snapshot_id == build.source_snapshot_id,
                        NarrativeUnit.unit_stage == "canonical",
                        NarrativeUnit.status == "candidate",
                        NarrativeUnit.lifecycle_status.in_(("current", "disputed")),
                    )
                    .order_by(NarrativeUnit.canonical_id, NarrativeUnit.id)
                )
            ).all()
        )
        expected_ids = tuple(f"unit_{unit.canonical_id}_{unit.id}" for unit in units)
        manifest = stable_hash(
            [(expected_ids[i], units[i].content_hash) for i in range(len(units))]
        )
        if build.manifest_checksum != manifest:
            raise CandidateIndexError("build manifest checksum mismatch")
        collection_name = build.collection_name or f"narrative_{build.build_key}_{manifest[:12]}"
        texts = [f"Q: {unit.question}\nA: {unit.answer}" for unit in units]
        if embedder is None:
            embedder = ai_service.embedding
        try:
            embeddings = await embedder(texts)
            collection = await asyncio.to_thread(
                self.store.get_named_collection, collection_name, create=True
            )
            if await asyncio.to_thread(collection.count):
                existing = await asyncio.to_thread(collection.get, include=[])
                existing_ids = tuple(sorted(existing.get("ids", [])))
                if existing_ids != tuple(sorted(expected_ids)):
                    raise CandidateIndexError("immutable collection already has different IDs")
            elif units:
                metadatas = [
                    {
                        "owner_id": unit.owner_id,
                        "novel_id": unit.novel_id,
                        "unit_id": unit.id,
                        "canonical_id": unit.canonical_id,
                        "domain_profile": unit.domain_profile,
                        "lifecycle": unit.lifecycle_status,
                        "source_judgment_id": unit.source_judgment_id,
                        "evidence_checksum": unit.evidence_manifest_checksum,
                    }
                    for unit in units
                ]
                await asyncio.to_thread(
                    collection.add,
                    ids=list(expected_ids),
                    documents=texts,
                    embeddings=embeddings,
                    metadatas=metadatas,
                )
            actual = await asyncio.to_thread(collection.get, include=[])
            actual_ids = tuple(sorted(actual.get("ids", [])))
        except CandidateIndexError:
            build.status = "failed"
            await db.flush()
            raise
        except Exception as exc:
            build.status = "failed"
            build.error_detail = str(exc)
            await db.flush()
            raise VectorStoreError(f"candidate index blocked: {exc}") from exc
        missing = tuple(sorted(set(expected_ids) - set(actual_ids)))
        orphan = tuple(sorted(set(actual_ids) - set(expected_ids)))
        if missing or orphan:
            build.status = "failed"
            await db.flush()
            raise CandidateIndexError(f"candidate reconcile failed: missing={missing}, orphan={orphan}")
        build.collection_name = collection_name
        build.unit_count = len(units)
        build.status = "candidate"
        await db.flush()
        return CandidateBuildReport(build.id, collection_name, expected_ids, actual_ids, missing, orphan, manifest, build.status)


narrative_indexing_service = NarrativeIndexingService()
