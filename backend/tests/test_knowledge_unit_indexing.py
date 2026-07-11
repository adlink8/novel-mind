"""Immutable narrative candidate index tests."""

from sqlalchemy import select

from app.models.knowledge_unit import NarrativeIndexBuild, NarrativeUnit
from app.services.knowledge_units.canonicalize import narrative_canonicalizer
from app.services.knowledge_units.indexing import NarrativeIndexingService
from app.services.knowledge_units.materialize import (
    narrative_unit_materializer,
    stable_hash,
)
from app.services.vector_store import VectorStore
from tests.test_knowledge_unit_materialize import _accepted_source


class FakeCollection:
    def __init__(self):
        self.ids = []
        self.documents = []
        self.metadatas = []

    def count(self):
        return len(self.ids)

    def add(self, *, ids, documents, embeddings, metadatas):
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def get(self, include=None):
        return {"ids": list(self.ids), "metadatas": list(self.metadatas)}


class FakeStore:
    def __init__(self):
        self.collections = {}

    def get_named_collection(self, name, *, create=False):
        if create:
            return self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


async def _candidate_build(db):
    snapshot = await _accepted_source(db)
    await narrative_unit_materializer.materialize_snapshot(db, snapshot_id=snapshot.id)
    await narrative_canonicalizer.canonicalize_snapshot(db, snapshot_id=snapshot.id)
    units = list(
        (
            await db.scalars(
                select(NarrativeUnit).order_by(
                    NarrativeUnit.canonical_id, NarrativeUnit.id
                )
            )
        ).all()
    )
    ids = [f"unit_{unit.canonical_id}_{unit.id}" for unit in units]
    manifest = stable_hash([(ids[i], units[i].content_hash) for i in range(len(units))])
    build = NarrativeIndexBuild(
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        source_snapshot_id=snapshot.id,
        domain_profile=snapshot.domain_profile,
        build_key="test-build",
        status="draft",
        manifest_checksum=manifest,
        config_checksum="c" * 64,
        unit_count=0,
    )
    db.add(build)
    await db.flush()
    return build


async def test_builds_and_reconciles_immutable_candidate(db_session):
    build = await _candidate_build(db_session)
    store = FakeStore()
    service = NarrativeIndexingService(store=store)

    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]

    report = await service.build_candidate(
        db_session, build_id=build.id, embedder=embed
    )
    assert report.status == "candidate"
    assert report.expected_ids == report.actual_ids
    assert report.missing == report.orphan == ()
    assert build.collection_name.startswith("narrative_test-build_")


async def test_prepare_build_closes_canonical_to_candidate_link(db_session):
    snapshot = await _accepted_source(db_session)
    await narrative_unit_materializer.materialize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    await narrative_canonicalizer.canonicalize_snapshot(
        db_session, snapshot_id=snapshot.id
    )
    service = NarrativeIndexingService(store=FakeStore())
    first = await service.prepare_build(
        db_session, snapshot_id=snapshot.id, config={"model": "test"}
    )
    second = await service.prepare_build(
        db_session, snapshot_id=snapshot.id, config={"model": "test"}
    )
    assert first.id == second.id
    assert first.status == "draft" and first.unit_count == 1
    assert len(first.manifest_checksum) == 64


async def test_reuses_exact_immutable_collection(db_session):
    build = await _candidate_build(db_session)
    store = FakeStore()
    service = NarrativeIndexingService(store=store)

    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]

    first = await service.build_candidate(db_session, build_id=build.id, embedder=embed)
    build.status = "draft"
    second = await service.build_candidate(
        db_session, build_id=build.id, embedder=embed
    )
    assert first.actual_ids == second.actual_ids


def test_vector_store_client_is_lazy():
    store = VectorStore(host="127.0.0.1", port=65530)
    assert store._client is None
