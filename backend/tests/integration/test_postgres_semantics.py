"""
PostgreSQL 16 semantic contracts on real CI database (not SQLite).

Covers (D-05):
- tsvector generated column + GIN index query behavior
- uniqueness / FK constraints
- owner isolation
- import lease + promotion journal concurrency where feasible
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.import_job import ImportJob
from app.models.knowledge_unit import (
    NarrativeIndexBuild,
    NarrativePromotionJournal,
    NarrativeSourceSnapshot,
)
from app.models.novel import Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.import_service import ImportService
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_db(pg_sync_url, pg_async_url, require_postgres):
    """Reset + upgrade heads so semantic tests start from a clean migrated schema."""
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    return pg_async_url


@pytest.fixture
async def session_factory(migrated_db):
    engine = create_async_engine(migrated_db, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_user(session: AsyncSession, username: str, email: str) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password="!test-hash",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def test_tsvector_generated_and_searchable(session_factory):
    """text_chunks.search_vector is real tsvector with working GIN path."""
    async with session_factory() as session:
        user = await _make_user(session, "tsv_owner", "tsv@example.com")
        novel = Novel(title="TSVector Novel", owner_id=user.id, status="ready")
        session.add(novel)
        await session.flush()
        chunk = TextChunk(
            novel_id=novel.id,
            chunk_index=0,
            content="林黛玉在潇湘馆 recites poetry under moonlight",
            chunk_type="narration",
            word_count=10,
            embedding_status="pending",
        )
        session.add(chunk)
        await session.commit()
        chunk_id = chunk.id

    async with session_factory() as session:
        # Generated column type
        row = (
            await session.execute(
                text(
                    """
                    SELECT pg_typeof(search_vector)::text, search_vector IS NOT NULL
                    FROM text_chunks WHERE id = :id
                    """
                ),
                {"id": chunk_id},
            )
        ).one()
        assert row[0] == "tsvector"
        assert row[1] is True

        # Full-text match via GIN-backed @@
        hits = (
            await session.execute(
                text(
                    """
                    SELECT id FROM text_chunks
                    WHERE search_vector @@ to_tsquery('simple', 'poetry')
                    """
                )
            )
        ).fetchall()
        assert any(h[0] == chunk_id for h in hits)

        # Rank function available
        rank = (
            await session.execute(
                text(
                    """
                    SELECT ts_rank(search_vector, to_tsquery('simple', 'poetry'))
                    FROM text_chunks WHERE id = :id
                    """
                ),
                {"id": chunk_id},
            )
        ).scalar()
        assert rank is not None and float(rank) > 0


async def test_unique_and_fk_constraints(session_factory):
    """Username/email uniqueness and novel.owner_id FK enforced by PostgreSQL."""
    async with session_factory() as session:
        await _make_user(session, "alice", "alice@example.com")
        await session.commit()

    async with session_factory() as session:
        session.add(
            User(
                username="alice",
                email="alice2@example.com",
                hashed_password="x",
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_factory() as session:
        session.add(
            User(
                username="bob",
                email="alice@example.com",
                hashed_password="x",
                is_active=True,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_factory() as session:
        session.add(Novel(title="orphan", owner_id=999999, status="ready"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_owner_isolation(session_factory):
    """Novels are scoped by owner_id; cross-owner queries do not leak rows."""
    async with session_factory() as session:
        a = await _make_user(session, "owner_a", "a@example.com")
        b = await _make_user(session, "owner_b", "b@example.com")
        session.add_all(
            [
                Novel(title="A Book", owner_id=a.id, status="ready"),
                Novel(title="B Book", owner_id=b.id, status="ready"),
            ]
        )
        await session.commit()
        a_id, b_id = a.id, b.id

    async with session_factory() as session:
        a_novels = list(
            (
                await session.scalars(select(Novel).where(Novel.owner_id == a_id))
            ).all()
        )
        b_novels = list(
            (
                await session.scalars(select(Novel).where(Novel.owner_id == b_id))
            ).all()
        )
        assert len(a_novels) == 1 and a_novels[0].title == "A Book"
        assert len(b_novels) == 1 and b_novels[0].title == "B Book"
        assert a_novels[0].id != b_novels[0].id


async def test_import_lease_exclusive_while_valid(session_factory):
    """Second acquire fails while first lease is still valid (import concurrency)."""
    service = ImportService()
    async with session_factory() as session:
        user = await _make_user(session, "lease_owner", "lease@example.com")
        novel = Novel(title="Lease Novel", owner_id=user.id, status="importing")
        session.add(novel)
        await session.flush()
        job = ImportJob(novel_id=novel.id, status="pending", progress=0)
        session.add(job)
        await session.commit()
        job_id = job.id

    async with session_factory() as s1:
        ok1 = await service.acquire_lease(s1, job_id)
        await s1.commit()
        assert ok1 is True

    async with session_factory() as s2:
        ok2 = await service.acquire_lease(s2, job_id)
        await s2.commit()
        assert ok2 is False  # lease still held

    async with session_factory() as session:
        job = await session.get(ImportJob, job_id)
        assert job is not None and job.lease_id is not None
        # Force expiry so recovery path can re-acquire.
        job.lease_expires_at = datetime(2000, 1, 1, tzinfo=UTC)
        await session.commit()

    async with session_factory() as s3:
        ok3 = await service.acquire_lease(s3, job_id)
        await s3.commit()
        assert ok3 is True


async def test_promotion_journal_transaction_key_unique(session_factory):
    """Promotion journal transaction_key uniqueness (promotion concurrency gate)."""
    async with session_factory() as session:
        user = await _make_user(session, "promo_owner", "promo@example.com")
        novel = Novel(title="Promo Novel", owner_id=user.id, status="ready")
        session.add(novel)
        await session.flush()
        snapshot = NarrativeSourceSnapshot(
            owner_id=user.id,
            novel_id=novel.id,
            domain_profile="fiction",
            ontology_profile="fiction.v1",
            status="frozen",
            source_watermark="wm-promo-1",
            manifest_checksum="a" * 64,
            item_count=1,
        )
        session.add(snapshot)
        await session.flush()
        builds = [
            NarrativeIndexBuild(
                owner_id=user.id,
                novel_id=novel.id,
                source_snapshot_id=snapshot.id,
                domain_profile="fiction",
                build_key=f"promo-build-{i}",
                status="candidate",
                manifest_checksum=f"{i}" * 64,
                config_checksum="c" * 64,
                unit_count=1,
            )
            for i in (1, 2)
        ]
        session.add_all(builds)
        await session.flush()
        key = "tx-unique-key-integration-01"
        session.add(
            NarrativePromotionJournal(
                owner_id=user.id,
                novel_id=novel.id,
                domain_profile="fiction",
                candidate_build_id=builds[0].id,
                transaction_key=key,
                status="prepared",
                candidate_checksum=builds[0].manifest_checksum,
                details={},
            )
        )
        await session.commit()
        owner_id, novel_id, second_build_id = user.id, novel.id, builds[1].id

    async with session_factory() as session:
        session.add(
            NarrativePromotionJournal(
                owner_id=owner_id,
                novel_id=novel_id,
                domain_profile="fiction",
                candidate_build_id=second_build_id,
                transaction_key=key,
                status="prepared",
                candidate_checksum="2" * 64,
                details={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_concurrent_duplicate_username_one_wins(session_factory):
    """Two concurrent inserts of the same username: exactly one commits."""

    async def try_insert(label: str) -> bool:
        async with session_factory() as session:
            session.add(
                User(
                    username="race_user",
                    email=f"{label}@example.com",
                    hashed_password="x",
                    is_active=True,
                )
            )
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    results = await asyncio.gather(try_insert("r1"), try_insert("r2"))
    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 1
