"""PostgreSQL trust-boundary attacks for Phase 09 relationship graph."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.character import CharacterRelation
from app.models.novel import Novel
from app.schemas.relationship import RelationshipVersionSource
from app.services.relationships.query import RelationshipGraphQueryService
from tests.integration.conftest import run_alembic
from tests.integration.relationships.test_api import _async_session, _seed_graph

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_cross_owner_and_cross_version_produce_zero_visible_facts(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None

        foreign = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["other_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert foreign is None or (
            foreign.counts.nodes == 0 and foreign.counts.edges == 0
        )

        wrong_version = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            version_id=ids["v2_id"],
        )
        if wrong_version is not None:
            payload = wrong_version.model_dump_json()
            assert (
                "CANDIDATE_ONLY" in payload or wrong_version.version_id == ids["v2_id"]
            )

        active = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert active is not None
        assert "CANDIDATE_ONLY" not in active.model_dump_json()
        assert "LEGACY_SECRET" not in active.model_dump_json()

    await aengine.dispose()


@pytest.mark.asyncio
async def test_future_metadata_and_counts_never_leak_before_full_book(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        assert novel is not None
        envelope = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert envelope is not None
        blob = envelope.model_dump_json()
        assert "CarolFuture" not in blob
        assert "SECRET_FUTURE" not in blob
        assert envelope.counts.nodes >= 1
        assert envelope.counts.nodes == len(envelope.nodes)
        assert envelope.counts.edges == len(envelope.edges)
        assert ids["carol_id"] not in envelope.available_character_ids

        denied = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
            request_full_book=True,
        )
        assert denied is not None
        assert denied.full_book is False
        assert "CarolFuture" not in denied.model_dump_json()

    await aengine.dispose()


@pytest.mark.asyncio
async def test_legacy_character_relations_never_become_graph_facts(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_graph(engine)
    with Session(engine) as session:
        legacy = list(
            session.scalars(
                select(CharacterRelation).where(
                    CharacterRelation.novel_id == ids["novel_id"]
                )
            )
        )
        assert legacy, "seed must include a legacy row to prove isolation"
    engine.dispose()

    aengine, factory = await _async_session(empty_postgres)
    svc = RelationshipGraphQueryService()
    async with factory() as db:
        novel = await db.get(Novel, ids["novel_id"])
        envelope = await svc.build_graph(
            db,
            novel=novel,
            owner_id=ids["owner_id"],
            source=RelationshipVersionSource.ACTIVE,
        )
        assert envelope is not None
        assert "LEGACY_SECRET_RELATION" not in envelope.model_dump_json()
        assert "friend" not in {e.relation_type.value for e in envelope.edges}

    await aengine.dispose()
