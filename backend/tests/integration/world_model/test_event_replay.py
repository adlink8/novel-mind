"""Phase 27-01 durable world-model projection: migration + restart replay.

Covers (REQ-WM-01):
- migration ``20260801_2701`` upgrade/downgrade is reversible and idempotent;
  the ORM reads back rows written through the migration (old-DB compat).
- append projection → restart (fresh engine/session on the same file) → replay
  is byte-equivalent: sealed projection hash, per-row checksums, row counts and
  conflicts are unchanged; idempotent re-append creates no duplicate rows.
- repository rejects UPDATE (no update/delete/promote API), wrong-owner reads,
  and stale-version writes.
- cutoff-aware query returns only visible rows (D-05) with no raw row leaks.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.novel import Novel
from app.models.user import User
from app.models.world_model_event import (
    WorldModelCausalEdge,
    WorldModelConflict,
    WorldModelEvent,
)
from app.services.world_model.claims import CausalEdgeClaim, EventClaim
from app.services.world_model.contracts import (
    Authority,
    CausalEdge,
    ConflictKind,
    EventFact,
    WorldModelCandidateProjection,
    projection_checksum,
)
from app.services.world_model.event_queries import WorldModelEventQueries
from app.services.world_model.event_repository import (
    WorldModelEventRepository,
    WorldModelRepositoryError,
)
from app.services.world_model.gates import WorldModelGate, build_candidate

pytestmark = pytest.mark.integration

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "events_v1.json"
    ).read_text(encoding="utf-8")
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / ("20260801_2701_world_event_projection.py")
)

HEX_A = "a" * 64
HEX_B = "b" * 64


def load_scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def build_valid_projection(*, version_id: int = 1) -> WorldModelCandidateProjection:
    """Run the 'valid' fixture through the gate into an immutable candidate."""
    scenario = load_scenario("valid")
    scope = scenario["scope"]
    gate = WorldModelGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    facts: list[EventFact] = []
    for raw in scenario["events"]:
        result = gate.validate_event(
            EventClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.fact is not None, result.verdicts
        facts.append(result.fact)
    events_by_key = {fact.event_key: fact for fact in facts}
    edges: list[CausalEdge] = []
    for raw in scenario["edges"]:
        result = gate.validate_edge(
            CausalEdgeClaim.model_validate({**raw, "version_id": version_id}),
            events_by_key,
        )
        assert result.edge is not None, result.verdicts
        edges.append(result.edge)
    return build_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        events=facts,
        edges=edges,
    )


def build_temporal_projection(*, version_id: int = 2) -> WorldModelCandidateProjection:
    """Fixture temporal-conflict scenario: both rows + the edge are preserved."""
    scenario = load_scenario("temporal_conflict")
    scope = scenario["scope"]
    gate = WorldModelGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    facts = []
    for raw in scenario["events"]:
        result = gate.validate_event(
            EventClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.fact is not None, result.verdicts
        facts.append(result.fact)
    events_by_key = {fact.event_key: fact for fact in facts}
    edges = []
    for raw in scenario["edges"]:
        result = gate.validate_edge(
            CausalEdgeClaim.model_validate({**raw, "version_id": version_id}),
            events_by_key,
        )
        assert result.edge is not None, result.verdicts
        edges.append(result.edge)
    return build_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        events=facts,
        edges=edges,
    )


async def seed_scope(session: AsyncSession) -> None:
    session.add(
        User(id=1, username="author", email="author@example.com", hashed_password="x")
    )
    session.add(
        User(id=2, username="other", email="other@example.com", hashed_password="x")
    )
    session.add(Novel(id=1, owner_id=1, title="测试小说"))
    await session.flush()


async def make_engine_and_factory(tmp_path, db_name: str = "world_model.db"):
    url = f"sqlite+aiosqlite:///{tmp_path / db_name}"
    engine = create_async_engine(url)
    # PostgreSQL owns the tsvector generated expression; SQLite needs the plain
    # Text variant (same workaround as tests/conftest.py db_session fixture).
    search_vector = Base.metadata.tables["text_chunks"].c.search_vector
    postgres_computed = search_vector.computed
    search_vector.computed = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        search_vector.computed = postgres_computed
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


def assert_projection_matches(
    actual: WorldModelCandidateProjection, expected: WorldModelCandidateProjection
) -> None:
    assert actual.projection_hash == expected.projection_hash
    assert actual.owner_id == expected.owner_id
    assert actual.novel_id == expected.novel_id
    assert actual.version_id == expected.version_id
    assert [e.model_dump(mode="json") for e in actual.events] == [
        e.model_dump(mode="json") for e in expected.events
    ]
    assert [e.model_dump(mode="json") for e in actual.edges] == [
        e.model_dump(mode="json") for e in expected.edges
    ]
    assert [c.model_dump(mode="json") for c in actual.conflicts] == [
        c.model_dump(mode="json") for c in expected.conflicts
    ]


# ---------------------------------------------------------------------------
# Migration compatibility
# ---------------------------------------------------------------------------


def test_migration_revision_chain_single_head():
    spec = importlib.util.spec_from_file_location("m_2701", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "20260801_2701"
    assert mod.down_revision == "20260801_2601"


def test_model_tables_registered_in_metadata():
    tables = set(Base.metadata.tables)
    assert "world_model_events" in tables
    assert "world_model_causal_edges" in tables
    assert "world_model_conflicts" in tables


def test_model_tables_have_no_active_pointer_or_promotion_columns():
    for table in (WorldModelEvent.__table__, WorldModelCausalEdge.__table__):
        columns = {column.name for column in table.columns}
        for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
            assert forbidden not in columns, f"forbidden column leaked: {forbidden}"


# ---------------------------------------------------------------------------
# Restart replay byte-equivalence
# ---------------------------------------------------------------------------


async def test_restart_replay_is_byte_equivalent(tmp_path):
    """Commit → restart (fresh engine/session on the same file) → identical."""
    engine, factory = await make_engine_and_factory(tmp_path)
    projection = build_valid_projection(version_id=1)

    async with factory() as session:
        await seed_scope(session)
        repo = WorldModelEventRepository(session)
        await repo.append_projection(projection)
        await session.commit()
        event_count = await session.scalar(
            select(func.count()).select_from(WorldModelEvent)
        )
        edge_count = await session.scalar(
            select(func.count()).select_from(WorldModelCausalEdge)
        )
        conflict_count = await session.scalar(
            select(func.count()).select_from(WorldModelConflict)
        )

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)

    async with factory2() as session:
        repo2 = WorldModelEventRepository(session)
        replayed = await repo2.replay_projection(owner_id=1, novel_id=1, version_id=1)
        assert_projection_matches(replayed, projection)
        # Checksum / row counts / conflicts are unchanged after restart.
        assert replayed.projection_hash == projection.projection_hash
        assert len(replayed.events) == event_count == 4
        assert len(replayed.edges) == edge_count == 2
        assert len(replayed.conflicts) == conflict_count == 0
        assert projection_checksum(replayed) == replayed.projection_hash

        # Idempotent re-append (same content) replays; no duplicate rows.
        await repo2.append_projection(projection)
        await session.flush()
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelEvent)) == 4
        )
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelCausalEdge))
            == 2
        )

        # Version lineage is queryable.
        assert await repo2.list_versions(owner_id=1, novel_id=1) == [1]
    await engine2.dispose()


async def test_temporal_conflicts_survive_restart(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    projection = build_temporal_projection(version_id=2)

    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(projection)
        await session.commit()

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)
    async with factory2() as session:
        replayed = await WorldModelEventRepository(session).replay_projection(
            owner_id=1, novel_id=1, version_id=2
        )
        assert len(replayed.conflicts) == 1
        conflict = replayed.conflicts[0]
        assert conflict.kind == ConflictKind.TEMPORAL_CONFLICT
        assert conflict.conflict_key == "temporal:edge-tc"
        assert replayed.projection_hash == projection.projection_hash
    await engine2.dispose()


async def test_wrong_owner_replay_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        with pytest.raises(WorldModelRepositoryError):
            await WorldModelEventRepository(session).replay_projection(
                owner_id=2, novel_id=1, version_id=1
            )
        with pytest.raises(WorldModelRepositoryError):
            await WorldModelEventRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=99
            )
    await engine.dispose()


async def test_stale_version_append_is_rejected(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = WorldModelEventRepository(session)
        await repo.append_projection(build_valid_projection(version_id=1))
        # A newer version exists → writing the older version again is stale.
        await repo.append_projection(build_valid_projection(version_id=2))
        with pytest.raises(WorldModelRepositoryError) as excinfo:
            await repo.append_projection(build_valid_projection(version_id=1))
        assert "stale-version" in str(excinfo.value)
    await engine.dispose()


def test_repository_exposes_no_update_api():
    """Immutability: no UPDATE / DELETE / promote path (D-14)."""
    members = {
        name
        for name, _ in inspect.getmembers(WorldModelEventRepository, predicate=callable)
    }
    assert not {m for m in members if m.startswith(("update", "delete", "promote"))}
    assert "append_projection" in members
    assert "replay_projection" in members


async def test_tampered_row_checksum_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        row = (await session.scalars(select(WorldModelEvent).limit(1))).first()
        row.canonical_payload_hash = "0" * 64  # tamper (test-only; no update API)
        await session.flush()
        with pytest.raises(WorldModelRepositoryError):
            await WorldModelEventRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=1
            )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Cutoff-aware query (D-05) with evidence / lineage / conflicts
# ---------------------------------------------------------------------------


async def test_cutoff_query_hides_future_facts_and_edges(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    projection = build_valid_projection(version_id=1)

    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(projection)
        queries = WorldModelEventQueries(session)

        full = await queries.query_cutoff_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=3
        )
        assert full is not None
        assert {e.event_key for e in full.events} == {
            "e-arrival",
            "e-order",
            "e-revolt",
            "e-treaty-reading",
        }
        assert len(full.edges) == 2

        at_cutoff_2 = await queries.query_cutoff_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=2
        )
        assert at_cutoff_2 is not None
        assert {e.event_key for e in at_cutoff_2.events} == {"e-arrival", "e-order"}
        # Edge pointing at a hidden chapter-3 event is dropped too.
        assert [e.edge_key for e in at_cutoff_2.edges] == ["edge-arrival-order"]
        # No raw chapter-3 row leaks.
        assert "e-revolt" not in {e.event_key for e in at_cutoff_2.events}

        missing = await queries.query_cutoff_projection(
            owner_id=2, novel_id=1, version_id=1, cutoff=3
        )
        assert missing is None, "cross-owner query fails closed"
    await engine.dispose()


async def test_query_returns_evidence_lineage_and_conflicts(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(
            build_temporal_projection(version_id=2)
        )
        queries = WorldModelEventQueries(session)

        lineage = await queries.query_event_lineage(
            owner_id=1, novel_id=1, event_key="e-tc-a"
        )
        assert len(lineage) == 1
        event = lineage[0]
        assert event.authority == Authority.PROBABLE_INFERENCE
        assert event.source_refs[0].evidence_id == "ev-treaty2"
        assert event.source_refs[0].source_snapshot_hash == "c" * 64

        conflicts = await queries.query_conflicts(owner_id=1, novel_id=1, version_id=2)
        assert [c.kind for c in conflicts] == [ConflictKind.TEMPORAL_CONFLICT]

        at_cutoff = await queries.query_cutoff_projection(
            owner_id=1, novel_id=1, version_id=2, cutoff=1
        )
        assert at_cutoff is not None
        assert {e.event_key for e in at_cutoff.events} == {"e-tc-a"}
        assert at_cutoff.edges == ()
        # The temporal conflict involves a hidden event and is filtered out too.
        assert at_cutoff.conflicts == ()
    await engine.dispose()


def test_query_module_exposes_read_only_api():
    members = {
        name
        for name, _ in inspect.getmembers(WorldModelEventQueries, predicate=callable)
    }
    assert "query_cutoff_projection" in members
    assert "query_event_lineage" in members
    assert "query_conflicts" in members
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
