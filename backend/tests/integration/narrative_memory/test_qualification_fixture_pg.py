"""PostgreSQL gold-leaf prevalidation for Phase 17 fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.chunking.manifests import content_hash
from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    load_frozen_bundle,
    prevalidate_fixture_against_pg,
    freeze_paired_case_matrix,
    module_has_forbidden_capability,
)
from pathlib import Path

pytestmark = pytest.mark.integration

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64

CH1 = "第一章。林安走进客栈，看见桌上放着一封未拆的信。"
CH2 = "第二章。信中写着：三日后北门见。林安收起信封。"
CH3 = "第三章。三日后，林安在北门与神秘人会面，得知真相。"


async def _seed(session: AsyncSession, owner_id: int = 1, novel_id: int = 1) -> dict:
    await session.execute(
        text(
            "INSERT INTO users (id,username,email,hashed_password,is_active,is_superuser) "
            "VALUES (:id,'quser','q@example.com','x',true,false) "
            "ON CONFLICT DO NOTHING"
        ),
        {"id": owner_id},
    )
    # users table may not allow explicit id easily — use RETURNING
    row = await session.execute(
        text(
            "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
            "VALUES ('quser2','q2@example.com','x',true,false) RETURNING id"
        )
    )
    owner_id = row.scalar_one()
    novel_id = (
        await session.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:o,'Qual Book','ready',3,100) RETURNING id"
            ),
            {"o": owner_id},
        )
    ).scalar_one()
    chapters = {}
    for num, body in ((1, CH1), (2, CH2), (3, CH3)):
        cid = (
            await session.execute(
                text(
                    "INSERT INTO chapters (novel_id,chapter_number,title,content,word_count) "
                    "VALUES (:n,:num,:t,:c,:w) RETURNING id"
                ),
                {
                    "n": novel_id,
                    "num": num,
                    "t": f"Ch{num}",
                    "c": body,
                    "w": len(body),
                },
            )
        ).scalar_one()
        chapters[num] = cid
    await session.execute(
        text(
            """
            INSERT INTO chunk_builds (
                build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                chunker_name,chunker_version,chunker_config_hash,collection_name,
                is_candidate,immutable,changed_chapter_ids,journal,vector_ids
            ) VALUES (
                'hb-qual-v1',:n,'committed',:h,:hb,'semantic','1',:h,'c',false,true,
                '[]','[]','[]'
            )
            """
        ),
        {"n": novel_id, "h": HEX_A, "hb": HEX_B},
    )
    # hierarchy leaf nodes optional
    for num, leaf_id, start, end in (
        (1, "leaf-ch1-01", 4, 12),
        (2, "leaf-ch2-01", 4, 18),
        (3, "leaf-ch3-01", 7, 20),
    ):
        body = {1: CH1, 2: CH2, 3: CH3}[num]
        slice_text = body[start:end]
        await session.execute(
            text(
                """
                INSERT INTO chunk_hierarchy_nodes (
                    build_id,novel_id,node_id,level,chapter_id,chapter_number,
                    parent_id,child_ids,content,content_hash,source_start,source_end,
                    chunk_type,decision_lineage,order_index
                ) VALUES (
                    'hb-qual-v1',:n,:lid,'evidence',:cid,:num,
                    NULL,'[]',:body,:ch,:s,:e,
                    'paragraph','[]',0
                )
                """
            ),
            {
                "n": novel_id,
                "lid": leaf_id,
                "cid": chapters[num],
                "num": num,
                "s": start,
                "e": end,
                "ch": content_hash(slice_text),
                "body": slice_text,
            },
        )
    await session.commit()
    return {"owner_id": owner_id, "novel_id": novel_id, "chapters": chapters}


@pytest.mark.asyncio
async def test_gold_leaf_prevalidation_pg(empty_postgres: str, pg_async_url: str):
    from tests.integration.conftest import run_alembic

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            seeded = await _seed(session)
            fixture, policy, fx, pol = load_frozen_bundle(
                FIXTURES_DIR / "single_book_v1.json",
                FIXTURES_DIR / "policy_v1.json",
            )
            # rewrite fixture scope to seeded ids while preserving gold hashes
            cases = []
            for case in fixture.cases:
                golds = []
                for leaf in case.gold_leaves:
                    ch_num = leaf.chapter_number
                    golds.append(
                        leaf.model_copy(
                            update={
                                "chapter_id": seeded["chapters"][ch_num],
                            }
                        )
                    )
                cases.append(case.model_copy(update={"gold_leaves": tuple(golds)}))
            adapted = fixture.model_copy(
                update={
                    "owner_id": seeded["owner_id"],
                    "novel_id": seeded["novel_id"],
                    "cases": tuple(cases),
                }
            )
            errors = await prevalidate_fixture_against_pg(session, adapted)
            assert errors == [], errors
            pairs = freeze_paired_case_matrix(adapted, policy)
            assert len(pairs) == 5
            assert fx and pol
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tampered_gold_hash_fails(empty_postgres: str, pg_async_url: str):
    from tests.integration.conftest import run_alembic

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            seeded = await _seed(session)
            fixture, _, _, _ = load_frozen_bundle(
                FIXTURES_DIR / "single_book_v1.json",
                FIXTURES_DIR / "policy_v1.json",
            )
            cases = []
            for case in fixture.cases:
                golds = []
                for leaf in case.gold_leaves:
                    golds.append(
                        leaf.model_copy(
                            update={
                                "chapter_id": seeded["chapters"][leaf.chapter_number],
                                "content_hash": "f" * 64,
                            }
                        )
                    )
                cases.append(case.model_copy(update={"gold_leaves": tuple(golds)}))
            adapted = fixture.model_copy(
                update={
                    "owner_id": seeded["owner_id"],
                    "novel_id": seeded["novel_id"],
                    "cases": tuple(cases),
                }
            )
            errors = await prevalidate_fixture_against_pg(session, adapted)
            assert any("content_hash mismatch" in e for e in errors)
    finally:
        await engine.dispose()


def test_freeze_modules_no_result_readers():
    base = Path(__file__).resolve().parents[3] / "app" / "services" / "narrative_memory"
    for name in ("qualification_contracts.py", "qualification_fixtures.py"):
        hits = module_has_forbidden_capability(base / name)
        assert not any(h.startswith("import:reader") for h in hits)
