"""Phase 40 第二层物化：key_scenes 域表 candidate 行（阶段 A 集成测试）。

覆盖：
- scene_candidate artifact → key_scene_sets/candidates/evidence_ranges candidate 行
- 幂等（重复 materialize 不重复写）
- 快照过期 → skipped:stale_source_snapshot（零写入）

依赖真实 Postgres（CI PG @ 5433）+ alembic 迁移。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.agent_runtime.materialize import materialize_skill_run
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

CH_CONTENT = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    "We attack at dawn! he said. Mara drew her sword and charged."
)


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield f
    finally:
        await engine.dispose()


async def _seed_novel_with_chapter(f, *, suffix: str) -> dict[str, Any]:
    from app.schemas.agent_runtime import SkillVersionRegister
    from app.services.agent_runtime.registry import register_skill_version

    async with f() as session:
        user = (
            await session.execute(
                text(
                    "INSERT INTO users (username, email, hashed_password, is_active, is_superuser, created_at, updated_at) "
                    "VALUES (:u, :e, :p, true, true, now(), now()) RETURNING id"
                ),
                {"u": f"p40ks_{suffix}", "e": f"p40ks_{suffix}@example.com", "p": "x"},
            )
        ).scalar()
        novel = (
            await session.execute(
                text(
                    "INSERT INTO novels (owner_id, title, author, status, chapter_count, word_count, created_at, updated_at) "
                    "VALUES (:o, :t, 't', 'ready', 1, 10, now(), now()) RETURNING id"
                ),
                {"o": user, "t": f"p40ks-book-{suffix}"},
            )
        ).scalar()
        chapter = (
            await session.execute(
                text(
                    "INSERT INTO chapters (novel_id, chapter_number, title, content, word_count, created_at, updated_at) "
                    "VALUES (:n, 1, 'ch1', :c, :wc, now(), now()) RETURNING id"
                ),
                {"n": novel, "c": CH_CONTENT, "wc": len(CH_CONTENT)},
            )
        ).scalar()
        contract = SkillVersionRegister.model_validate(
            {
                "novel_id": novel,
                "name": "detect-key-scenes",
                "version": "1.0.0",
                "allowed_tools": ["get_events"],
                "read_permissions": ["canon"],
                "write_permissions": [],
                "forbidden_spaces": ["canon:original"],
                "budget": {
                    "max_calls": 10,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 1000,
                    "max_cost_usd": "1.00",
                },
                "approval_required_for": [],
                "input_schema": {
                    "type": "object",
                    "properties": {"novel_id": {"type": "integer"}},
                    "required": ["novel_id"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"type": {"const": "scene_candidate"}},
                },
            }
        )
        _, version = await register_skill_version(
            session, owner_id=user, novel_id=novel, contract=contract
        )
        await session.commit()
        return {
            "owner_id": user,
            "novel_id": novel,
            "chapter_id": chapter,
            "skill_version_id": version.id,
        }


async def _create_backfill_run(
    f, *, owner_id: int, novel_id: int, skill_version_id: int
) -> int:
    async with f() as session:
        r = (
            await session.execute(
                text(
                    "INSERT INTO skill_runs (owner_id, novel_id, skill_version_id, status, "
                    "input, input_hash, frozen_manifest, budget_snapshot, origin, "
                    "backfill_dimension, internal_token_hash, created_at, updated_at) "
                    "VALUES (:o, :n, :sv, 'queued', :inp, :ih, '{}', '{}', 'chat_backfill', "
                    "'raw_text', :th, now(), now()) RETURNING id"
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "sv": skill_version_id,
                    "inp": '{"novel_id":1,"question":"q","dimension":"raw_text","branch":null}',
                    "ih": HEX64,
                    "th": hashlib.sha256(b"token").hexdigest(),
                },
            )
        ).scalar()
        await session.commit()
        return r


async def _create_scene_artifact(
    f, *, seed: dict[str, Any], run_id: int, set_payload: dict[str, Any]
) -> None:
    async with f() as session:
        art = (
            await session.execute(
                text(
                    "INSERT INTO artifacts (owner_id, novel_id, skill_version_id, run_id, "
                    "schema_version, type, status, model_lineage, source_versions, input_hash, created_at, updated_at) "
                    "VALUES (:o, :n, :sv, :r, 'v1', 'scene_candidate', 'candidate', '{}', '{}', :ih, now(), now()) "
                    "RETURNING id"
                ),
                {
                    "o": seed["owner_id"],
                    "n": seed["novel_id"],
                    "sv": seed["skill_version_id"],
                    "r": run_id,
                    "ih": HEX64,
                },
            )
        ).scalar()
        await session.execute(
            text(
                "INSERT INTO artifact_revisions (artifact_id, owner_id, novel_id, "
                "revision_no, content, content_hash, evidence_refs, created_at) "
                "VALUES (:a, :o, :n, 1, :c, :ch, '[]', now())"
            ),
            {
                "a": art,
                "o": seed["owner_id"],
                "n": seed["novel_id"],
                "c": json.dumps(
                    {"type": "scene_candidate", "scene_candidate_set": set_payload},
                    ensure_ascii=False,
                ),
                "ch": HEX64,
            },
        )
        await session.commit()


async def _build_scene_set_payload(
    seed: dict[str, Any], *, snapshot_hash: str, skip_assert: bool = False
) -> dict[str, Any]:
    from app.schemas.key_scene import (
        SceneCandidateContract,
        SceneCandidateSetContract,
        SceneCoordinates,
        SalienceReason,
    )
    from app.services.key_scenes.boundaries import (
        ChapterRecord,
        compute_source_snapshot_hash,
    )
    from app.services.key_scenes.candidates import (
        KEY_SCENE_DETECTOR_ID,
        KEY_SCENE_DETECTOR_VERSION,
        KEY_SCENE_SCHEMA_HASH,
        recompute_manifest_hash,
    )
    from app.services.key_scenes.scoring import DEFAULT_SCENE_POLICY, policy_hash

    record = ChapterRecord(
        chapter_id=seed["chapter_id"], chapter_number=1, content=CH_CONTENT
    )
    hash_from_db = compute_source_snapshot_hash(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        chapters=(record,),
    )
    if not skip_assert:
        assert hash_from_db == snapshot_hash
    start, end = 0, min(len(CH_CONTENT), 40)
    evidence_key = f"qp:{seed['chapter_id']}:0:40:{HEX64}"
    candidate = SceneCandidateContract(
        candidate_key="ks-p40-0",
        candidate_order=0,
        scene_id="scene-p40-0-40",
        chapter_id=seed["chapter_id"],
        chapter_number=1,
        source_start=start,
        source_end=end,
        source_hash=HEX64,
        coordinates=SceneCoordinates(
            cast=["arin"], place="courtyard", time="night", pov="arin"
        ),
        spoiler_cutoff=1,
        salience_reasons=[
            SalienceReason(reason_code="plot_turn", detail="attack", score=0.9)
        ],
        score_total=0.9,
        score_breakdown={"action": 0.8},
        diversity_key="dk-p40",
        detector_id=KEY_SCENE_DETECTOR_ID,
        detector_version=KEY_SCENE_DETECTOR_VERSION,
        policy_hash=policy_hash(DEFAULT_SCENE_POLICY),
        evidence_ranges=[
            {
                "evidence_key": evidence_key,
                "source_snapshot_id": "ss-p40",
                "source_snapshot_hash": snapshot_hash,
                "chapter_id": seed["chapter_id"],
                "chapter_number": 1,
                "source_start": start,
                "source_end": end,
                "content_hash": HEX64,
                "excerpt": CH_CONTENT[start:end],
                "cutoff_chapter": 1,
            }
        ],
        heuristic_signal=None,
        review_state="candidate",
    )
    set_contract = SceneCandidateSetContract(
        schema_version="key-scene.v1",
        artifact_kind="key_scene",
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_key=f"ks-p40-{seed['novel_id']}",
        revision_number=1,
        parent_set_id=None,
        source_snapshot_id="ss-p40",
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=1,
        schema_hash=KEY_SCENE_SCHEMA_HASH,
        policy_hash=policy_hash(DEFAULT_SCENE_POLICY),
        detector_id=KEY_SCENE_DETECTOR_ID,
        detector_version=KEY_SCENE_DETECTOR_VERSION,
        manifest_hash="0" * 64,
        approved_visual_bible_revision_id=None,
        approved_visual_bible_revision_hash=None,
        candidates=[candidate],
        review_state="candidate",
    )
    set_contract = set_contract.model_copy(
        update={"manifest_hash": recompute_manifest_hash(set_contract)}
    )
    return set_contract.model_dump(mode="json")


async def _chapter_snapshot_hash(f, *, seed: dict[str, Any]) -> str:
    from app.services.key_scenes.boundaries import (
        ChapterRecord,
        compute_source_snapshot_hash,
    )

    record = ChapterRecord(
        chapter_id=seed["chapter_id"], chapter_number=1, content=CH_CONTENT
    )
    return compute_source_snapshot_hash(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        chapters=(record,),
    )


class TestKeyScenesMaterialization:
    async def test_materialize_scene_candidate_writes_rows(self, factory):
        seed = await _seed_novel_with_chapter(factory, suffix="ks1")
        snapshot_hash = await _chapter_snapshot_hash(factory, seed=seed)
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        await _create_scene_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            set_payload=await _build_scene_set_payload(seed, snapshot_hash=snapshot_hash),
        )
        async with factory() as session:
            await session.execute(
                text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
                {"id": run_id},
            )
            await session.commit()

        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "materialized:scene_candidate"

        async with factory() as session:
            sets = (
                await session.execute(
                    text(
                        "SELECT version_key, review_state FROM key_scene_sets WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).all()
            assert len(sets) == 1
            assert sets[0].review_state == "candidate"
            cands = (
                await session.execute(
                    text(
                        "SELECT candidate_key FROM key_scene_candidates WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).all()
            assert len(cands) == 1
            evs = (
                await session.execute(
                    text(
                        "SELECT evidence_key FROM key_scene_evidence_ranges WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).all()
            assert len(evs) == 1

    async def test_materialize_idempotent(self, factory):
        seed = await _seed_novel_with_chapter(factory, suffix="ks2")
        snapshot_hash = await _chapter_snapshot_hash(factory, seed=seed)
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        await _create_scene_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            set_payload=await _build_scene_set_payload(seed, snapshot_hash=snapshot_hash),
        )
        async with factory() as session:
            await session.execute(
                text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
                {"id": run_id},
            )
            await session.commit()

        first = await materialize_skill_run(factory, run_id)
        assert first == "materialized:scene_candidate"
        second = await materialize_skill_run(factory, run_id)
        assert second == "materialized:scene_candidate"
        async with factory() as session:
            n = (
                await session.execute(
                    text("SELECT COUNT(*) FROM key_scene_sets WHERE owner_id=:o"),
                    {"o": seed["owner_id"]},
                )
            ).scalar()
            assert n == 1

    async def test_materialize_stale_snapshot_skipped(self, factory):
        seed = await _seed_novel_with_chapter(factory, suffix="ks3")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        await _create_scene_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            set_payload=await _build_scene_set_payload(
                seed, snapshot_hash="0" * 64, skip_assert=True
            ),
        )
        async with factory() as session:
            await session.execute(
                text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
                {"id": run_id},
            )
            await session.commit()

        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "skipped:stale_source_snapshot"
        async with factory() as session:
            n = (
                await session.execute(
                    text("SELECT COUNT(*) FROM key_scene_sets WHERE owner_id=:o"),
                    {"o": seed["owner_id"]},
                )
            ).scalar()
            assert n == 0


# ══════════════════════════════════════════════════════════════════════
# 阶段 D：knowledge 检索接线
# ══════════════════════════════════════════════════════════════════════


class TestKnowledgeRetrievalWiring:
    async def test_fetch_knowledge_evidence_reads_materialized_rows(self, factory):
        seed = await _seed_novel_with_chapter(factory, suffix="kr1")
        snapshot_hash = await _chapter_snapshot_hash(factory, seed=seed)
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        await _create_scene_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            set_payload=await _build_scene_set_payload(seed, snapshot_hash=snapshot_hash),
        )
        async with factory() as session:
            await session.execute(
                text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
                {"id": run_id},
            )
            await session.commit()
        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "materialized:scene_candidate"

        from app.models import Chapter
        from app.services.reader_chat.retrieval import fetch_knowledge_evidence

        async with factory() as session:
            chapters = (
                (
                    await session.scalars(
                        select(Chapter).where(Chapter.novel_id == seed["novel_id"])
                    )
                )
                .all()
            )
            chapters_by_number = {c.chapter_number: c for c in chapters}
            items, omitted, status = await fetch_knowledge_evidence(
                session,
                owner_id=seed["owner_id"],
                novel_id=seed["novel_id"],
                version_id=1,
                cutoff_chapter=1,
                full_book=False,
                chapters_by_number=chapters_by_number,
            )
            assert status == "ok"
            assert len(items) >= 1
            first = items[0]
            assert first.source_type == "knowledge"
            assert first.version_lineage.get("candidate") is True

    async def test_fetch_knowledge_evidence_absent_without_version(self, factory):
        seed = await _seed_novel_with_chapter(factory, suffix="kr2")
        from app.models import Chapter
        from app.services.reader_chat.retrieval import fetch_knowledge_evidence

        async with factory() as session:
            chapters = (
                (
                    await session.scalars(
                        select(Chapter).where(Chapter.novel_id == seed["novel_id"])
                    )
                )
                .all()
            )
            chapters_by_number = {c.chapter_number: c for c in chapters}
            items, omitted, status = await fetch_knowledge_evidence(
                session,
                owner_id=seed["owner_id"],
                novel_id=seed["novel_id"],
                version_id=None,
                cutoff_chapter=1,
                full_book=False,
                chapters_by_number=chapters_by_number,
            )
            assert status == "absent"
            assert items == []
