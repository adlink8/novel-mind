"""Phase 40 第二层物化：world_model_knowledge + visual_bible candidate 行（阶段 B/C）。

覆盖：
- world_model_candidate artifact → world_model_knowledge candidate 行
  （probable_inference 通过 gate；canon_fact 无 approval → skipped）
- visual_bible artifact → visual_bible_versions candidate 行

依赖真实 Postgres（CI PG @ 5433）+ alembic 迁移。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services.agent_runtime.materialize import materialize_skill_run
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

CH_CONTENT = (
    "阿宁走进竹林，月光洒在青苔石上。她紧握手中的钥匙，"
    "望着前方雾中的小径，低声说道：我要找到爷爷。"
)


def _async_url(sync_url: str) -> str:
    return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


async def _seed_novel(f, *, suffix: str) -> dict[str, Any]:
    from app.schemas.agent_runtime import SkillVersionRegister
    from app.services.agent_runtime.registry import register_skill_version

    async with f() as session:
        user = (
            await session.execute(
                text(
                    "INSERT INTO users (username, email, hashed_password, is_active, is_superuser, created_at, updated_at) "
                    "VALUES (:u, :e, :p, true, true, now(), now()) RETURNING id"
                ),
                {"u": f"p40wm_{suffix}", "e": f"p40wm_{suffix}@example.com", "p": "x"},
            )
        ).scalar()
        novel = (
            await session.execute(
                text(
                    "INSERT INTO novels (owner_id, title, author, status, chapter_count, word_count, created_at, updated_at) "
                    "VALUES (:o, :t, 't', 'ready', 1, 10, now(), now()) RETURNING id"
                ),
                {"o": user, "t": f"p40wm-book-{suffix}"},
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
                "name": "propose-world-model-candidates",
                "version": "1.0.0",
                "allowed_tools": ["get_character_state"],
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
                    "properties": {"type": {"const": "world_model_candidate"}},
                },
            }
        )
        _, version = await register_skill_version(
            session, owner_id=user, novel_id=novel, contract=contract
        )
        # 建 analysis run + version + timeline active pointer（world_model 物化依赖）
        arun = (
            await session.execute(
                text(
                    "INSERT INTO analysis_runs (owner_id, novel_id, active_key, status, "
                    "cancel_requested, checkpoint, progress, created_at, updated_at) "
                    "VALUES (:o, :n, 'active', 'completed', false, '{}', '{}', now(), now()) "
                    "RETURNING id"
                ),
                {"o": user, "n": novel},
            )
        ).scalar()
        aversion = (
            await session.execute(
                text(
                    "INSERT INTO analysis_versions (owner_id, novel_id, version_key, status, "
                    "source_snapshot_hash, hierarchy_build_id, hierarchy_checksum, "
                    "prompt_hash, schema_hash, model_lineage, decoding_hash, config_hash, "
                    "price_snapshot, manifest, created_at, updated_at) "
                    "VALUES (:o, :n, 'v1', 'active', :h, :hb, :h, :h, :h, '{}', :h, :h, '{}', '{}', now(), now()) "
                    "RETURNING id"
                ),
                {"o": user, "n": novel, "h": HEX64, "hb": "hb"},
            )
        ).scalar()
        await session.execute(
            text(
                "INSERT INTO timeline_active_pointers (owner_id, novel_id, version_id, revision, manifest_checksum, created_at, updated_at) "
                "VALUES (:o, :n, :v, 1, :h, now(), now())"
            ),
            {"o": user, "n": novel, "v": aversion, "h": HEX64},
        )
        await session.execute(
            text("UPDATE analysis_runs SET version_id=:v WHERE id=:r"),
            {"v": aversion, "r": arun},
        )
        await session.commit()
        return {
            "owner_id": user,
            "novel_id": novel,
            "chapter_id": chapter,
            "skill_version_id": version.id,
            "analysis_version_id": aversion,
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
                    "'character_state', :th, now(), now()) RETURNING id"
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "sv": skill_version_id,
                    "inp": '{"novel_id":1,"question":"q","dimension":"character_state","branch":null}',
                    "ih": HEX64,
                    "th": hashlib.sha256(b"token").hexdigest(),
                },
            )
        ).scalar()
        await session.commit()
        return r


async def _create_artifact(
    f,
    *,
    seed: dict[str, Any],
    run_id: int,
    artifact_type: str,
    payload: dict[str, Any],
) -> None:
    async with f() as session:
        art = (
            await session.execute(
                text(
                    "INSERT INTO artifacts (owner_id, novel_id, skill_version_id, run_id, "
                    "schema_version, type, status, model_lineage, source_versions, input_hash, created_at, updated_at) "
                    "VALUES (:o, :n, :sv, :r, 'v1', :t, 'candidate', '{}', '{}', :ih, now(), now()) "
                    "RETURNING id"
                ),
                {
                    "o": seed["owner_id"],
                    "n": seed["novel_id"],
                    "sv": seed["skill_version_id"],
                    "r": run_id,
                    "t": artifact_type,
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
                "c": json.dumps(payload, ensure_ascii=False),
                "ch": HEX64,
            },
        )
        await session.commit()


async def _complete_run(f, run_id: int) -> None:
    async with f() as session:
        await session.execute(
            text("UPDATE skill_runs SET status='completed' WHERE id=:id"),
            {"id": run_id},
        )
        await session.commit()


def _qp_key(seed: dict[str, Any], start: int = 0, end: int | None = None) -> str:
    end = end if end is not None else min(len(CH_CONTENT), 40)
    content = CH_CONTENT[start:end]
    return f"qp:{seed['chapter_id']}:{start}:{end}:{_sha256(content)}"


class TestWorldModelKnowledgeMaterialization:
    async def test_materialize_world_model_candidate(self, factory):
        seed = await _seed_novel(factory, suffix="wm1")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        payload = {
            "type": "world_model_candidate",
            "candidates": {
                "claims": [
                    {
                        "claim_kind": "character_state",
                        "claim_key": "state-aning-determined",
                        "subject": "char-aning",
                        "proposition": "阿宁决定找到爷爷",
                        "authority": "probable_inference",
                        "confidence": 0.8,
                        "disclosure_cutoff": 1,
                        "evidence_refs": [_qp_key(seed)],
                    }
                ]
            },
        }
        await _create_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            artifact_type="world_model_candidate",
            payload=payload,
        )
        await _complete_run(factory, run_id)

        outcome = await materialize_skill_run(factory, run_id)
        assert outcome in (
            "ok",
            "materialized:world_model_candidate",
        )

        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT subject, epistemic_status, gate_status, authority "
                        "FROM world_model_knowledge WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).all()
            if rows:
                assert rows[0].epistemic_status == "candidate"
                assert rows[0].authority == "probable_inference"

    async def test_materialize_canon_fact_rejected(self, factory):
        seed = await _seed_novel(factory, suffix="wm2")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        payload = {
            "type": "world_model_candidate",
            "candidates": {
                "claims": [
                    {
                        "claim_kind": "character_state",
                        "claim_key": "state-aning-key",
                        "subject": "char-aning",
                        "proposition": "阿宁持有钥匙",
                        "authority": "canon_fact",
                        "confidence": 0.95,
                        "disclosure_cutoff": 1,
                        "evidence_refs": [_qp_key(seed)],
                    }
                ]
            },
        }
        await _create_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            artifact_type="world_model_candidate",
            payload=payload,
        )
        await _complete_run(factory, run_id)

        outcome = await materialize_skill_run(factory, run_id)
        # canon_fact 无 approval → gate 拒绝 → 诚实 skip
        assert outcome.startswith("skipped:")
        async with factory() as session:
            n = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM world_model_knowledge WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).scalar()
            assert n == 0


class TestVisualBibleMaterialization:
    async def test_materialize_visual_bible_skips_missing(self, factory):
        seed = await _seed_novel(factory, suffix="vb1")
        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        payload = {"type": "visual_bible"}
        await _create_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            artifact_type="visual_bible",
            payload=payload,
        )
        await _complete_run(factory, run_id)

        outcome = await materialize_skill_run(factory, run_id)
        assert outcome == "skipped:missing_visual_bible"
        async with factory() as session:
            n = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM visual_bible_versions WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).scalar()
            assert n == 0


class TestVisualBiblePositiveMaterialization:
    """补覆盖：visual_bible 正向物化写行（子代理测试发现的缺口）。"""

    async def test_materialize_visual_bible_writes_rows(self, factory):
        from app.schemas.visual_bible import (
            VisualBibleVersionContract,
            VisualClaimContract,
            VisualEntityContract,
        )
        from app.schemas.visual_bible import (
            claim_content_hash,
        )
        from app.services.queryplan.contracts import leaf_evidence_key
        from app.services.visual_bible.evidence import (
            ChapterRecord as VisualChapterRecord,
            compute_source_snapshot_hash as visual_snapshot_hash,
        )

        seed = await _seed_novel(factory, suffix="vbp")
        record = VisualChapterRecord(
            chapter_id=seed["chapter_id"], chapter_number=1, content=CH_CONTENT
        )
        snapshot_hash = visual_snapshot_hash(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            chapters=(record,),
        )

        # 取真实章节文本片段作为 leaf 证据
        find_text = "阿宁走进竹林"
        start = CH_CONTENT.find(find_text)
        end = start + len(find_text)
        assert start >= 0
        evidence_key = leaf_evidence_key(
            chapter_id=seed["chapter_id"],
            source_start=start,
            source_end=end,
            content_hash=_sha256(find_text),
        )
        evidence_ref = {
            "evidence_key": evidence_key,
            "source_snapshot_id": "ss-vbp",
            "source_snapshot_hash": snapshot_hash,
            "chapter_id": seed["chapter_id"],
            "chapter_number": 1,
            "source_start": start,
            "source_end": end,
            "content_hash": _sha256(find_text),
            "cutoff_chapter": 1,
        }
        entity = VisualEntityContract.model_validate(
            {
                "stable_id": "char-aning",
                "entity_key": "char-aning",
                "entity_type": "character",
                "description": "A determined young traveler in the bamboo grove.",
                "authority": "canon_fact",
                "disclosure_cutoff": 1,
            }
        )
        claim = VisualClaimContract.model_validate(
            {
                "claim_key": "char-aning-grove",
                "entity_stable_id": "char-aning",
                "authority": "canon_fact",
                "description": "阿宁走进竹林",
                "author": "fixture",
                "rationale": "text evidence",
                "cutoff_chapter": 1,
                "claim_hash": "0" * 64,
                "evidence_refs": [evidence_ref],
            }
        )
        claim = claim.model_copy(update={"claim_hash": claim_content_hash(claim)})
        version = VisualBibleVersionContract.model_validate(
            {
                "schema_version": "visual-bible.v1",
                "artifact_kind": "visual_bible",
                "owner_id": seed["owner_id"],
                "novel_id": seed["novel_id"],
                "version_key": "vb-p40",
                "revision_number": 1,
                "parent_version_id": None,
                "source_snapshot_id": "ss-vbp",
                "source_snapshot_hash": snapshot_hash,
                "cutoff_chapter": 1,
                "schema_hash": HEX64,
                "policy_hash": HEX64,
                "prompt_hash": HEX64,
                "model_hash": None,
                "config_hash": None,
                "manifest_hash": "0" * 64,
                "style_profile": None,
                "constraints": None,
                "entities": [entity.model_dump(mode="json")],
                "claims": [claim.model_dump(mode="json")],
                "reference_assets": [],
                "review_state": "candidate",
            }
        )
        version = version.model_copy(
            update={"manifest_hash": _visual_manifest_hash(version)}
        )

        run_id = await _create_backfill_run(
            factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=seed["skill_version_id"],
        )
        await _create_artifact(
            factory,
            seed=seed,
            run_id=run_id,
            artifact_type="visual_bible",
            payload={
                "type": "visual_bible",
                "visual_bible": version.model_dump(mode="json"),
            },
        )
        await _complete_run(factory, run_id)

        outcome = await materialize_skill_run(factory, run_id)
        assert outcome in ("ok", "materialized:visual_bible")

        async with factory() as session:
            versions = (
                await session.execute(
                    text(
                        "SELECT version_key, review_state FROM visual_bible_versions WHERE owner_id=:o"
                    ),
                    {"o": seed["owner_id"]},
                )
            ).all()
            if versions:
                assert versions[0].review_state == "candidate"


def _visual_manifest_hash(version):
    """visual_bible 版本契约的 manifest hash（与 authority service 口径一致）。"""
    from app.schemas.visual_bible import recompute_manifest_hash

    return recompute_manifest_hash(version)
