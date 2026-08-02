"""Structured Output Integrity 服务端权威契约（26-06 / REQ-AGENT-08 / D-16）。

证明：
  - 唯一 finalizer 在任何写入前运行 fail-closed integrity gate；blocked 路径
    产生 0 Artifact、0 Revision、0 ApprovalRequest（无 promotion / active-pointer）。
  - 合法 alias/enum/container 修复后的 payload 携带可重放的
    raw_hash/repaired_hash/normalization_actions/warnings 进入 Artifact lineage。
  - 受保护字段（owner/cutoff/authority/branch/fork/approval）缺失或非法 →
    blocked，不补默认值；伪造 evidence / heuristic candidate → blocked。
  - canonical_content_hash 与 agent-service normalizer 的 canonicalHash 口径一致
    （跨语言重放可验证）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.security import create_access_token, hash_password
from app.models import Chapter, Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRun,
)
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    finalize_skill_run,
)
from app.services.agent_runtime.registry import register_skill_version
from app.services.agent_runtime.structured_output_integrity import (
    BLOCKED_EXTERNAL_CANON,
    BLOCKED_LINEAGE_OWNER,
    BLOCKED_NO_EVIDENCE,
    BLOCKED_STALE_REPAIRED_HASH,
    BLOCKED_TRAIL_INCONSISTENT,
    BLOCKED_UNKNOWN_TYPE,
    canonical_content_hash,
    evaluate_integrity,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
FIXED_QUESTION = "阿宁在竹林里看见了谁？"
EVIDENCE_KEY = "evidence:1"

DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
]


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(*, novel_id: int) -> SkillVersionRegister:
    return SkillVersionRegister.model_validate(
        {
            "novel_id": novel_id,
            "name": "answer-reading-question",
            "version": "1.0.0",
            "allowed_tools": list(DEFAULT_TOOLS),
            "read_permissions": ["canon", "derivative"],
            "write_permissions": [],
            "forbidden_spaces": ["canon:original", "derivative:write"],
            "budget": {
                "max_calls": 10,
                "max_input_tokens": 20_000,
                "max_output_tokens": 4_000,
                "max_cost_usd": "0.50",
            },
            "approval_required_for": [],
            "input_schema": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "novel_id": {"type": "integer"}},
                "required": ["question", "novel_id"],
            },
            "output_schema": {"type": "object", "properties": {"schema_version": {"type": "string"}}},
        }
    )


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"soi_owner_{suffix}",
            email=f"soi_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"SOI Novel {suffix}",
            author="Author",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
            chapter_count=1,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(novel)
        session.flush()
        chapter = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        session.add(chapter)
        session.commit()
        data = {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
        }
    engine.dispose()
    return data


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


# ────────────────────────── helpers ──────────────────────────


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    include_evidence: bool = True,
    extra: dict[str, Any] | None = None,
    tamper_answer: bool = False,
    stale_hash: bool = False,
    trail_inconsistent: bool = False,
    wrong_owner: bool = False,
) -> dict[str, Any]:
    """构建携带完整性 trail 的 cited_answer 信封（默认 noop 修复）。"""
    envelope: dict[str, Any] = {
        "type": "cited_answer",
        "schema_version": "cited-answer.v1",
        "owner_id": owner_id if not wrong_owner else owner_id + 999,
        "novel_id": novel_id,
        "branch": None,
        "producing_skill": "answer-reading-question",
        "producing_skill_version": "1.0.0",
        "skill_version_id": skill_version_id,
        "model_lineage": {"provider": "fixture", "model": "stub", "revision": "s1"},
        "source_versions": {"novel": "v1"},
        "input_hash": input_hash,
        "evidence_refs": [EVIDENCE_KEY] if include_evidence else [],
        "answer": {
            "answer_blocks": [
                {"block_id": "b1", "text": "阿宁在竹林里看见了使者的身影。", "evidence_refs": [EVIDENCE_KEY]}
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        },
        "status": "candidate",
        "parent_revision": None,
    }
    if extra:
        envelope.update(extra)
    if tamper_answer:
        envelope["answer"]["answer_blocks"][0]["text"] = "被篡改的答案。"
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    if stale_hash:
        repaired_hash = "0" * 64  # 记录值 ≠ 实际内容 → 重放失败
    raw_hash = repaired_hash
    if trail_inconsistent:
        raw_hash = "1" * 64  # 无 actions 但 raw != repaired → trail 自相矛盾
    envelope["normalization"] = {
        "raw_hash": raw_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


async def _register_skill(factory, *, owner_id: int, novel_id: int) -> int:
    async with factory() as session:
        _, version = await register_skill_version(
            session, owner_id=owner_id, novel_id=novel_id, contract=_skill_contract(novel_id=novel_id)
        )
        await session.commit()
        return version.id


async def _create_run(
    factory, *, owner_id: int, novel_id: int, skill_version_id: int, input_hash: str
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            input={"question": FIXED_QUESTION},
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 10},
        )
        session.add(run)
        await session.commit()
        return run.id


async def _finalize(
    factory,
    *,
    run_id: int,
    envelope: dict[str, Any],
    frozen_manifest: dict[str, Any] | None = None,
):
    return await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=envelope,
        model_lineage={},
        source_versions={},
        usage={"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"},
        frozen_manifest=frozen_manifest,
    )


async def _count_artifacts(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(Artifact).where(Artifact.run_id == run_id)
            )
            or 0
        )


async def _count_revisions(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ArtifactRevision)
                .join(Artifact, ArtifactRevision.artifact_id == Artifact.id)
                .where(Artifact.run_id == run_id)
            )
            or 0
        )


async def _count_approvals(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ApprovalRequest)
                .where(ApprovalRequest.run_id == run_id)
            )
            or 0
        )


async def _set_up(factory, migrated_postgres: str, *, suffix: str) -> dict[str, Any]:
    """seed owner/novel + 注册技能 + 创建 running run，返回上下文。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=suffix)
    svid = await _register_skill(
        factory, owner_id=seed["owner_id"], novel_id=seed["novel_id"]
    )
    input_hash = "c" * 64
    run_id = await _create_run(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
    )
    return {**seed, "skill_version_id": svid, "input_hash": input_hash, "run_id": run_id}


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count_artifacts(factory, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


# ────────────────────────── 跨语言 hash 口径 ──────────────────────────


def test_canonical_content_hash_matches_node_normalizer():
    """与 agent-service normalizer 的 canonicalHash 基准对齐（sort_keys + utf-8 原样）。"""
    assert canonical_content_hash({"b": 2, "a": 1}) == (
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    )
    assert canonical_content_hash({"text": "阿宁"}) == (
        "076f77aa491996c8c4c7310a9694ee52db928bb37a48a872690a5090d10ca6bc"
    )
    assert canonical_content_hash({"a": [{"x": 1, "y": "阿宁"}], "z": None}) == (
        "2b330aba8810faf79a883962afde3352ba5c4db94bae23d7a30eb9644b746c55"
    )


# ────────────────────────── happy path ──────────────────────────


async def test_happy_path_records_normalization_trail(
    runtime_factory, migrated_postgres: str
):
    """合法 noop 修复信封 → completed；content 携带可重放 trail。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY], "manifest_checksum": "m" * 64},
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_id is not None
    assert await _count_artifacts(runtime_factory, run_id=ctx["run_id"]) == 1
    assert await _count_revisions(runtime_factory, run_id=ctx["run_id"]) == 1

    async with runtime_factory() as session:
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        artifact = await session.get(Artifact, outcome.artifact_id)
    assert revision is not None
    trail = revision.content["normalization"]
    assert trail["raw_hash"] == trail["repaired_hash"]  # noop 修复
    assert trail["normalization_actions"] == []
    assert trail["warnings"] == []
    # 服务端重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert canonical_content_hash(_strip_trail(revision.content)) == trail["repaired_hash"]
    assert artifact is not None and artifact.status == "candidate"
    assert artifact.status != "published"  # 无自动 promotion
    # finalize 后没有 ApprovalRequest 副作用。
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


# ────────────────────────── blocked paths（fail closed，零写入） ──────────────────────────


async def test_schema_violation_protected_field_blocks(runtime_factory, migrated_postgres: str):
    """信封含受保护字段 authority → blocked，0 写（extra=forbid + 显式检查）。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"auth_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        extra={"authority": "model-claimed-authority"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "authority" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_missing_evidence_heuristic_candidate_blocks(runtime_factory, migrated_postgres: str):
    """cited_answer 无 evidence_refs（heuristic candidate 形状）→ blocked，0 写。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        include_evidence=False,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "heuristic candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_lineage_owner_mismatch_blocks(runtime_factory, migrated_postgres: str):
    """owner 血缘与 run 不符 → blocked，0 写（不补默认值）。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        wrong_owner=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_stale_repaired_hash_blocks(runtime_factory, migrated_postgres: str):
    """repaired_hash 与内容不符（payload 在规范化后被篡改）→ blocked，0 写。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"stale_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        tamper_answer=True,
        stale_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "repaired_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_trail_inconsistent_blocks(runtime_factory, migrated_postgres: str):
    """无 normalization_actions 但 raw_hash != repaired_hash → blocked，0 写。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"trail_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        trail_inconsistent=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "trail inconsistent" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_unknown_artifact_type_blocks(runtime_factory, migrated_postgres: str):
    """未注册 artifact type → blocked，0 写（fail closed）。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"type_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
    )
    envelope["type"] = "hallucinated-type"
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "unknown artifact type" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_unknown_evidence_ref_blocks(runtime_factory, migrated_postgres: str):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，0 写（leaf-evidence 权威）。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"evid_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        extra={"evidence_refs": ["evidence:forged"]},
    )
    envelope["answer"]["answer_blocks"][0]["evidence_refs"] = ["evidence:forged"]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "unknown evidence ref" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_blocked_path_no_approval_request_or_promotion(
    runtime_factory, migrated_postgres: str
):
    """blocked 路径：无 ApprovalRequest、无 Artifact/Revision、无 promotion 副作用。"""
    ctx = await _set_up(runtime_factory, migrated_postgres, suffix=f"prom_{uuid.uuid4().hex[:6]}")
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        extra={"authority": "x"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


# ────────────────────────── evaluate_integrity 直接单测（external_evidence） ──────────────────────────


def _fake_run(**overrides: Any) -> SimpleNamespace:
    base = {
        "owner_id": 1,
        "novel_id": 1,
        "skill_version_id": 1,
        "input_hash": "c" * 64,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_evaluate_integrity_external_evidence_valid():
    """external_evidence 合法信封（prohibited_from_canon=True 常量）→ ok。"""
    envelope = {
        "type": "external_evidence",
        "schema_version": 1,
        "sources": [
            {"server": "s", "tool": "t", "uri": "https://example.com/x", "title": "T", "retrieved_from": "mcp"}
        ],
        "retrieval_time": "2026-08-03T00:00:00Z",
        "claims": [{"text": "外部主张", "source_index": 0}],
        "confidence": "low",
        "prohibited_from_canon": True,
        "release_status": "external",
    }
    decision = evaluate_integrity(envelope=envelope, run=_fake_run())
    assert decision.ok is True
    assert decision.blocked_reason is None


def test_evaluate_integrity_external_evidence_canon_flag_rejected():
    """prohibited_from_canon 被翻转为 false → blocked（服务端常量不可翻转）。"""
    envelope = {
        "type": "external_evidence",
        "schema_version": 1,
        "sources": [
            {"server": "s", "tool": "t", "uri": "https://example.com/x", "title": "T", "retrieved_from": "mcp"}
        ],
        "retrieval_time": "2026-08-03T00:00:00Z",
        "claims": [{"text": "x", "source_index": 0}],
        "confidence": "low",
        "prohibited_from_canon": False,
        "release_status": "external",
    }
    decision = evaluate_integrity(envelope=envelope, run=_fake_run())
    assert decision.ok is False
    assert decision.blocked_reason == BLOCKED_EXTERNAL_CANON


def test_evaluate_integrity_rejects_protected_synthesis():
    """受保护字段 authority 出现在信封 → blocked（服务端不补默认值）。"""
    envelope = _build_envelope(
        owner_id=1,
        novel_id=1,
        skill_version_id=1,
        input_hash="c" * 64,
        extra={"authority": "x"},
    )
    decision = evaluate_integrity(envelope=envelope, run=_fake_run())
    assert decision.ok is False
    assert decision.blocked_reason is not None
    assert "authority" in decision.blocked_reason


def test_evaluate_integrity_owner_mismatch():
    decision = evaluate_integrity(
        envelope=_build_envelope(
            owner_id=1, novel_id=1, skill_version_id=1, input_hash="c" * 64, wrong_owner=True
        ),
        run=_fake_run(),
    )
    assert decision.ok is False
    assert decision.blocked_reason == BLOCKED_LINEAGE_OWNER


def test_evaluate_integrity_no_evidence_heuristic():
    decision = evaluate_integrity(
        envelope=_build_envelope(
            owner_id=1, novel_id=1, skill_version_id=1, input_hash="c" * 64, include_evidence=False
        ),
        run=_fake_run(),
    )
    assert decision.ok is False
    assert decision.blocked_reason == BLOCKED_NO_EVIDENCE


def test_evaluate_integrity_stale_hash_and_unknown_type():
    stale = evaluate_integrity(
        envelope=_build_envelope(
            owner_id=1,
            novel_id=1,
            skill_version_id=1,
            input_hash="c" * 64,
            tamper_answer=True,
            stale_hash=True,
        ),
        run=_fake_run(),
    )
    assert stale.ok is False
    assert stale.blocked_reason == BLOCKED_STALE_REPAIRED_HASH

    unknown = _build_envelope(owner_id=1, novel_id=1, skill_version_id=1, input_hash="c" * 64)
    unknown["type"] = "mystery"
    unknown["normalization"]["repaired_hash"] = canonical_content_hash(_strip_trail(unknown))
    unknown["normalization"]["raw_hash"] = unknown["normalization"]["repaired_hash"]
    decision = evaluate_integrity(envelope=unknown, run=_fake_run())
    assert decision.ok is False
    assert decision.blocked_reason == BLOCKED_UNKNOWN_TYPE


def test_evaluate_integrity_trail_inconsistent():
    decision = evaluate_integrity(
        envelope=_build_envelope(
            owner_id=1,
            novel_id=1,
            skill_version_id=1,
            input_hash="c" * 64,
            trail_inconsistent=True,
        ),
        run=_fake_run(),
    )
    assert decision.ok is False
    assert decision.blocked_reason == BLOCKED_TRAIL_INCONSISTENT
