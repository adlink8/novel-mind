"""PostgreSQL paired runner integration for Phase 17."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services.chunking.manifests import content_hash
from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    load_frozen_bundle,
)
from app.services.narrative_memory.qualification_runner import (
    run_qualification,
    runner_has_promotion_capability,
    runner_persists_final_authority,
)
from app.services.narrative_memory.qualification_contracts import QualificationVerdict

pytestmark = pytest.mark.integration

HEX_A = "a" * 64
HEX_B = "b" * 64
CH1 = "第一章。林安走进客栈，看见桌上放着一封未拆的信。"
CH2 = "第二章。信中写着：三日后北门见。林安收起信封。"
CH3 = "第三章。三日后，林安在北门与神秘人会面，得知真相。"


async def _seed(session):
    owner_id = (
        await session.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('qr','qr@example.com','x',true,false) RETURNING id"
            )
        )
    ).scalar_one()
    novel_id = (
        await session.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:o,'QR','ready',3,100) RETURNING id"
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
                {"n": novel_id, "num": num, "t": f"C{num}", "c": body, "w": len(body)},
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
                    NULL,'[]',:body,:ch,:s,:e,'paragraph','[]',0
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
    return owner_id, novel_id, chapters


@pytest.mark.asyncio
async def test_paired_runner_pg(empty_postgres: str, pg_async_url: str):
    from tests.integration.conftest import run_alembic

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner_id, novel_id, chapters = await _seed(session)
            fixture, policy, _, _ = load_frozen_bundle(
                FIXTURES_DIR / "single_book_v1.json",
                FIXTURES_DIR / "policy_v1.json",
            )
            cases = []
            for case in fixture.cases:
                golds = [
                    leaf.model_copy(update={"chapter_id": chapters[leaf.chapter_number]})
                    for leaf in case.gold_leaves
                ]
                cases.append(case.model_copy(update={"gold_leaves": tuple(golds)}))
            fixture = fixture.model_copy(
                update={
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "cases": tuple(cases),
                }
            )
            result = await run_qualification(session, fixture, policy)
            assert result.report is not None
            assert len(result.artifacts) == 10  # 5 cases * 2 strategies
            strategies = {a["strategy"] for a in result.artifacts}
            assert strategies == {"hierarchical_candidate", "leaf_raw_baseline"}
            # common fields comparable
            by_case = {}
            for a in result.artifacts:
                by_case.setdefault(a["case_key"], []).append(a)
            for case_key, pair in by_case.items():
                assert len(pair) == 2
                assert pair[0]["case_key"] == pair[1]["case_key"]
            assert result.report.verdict in {
                QualificationVerdict.QUALIFIED_CANDIDATE,
                QualificationVerdict.BLOCKED,
            }
            assert runner_has_promotion_capability() is False
            assert runner_persists_final_authority() is False
    finally:
        await engine.dispose()
