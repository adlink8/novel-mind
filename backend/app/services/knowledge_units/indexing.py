"""Immutable Chroma candidate projection for canonical narrative units."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_unit import (
    NarrativeIndexBuild,
    NarrativeSourceSnapshot,
    NarrativeUnit,
)
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

    async def prepare_build(
        self,
        db: AsyncSession,
        *,
        snapshot_id: int,
        config: dict | None = None,
    ) -> NarrativeIndexBuild:
        snapshot = await db.get(NarrativeSourceSnapshot, snapshot_id)
        if snapshot is None:
            raise CandidateIndexError("source snapshot not found")
        units = list(
            (
                await db.scalars(
                    select(NarrativeUnit)
                    .where(
                        NarrativeUnit.owner_id == snapshot.owner_id,
                        NarrativeUnit.novel_id == snapshot.novel_id,
                        NarrativeUnit.domain_profile == snapshot.domain_profile,
                        NarrativeUnit.unit_stage == "canonical",
                        NarrativeUnit.status == "candidate",
                        NarrativeUnit.lifecycle_status.in_(("current", "disputed")),
                    )
                    .order_by(NarrativeUnit.canonical_id, NarrativeUnit.id)
                )
            ).all()
        )
        if not units:
            raise CandidateIndexError("snapshot has no publishable canonical units")
        first = units[0]
        if any(
            (unit.owner_id, unit.novel_id, unit.domain_profile)
            != (first.owner_id, first.novel_id, first.domain_profile)
            for unit in units
        ):
            raise CandidateIndexError("canonical units cross owner/work/domain scope")
        ids = [f"unit_{unit.canonical_id}_{unit.id}" for unit in units]
        manifest = stable_hash(
            [(ids[index], units[index].content_hash) for index in range(len(units))]
        )
        config_checksum = stable_hash(config or {"embedding": "configured-provider"})
        build_key = stable_hash(
            {
                "snapshot_id": snapshot_id,
                "manifest": manifest,
                "config": config_checksum,
            }
        )[:32]
        existing = await db.scalar(
            select(NarrativeIndexBuild).where(
                NarrativeIndexBuild.owner_id == first.owner_id,
                NarrativeIndexBuild.novel_id == first.novel_id,
                NarrativeIndexBuild.build_key == build_key,
            )
        )
        if existing is not None:
            return existing
        build = NarrativeIndexBuild(
            owner_id=first.owner_id,
            novel_id=first.novel_id,
            source_snapshot_id=snapshot_id,
            domain_profile=first.domain_profile,
            build_key=build_key,
            status="draft",
            manifest_checksum=manifest,
            config_checksum=config_checksum,
            unit_count=len(units),
        )
        db.add(build)
        await db.flush()
        return build

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
                        NarrativeUnit.domain_profile == build.domain_profile,
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
        collection_name = (
            build.collection_name or f"narrative_{build.build_key}_{manifest[:12]}"
        )
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
                    raise CandidateIndexError(
                        "immutable collection already has different IDs"
                    )
            elif units:
                metadatas = [
                    {
                        "owner_id": unit.owner_id,
                        "novel_id": unit.novel_id,
                        "unit_id": unit.id,
                        "build_id": build.id,
                        "manifest_checksum": manifest,
                        "canonical_id": unit.canonical_id,
                        "domain_profile": unit.domain_profile,
                        "lifecycle": unit.lifecycle_status,
                        "lifecycle_status": unit.lifecycle_status,
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
            raise CandidateIndexError(
                f"candidate reconcile failed: missing={missing}, orphan={orphan}"
            )
        build.collection_name = collection_name
        build.unit_count = len(units)
        build.status = "candidate"
        await db.flush()
        return CandidateBuildReport(
            build.id,
            collection_name,
            expected_ids,
            actual_ids,
            missing,
            orphan,
            manifest,
            build.status,
        )


narrative_indexing_service = NarrativeIndexingService()
