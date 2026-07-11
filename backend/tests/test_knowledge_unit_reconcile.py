"""Exact actual-ID and lifecycle residue reconciliation tests."""

from sqlalchemy import select

from app.models.knowledge_unit import NarrativeUnit
from app.services.knowledge_units.indexing import NarrativeIndexingService
from app.services.knowledge_units.reconcile import reconcile_build
from tests.test_knowledge_unit_indexing import FakeStore, _candidate_build


async def _indexed(db):
    build = await _candidate_build(db)
    store = FakeStore()
    async def embed(texts):
        return [[0.1, 0.2] for _ in texts]
    report = await NarrativeIndexingService(store).build_candidate(db, build_id=build.id, embedder=embed)
    collection = store.collections[report.collection_name]
    actual = [{"id": item_id, "metadata": metadata} for item_id, metadata in zip(collection.ids, collection.metadatas)]
    return build, actual


async def test_clean_actual_ids_reconcile(db_session):
    build, actual = await _indexed(db_session)
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert report.passed


async def test_orphan_and_wrong_owner_fail_reconcile(db_session):
    build, actual = await _indexed(db_session)
    actual.append({"id": "unit_orphan", "metadata": {"owner_id": 999, "novel_id": build.novel_id}})
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert report.orphan == ("unit_orphan",)
    assert report.wrong_owner == ("unit_orphan",)
    assert not report.passed


async def test_deprecated_unit_is_zero_residue_gate(db_session):
    build, actual = await _indexed(db_session)
    unit = await db_session.scalar(select(NarrativeUnit))
    unit.lifecycle_status = "deprecated"
    unit.status = "deprecated"
    report = await reconcile_build(db_session, build_id=build.id, actual_items=actual)
    assert actual[0]["id"] in report.deprecated
    assert not report.passed
