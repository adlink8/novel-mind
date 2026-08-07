"""PostgreSQL 16 schema contracts for the three knowledge spaces (Phase 35-01).

Validates the D-35-01/D-35-03 DB boundary on the real CI database:
- frozen lineage columns (source_snapshot_hash / through_chapter /
  full_book_authorized / read_only) exist after the 35_canon_space01 migration;
- the composite scope unique constraint treats space as part of identity, so the
  same version key may exist in different spaces but never twice in one space;
- the read-only marker is bound to the Original Canon space in both directions;
- invalid space / short snapshot hash / zero cutoff are rejected at the DB;
- rows are append-only: lineage mutation and delete fail closed;
- an Original-scope query never returns derivative rows;
- the migration upgrades and downgrades symmetrically (round trip).
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.canon_space import CanonSpaceArtifact
from app.models.novel import Novel
from app.models.user import User
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64

# Revision 35_canon_space01 sits on top of the Phase 34 head.
PREV_REVISION = "20260801_illustration_anchors"


@pytest.fixture
def migrated_db(pg_sync_url, pg_async_url, require_postgres):
    """Reset + upgrade heads so schema tests start from a clean migrated DB."""
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


async def _make_user(session: AsyncSession, suffix: str = "csp") -> User:
    user = User(
        username=f"owner_{suffix}",
        email=f"{suffix}@example.com",
        hashed_password="!test-hash",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_novel(session: AsyncSession, user: User) -> Novel:
    novel = Novel(title="Canon Space Novel", owner_id=user.id, status="ready")
    session.add(novel)
    await session.flush()
    return novel


def _artifact_insert(owner_id: int, novel_id: int, **overrides) -> dict:
    payload = {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "space": "fanfiction_canon",
        "namespace": "ff:1",
        "version_key": "v1",
        "authority": "creative_draft",
        "citation_policy": "fanfiction_only",
        "status": "draft",
        "content_hash": HEX64,
        "content": "derivative draft",
        "source_snapshot_hash": HEX64_B,
        "through_chapter": 3,
        "full_book_authorized": False,
        "read_only": False,
    }
    payload.update(overrides)
    cols = ", ".join(payload)
    placeholders = ", ".join(f":{col}" for col in payload)
    return text(
        f"INSERT INTO canon_space_artifacts ({cols}) VALUES ({placeholders})"
    ), payload


async def test_frozen_lineage_columns_exist_after_migration(migrated_db):
    sync_url = migrated_db.replace("+asyncpg", "+psycopg2")
    engine = create_async_engine(migrated_db, echo=False)
    async with engine.begin() as conn:
        cols = {
            col["name"]
            for col in await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_columns(
                    "canon_space_artifacts"
                )
            )
        }
    await engine.dispose()
    assert sync_url  # ensure the url shape is real (never a silent fallback)
    for expected in (
        "source_snapshot_hash",
        "through_chapter",
        "full_book_authorized",
        "read_only",
        "space",
        "namespace",
        "version_key",
        "authority",
        "citation_policy",
    ):
        assert expected in cols, f"missing migrated column: {expected}"


async def test_same_version_key_is_space_scoped(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "scope")
        novel = await _make_novel(session, user)
        # Same version key in a different space is a distinct identity.
        for space_spec in (
            {
                "space": "original_canon",
                "namespace": "orig:1",
                "authority": "source_text",
                "citation_policy": "original_leaf",
                "read_only": True,
            },
            {
                "space": "fanfiction_canon",
                "namespace": "ff:1",
                "authority": "creative_draft",
                "citation_policy": "fanfiction_only",
                "read_only": False,
            },
        ):
            stmt, params = _artifact_insert(
                user.id, novel.id, version_key="shared-v1", **space_spec
            )
            await session.execute(stmt, params)
        await session.commit()
        # Duplicate in the SAME space/namespace/version is rejected.
        stmt, params = _artifact_insert(
            user.id,
            novel.id,
            version_key="shared-v1",
            space="original_canon",
            namespace="orig:1",
            authority="source_text",
            citation_policy="original_leaf",
            read_only=True,
        )
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()


async def test_read_only_marker_bound_to_original_space(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "ro")
        novel = await _make_novel(session, user)
        # Commit owner/novel so constraint attempts below only affect the
        # artifact row (never the owner/novel scaffolding).
        await session.commit()
        user_id = user.id
        novel_id = novel.id
        # Derivative row claiming read_only fails.
        stmt, params = _artifact_insert(user_id, novel_id, read_only=True)
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()
        # Original row without read_only fails.
        stmt, params = _artifact_insert(
            user_id,
            novel_id,
            space="original_canon",
            namespace="orig:1",
            authority="source_text",
            citation_policy="original_leaf",
            read_only=False,
        )
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()
        # Original row with read_only is the only valid shape.
        stmt, params = _artifact_insert(
            user_id,
            novel_id,
            space="original_canon",
            namespace="orig:2",
            authority="source_text",
            citation_policy="original_leaf",
            read_only=True,
        )
        await session.execute(stmt, params)
        await session.commit()


async def test_invalid_space_rejected_by_db(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "space")
        novel = await _make_novel(session, user)
        stmt, params = _artifact_insert(
            user.id, novel.id, space="narrative_memory", read_only=False
        )
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()


async def test_short_snapshot_hash_rejected_by_db(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "hash")
        novel = await _make_novel(session, user)
        stmt, params = _artifact_insert(user.id, novel.id, source_snapshot_hash="short")
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()


async def test_zero_cutoff_rejected_by_db(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "cutoff")
        novel = await _make_novel(session, user)
        stmt, params = _artifact_insert(user.id, novel.id, through_chapter=0)
        with pytest.raises(IntegrityError):
            await session.execute(stmt, params)
            await session.commit()
        await session.rollback()


async def test_append_only_lineage_rejects_mutation_and_delete(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "append")
        novel = await _make_novel(session, user)
        # Persist owner/novel so the rollback after the mutation attempt below
        # never invalidates the scaffolding FKs.
        await session.commit()
        user_id = user.id
        novel_id = novel.id
        artifact = CanonSpaceArtifact(
            owner_id=user_id,
            novel_id=novel_id,
            space="user_interpretation",
            namespace="user:1",
            version_key="v1",
            authority="user_assertion",
            citation_policy="interpretation_with_original_refs",
            status="draft",
            content_hash=HEX64,
            content="interpretation row",
            source_snapshot_hash=HEX64_B,
            through_chapter=2,
            full_book_authorized=False,
        )
        session.add(artifact)
        await session.flush()
        artifact.content = "tampered lineage"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()

        # Re-insert a clean row and commit it so the status projection can be
        # exercised on the persisted row.
        artifact = CanonSpaceArtifact(
            owner_id=user_id,
            novel_id=novel_id,
            space="user_interpretation",
            namespace="user:2",
            version_key="v1",
            authority="user_assertion",
            citation_policy="interpretation_with_original_refs",
            status="draft",
            content_hash=HEX64,
            content="interpretation row",
            source_snapshot_hash=HEX64_B,
            through_chapter=2,
            full_book_authorized=False,
        )
        session.add(artifact)
        await session.commit()

    # Status is the only mutable projection.
    async with session_factory() as session2:
        row = (
            await session2.execute(
                select(CanonSpaceArtifact).where(
                    CanonSpaceArtifact.namespace == "user:2"
                )
            )
        ).scalar_one()
        row.status = "accepted"
        await session2.commit()

    async with session_factory() as session3:
        row = (
            await session3.execute(
                select(CanonSpaceArtifact).where(
                    CanonSpaceArtifact.namespace == "user:2"
                )
            )
        ).scalar_one()
        assert row.status == "accepted"
        await session3.delete(row)
        with pytest.raises(ValueError, match="immutable"):
            await session3.commit()


async def test_original_scope_query_never_returns_derivative_rows(session_factory):
    async with session_factory() as session:
        user = await _make_user(session, "iso")
        novel = await _make_novel(session, user)
        for space_spec in (
            {
                "space": "original_canon",
                "namespace": "orig:1",
                "authority": "source_text",
                "citation_policy": "original_leaf",
                "read_only": True,
                "content": "original canon text",
            },
            {
                "space": "user_interpretation",
                "namespace": "user:1",
                "authority": "user_assertion",
                "citation_policy": "interpretation_with_original_refs",
                "read_only": False,
                "content": "user interpretation text",
            },
            {
                "space": "fanfiction_canon",
                "namespace": "ff:1",
                "authority": "creative_draft",
                "citation_policy": "fanfiction_only",
                "read_only": False,
                "content": "fanfiction derivative text",
            },
        ):
            stmt, params = _artifact_insert(user.id, novel.id, **space_spec)
            await session.execute(stmt, params)
        await session.commit()

        original_rows = (
            await session.execute(
                text(
                    "SELECT namespace, content FROM canon_space_artifacts "
                    "WHERE owner_id = :owner AND novel_id = :novel "
                    "AND space = 'original_canon'"
                ),
                {"owner": user.id, "novel": novel.id},
            )
        ).all()
        assert len(original_rows) == 1
        assert original_rows[0].namespace == "orig:1"
        assert "interpretation" not in original_rows[0].content
        assert "fanfiction" not in original_rows[0].content


async def test_migration_round_trip(pg_sync_url, pg_async_url, require_postgres):
    """Upgrade -> downgrade -> upgrade is symmetric and loses no columns."""
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    run_alembic("downgrade", PREV_REVISION, database_url=pg_sync_url)

    engine = create_async_engine(pg_async_url, echo=False)
    async with engine.begin() as conn:
        cols = {
            col["name"]
            for col in await conn.run_sync(
                lambda sync: inspect(sync).get_columns("canon_space_artifacts")
            )
        }
        assert "source_snapshot_hash" not in cols
        assert "read_only" not in cols
    await engine.dispose()

    run_alembic("upgrade", "heads", database_url=pg_sync_url)
    current = run_alembic("current", database_url=pg_sync_url)
    # Phase 35-02 adds the immutable canon_forks head on top of canon_space01;
    # Phase 35-04 adds the contamination block audit head on top of that;
    # Phase 36-01 adds the owner-scoped derivative project head on top;
    # Phase 36-03 adds the append-only derivative revision head on top.
    assert "20260801_derivative_revision01" in current.stdout
    check = run_alembic("check", database_url=pg_sync_url)
    assert check.returncode == 0, (
        f"alembic check failed:\n{check.stdout}\n{check.stderr}"
    )
