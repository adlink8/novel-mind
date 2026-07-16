"""Fresh observer pointer equality tests."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    load_frozen_bundle,
)
from app.services.narrative_memory.qualification_verifier import (
    pointer_digest,
    snapshot_production_pointers,
    verify_qualification,
    verifier_has_promotion_capability,
    verifier_has_repair_capability,
)

pytestmark = pytest.mark.integration

HEX = "a" * 64


@pytest.mark.asyncio
async def test_pointer_snapshot_stable(empty_postgres: str, pg_async_url: str):
    from tests.integration.conftest import run_alembic

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await snapshot_production_pointers(session)
            d1 = pointer_digest(before)
            after = await snapshot_production_pointers(session)
            d2 = pointer_digest(after)
            assert d1 == d2
            assert "__unknown_selectors__" not in before or not before.get(
                "__unknown_selectors__"
            )

            fixture, policy, _, _ = load_frozen_bundle(
                FIXTURES_DIR / "single_book_v1.json",
                FIXTURES_DIR / "policy_v1.json",
            )
            vres = await verify_qualification(
                session,
                fixture=fixture,
                policy=policy,
                pointer_before=before,
                pointer_after=after,
                require_version_rows=False,
            )
            assert vres.ok
            assert len(vres.verifier_checksum) == 64
            assert verifier_has_promotion_capability() is False
            assert verifier_has_repair_capability() is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pointer_change_detected(empty_postgres: str, pg_async_url: str):
    from tests.integration.conftest import run_alembic

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await snapshot_production_pointers(session)
            # mutate a production pointer table if present
            owner = (
                await session.execute(
                    text(
                        "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                        "VALUES ('pv','pv@example.com','x',true,false) RETURNING id"
                    )
                )
            ).scalar_one()
            novel = (
                await session.execute(
                    text(
                        "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                        "VALUES (:o,'P','ready',1,1) RETURNING id"
                    ),
                    {"o": owner},
                )
            ).scalar_one()
            await session.execute(
                text(
                    """
                    INSERT INTO chunk_builds (
                        build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                        chunker_name,chunker_version,chunker_config_hash,collection_name,
                        is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                    ) VALUES (
                        'hb-p',:n,'committed',:h,:h,'semantic','1',:h,'c',false,true,
                        '[]','[]','[]'
                    )
                    """
                ),
                {"n": novel, "h": HEX},
            )
            # insert active pointer
            await session.execute(
                text(
                    """
                    INSERT INTO chunk_active_pointers (novel_id, build_id, committed_at)
                    VALUES (:n, 'hb-p', now())
                    """
                ),
                {"n": novel},
            )
            await session.commit()
            after = await snapshot_production_pointers(session)
            assert pointer_digest(before) != pointer_digest(after)
            fixture, policy, _, _ = load_frozen_bundle(
                FIXTURES_DIR / "single_book_v1.json",
                FIXTURES_DIR / "policy_v1.json",
            )
            vres = await verify_qualification(
                session,
                fixture=fixture,
                policy=policy,
                pointer_before=before,
                pointer_after=after,
                require_version_rows=False,
            )
            assert not vres.ok
            assert "pointer_before_after_mismatch" in vres.reasons
    finally:
        await engine.dispose()
