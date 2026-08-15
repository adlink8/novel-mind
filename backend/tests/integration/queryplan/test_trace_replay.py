"""Phase 26-01 durable QueryPlanTrace restart-replay integration tests.

Covers: commit trace → restart session/process → replay by idempotency key with
identical payload/lineage/checksum and no second trace; unique-key re-append
replays the existing row; cross-owner reads fail closed; canonical-payload
checksum drift fails closed; no half trace on validation error; repository has
no update path (immutable, D-14).
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.novel import Novel
from app.models.queryplan import QueryPlanTrace
from app.models.user import User
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.repository import (
    QueryPlanRepository,
    QueryPlanRepositoryError,
)
from app.services.queryplan.schemas import QueryPlan

pytestmark = pytest.mark.integration

HEX_A = "a" * 64
HEX_B = "b" * 64


def reader_payload(**overrides) -> dict:
    base = {
        "intent": "reader",
        "owner_id": 1,
        "novel_id": 1,
        "version_id": 1,
        "question_text": "林安在第一章走进哪里？",
        "reading_progress": {
            "through_chapter": 3,
            "snapshot_hash": HEX_A,
            "full_book_authorized": False,
        },
        "source": "reader_chat",
        "dataset_lineage": "queryplan-questions-v1",
    }
    base.update(overrides)
    return base


def analysis_payload(**overrides) -> dict:
    base = reader_payload(
        intent="analysis",
        question_text="前两章里主角的性格如何变化？",
        chapter_range={"kind": "chapter_range", "chapter_start": 1, "chapter_end": 2},
    )
    base.update(overrides)
    return base


async def seed_scope(session: AsyncSession) -> None:
    session.add(
        User(id=1, username="reader", email="reader@example.com", hashed_password="x")
    )
    session.add(
        User(id=2, username="other", email="other@example.com", hashed_password="x")
    )
    session.add(Novel(id=1, owner_id=1, title="测试小说"))
    await session.flush()


async def make_engine_and_factory(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'trace_replay.db'}"
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


def assert_plan_matches_row(plan: QueryPlan, row: QueryPlanTrace) -> None:
    """Payload / lineage / checksum must be identical on replay."""

    assert row.trace_id == plan.trace.trace_id
    assert row.idempotency_key == plan.trace.idempotency_key
    assert row.owner_id == plan.owner_id
    assert row.novel_id == plan.novel_id
    assert row.version_id == plan.version_id
    assert row.cutoff_mode == plan.spoiler_cutoff.mode.value
    assert row.through_chapter == plan.spoiler_cutoff.through_chapter
    assert row.full_book_authorized == plan.spoiler_cutoff.full_book_authorized
    assert row.schema_version == plan.schema_version
    assert row.parser_version == plan.parser_version
    assert row.source == plan.trace.source
    assert row.dataset_lineage == plan.trace.dataset_lineage
    assert row.canonical_payload_hash == plan.trace.canonical_payload_hash
    assert row.availability_checksum == plan.trace.availability_checksum
    assert row.canonical_payload == plan.model_dump(mode="json", exclude={"trace"})
    assert row.blocked_reason is None


async def test_restart_replay_payload_lineage_checksum_identical(tmp_path):
    """Commit → restart (new engine/session on the same file) → replay is identical."""

    engine, factory = await make_engine_and_factory(tmp_path)
    result = parse_query_plan(analysis_payload())
    assert isinstance(result, QueryPlan)

    # Session A: commit one trace.
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        row = await repo.append_trace(result)
        await session.commit()
        first_id = row.id

    # "Restart": dispose the engine and open a fresh engine/session on the file.
    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)

    async with factory2() as session:
        repo2 = QueryPlanRepository(session)
        replayed = await repo2.replay_by_key(
            owner_id=result.owner_id,
            idempotency_key=result.trace.idempotency_key,
        )
        assert_plan_matches_row(result, replayed)
        assert replayed.id == first_id
        # Re-append with the same idempotency key (fresh parse, new trace_id):
        # must replay the existing row and create no second trace.
        reparse = parse_query_plan(analysis_payload())
        assert isinstance(reparse, QueryPlan)
        assert reparse.trace.idempotency_key == result.trace.idempotency_key
        assert reparse.trace.trace_id != result.trace.trace_id
        replayed_again = await repo2.append_trace(reparse)
        assert replayed_again.id == first_id
        assert replayed_again.trace_id == result.trace.trace_id

        count = await session.scalar(select(func.count()).select_from(QueryPlanTrace))
        assert count == 1, "idempotency replay must not create a second trace"

    await engine2.dispose()


async def test_reappend_same_key_replays_existing_row(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        first = await repo.append_trace(parse_query_plan(reader_payload()))
        second = await repo.append_trace(parse_query_plan(reader_payload()))
        assert isinstance(first, QueryPlanTrace)
        assert second.id == first.id
        count = await session.scalar(select(func.count()).select_from(QueryPlanTrace))
        assert count == 1
    await engine.dispose()


async def test_cross_owner_replay_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        plan = parse_query_plan(analysis_payload())
        assert isinstance(plan, QueryPlan)
        await repo.append_trace(plan)

        with pytest.raises(QueryPlanRepositoryError):
            await repo.replay_by_key(
                owner_id=2, idempotency_key=plan.trace.idempotency_key
            )
        with pytest.raises(QueryPlanRepositoryError):
            await repo.replay_by_trace_id(owner_id=2, trace_id=plan.trace.trace_id)
    await engine.dispose()


async def test_scope_mismatch_reappend_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        plan = parse_query_plan(analysis_payload())
        assert isinstance(plan, QueryPlan)
        await repo.append_trace(plan)
        # Same idempotency key but a different owner scope must not replay.
        hijack = plan.model_copy(update={"owner_id": 2})
        with pytest.raises(QueryPlanRepositoryError):
            await repo.append_trace(hijack)
        count = await session.scalar(select(func.count()).select_from(QueryPlanTrace))
        assert count == 1
    await engine.dispose()


async def test_checksum_drift_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        plan = parse_query_plan(analysis_payload())
        assert isinstance(plan, QueryPlan)
        await repo.append_trace(plan)
        row = await repo.replay_by_key(
            owner_id=plan.owner_id, idempotency_key=plan.trace.idempotency_key
        )
        row.canonical_payload_hash = (
            "0" * 64
        )  # tamper (test-only; repo has no update API)
        await session.flush()

        with pytest.raises(QueryPlanRepositoryError):
            await repo.replay_by_key(
                owner_id=plan.owner_id, idempotency_key=plan.trace.idempotency_key
            )
    await engine.dispose()


async def test_validation_error_leaves_no_half_trace(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        plan = parse_query_plan(analysis_payload())
        assert isinstance(plan, QueryPlan)
        # Corrupt the trace's payload hash so append validation fails before write.
        broken = plan.model_copy(
            update={
                "trace": plan.trace.model_copy(
                    update={"canonical_payload_hash": "0" * 64}
                )
            }
        )
        with pytest.raises(QueryPlanRepositoryError):
            await repo.append_trace(broken)
        count = await session.scalar(select(func.count()).select_from(QueryPlanTrace))
        assert count == 0, "failed append must leave no half trace"
    await engine.dispose()


async def test_list_for_scope_is_owner_scoped_and_ordered(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = QueryPlanRepository(session)
        first = parse_query_plan(reader_payload(question_text="主角此刻在哪里？"))
        second = parse_query_plan(reader_payload(question_text="线索是什么？"))
        assert isinstance(first, QueryPlan)
        assert isinstance(second, QueryPlan)
        await repo.append_trace(first)
        await repo.append_trace(second)

        rows = await repo.list_for_scope(owner_id=1, novel_id=1, version_id=1)
        assert [r.id for r in rows] == sorted(r.id for r in rows)
        assert len(rows) == 2

        empty = await repo.list_for_scope(owner_id=2, novel_id=1, version_id=1)
        assert empty == []
    await engine.dispose()


def test_repository_exposes_no_update_api():
    """Immutability: the repository must not expose any update/delete method (D-14)."""

    members = {
        name for name, _ in inspect.getmembers(QueryPlanRepository, predicate=callable)
    }
    assert not {m for m in members if m.startswith(("update", "delete", "promote"))}
    assert "append_trace" in members
    assert "replay_by_key" in members


def test_trace_table_has_no_active_pointer_or_promotion_columns():
    """D-14: the durable table carries no promotion / active-pointer columns."""

    columns = {column.name for column in QueryPlanTrace.__table__.columns}
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in columns, f"forbidden column leaked: {forbidden}"
