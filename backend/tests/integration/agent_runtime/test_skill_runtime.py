"""Skill Runtime 集成测试：注册门禁、确定性 finalize、cancel-no-write、
状态迁移唯一路径、端到端 fixture run + replay（25.2-03 Task 2/4）。

约定:
  - 模块级迁移一次（reset → upgrade head），用例通过唯一用户名隔离。
  - stub agent loop 会真实调用 25.2-02 facade（get_novel 工具），再确定性
    物化 evidence_ref + 冻结 manifest，最后走 finalize 写候选产物。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Chapter, Novel, User
from app.models.agent_runtime import (
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.schemas.agent_runtime import (
    CitedAnswerArtifact,
    SkillVersionRegister,
)
from app.services.agent_runtime.artifacts import (
    ArtifactStateError,
    transition_artifact_status,
)
from app.services.agent_runtime.finalize import (
    ERROR_CODE_BUDGET_EXCEEDED,
    ERROR_CODE_FAILED_VALIDATION,
    finalize_skill_run,
)
from app.services.agent_runtime.registry import (
    SkillContractError,
    canonical_input_hash,
    register_skill_version,
    validate_skill_contract,
)
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.agent_tools.facade import ToolFacade
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()

DEFAULT_SKILL = "answer-reading-question"
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
]
FIXED_QUESTION = "阿宁在竹林里看见了谁？"

# D-10 字段清单（CitedAnswerArtifact 信封 + artifact 行血缘字段）。
D10_FIELDS = (
    "type",
    "schema_version",
    "owner_id",
    "novel_id",
    "producing_skill",
    "producing_skill_version",
    "skill_version_id",
    "model_lineage",
    "source_versions",
    "input_hash",
    "evidence_refs",
    "status",
)


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(
    *, novel_id: int, name: str = DEFAULT_SKILL, **overrides: Any
) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
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
            "properties": {
                "question": {"type": "string"},
                "novel_id": {"type": "integer"},
            },
            "required": ["question", "novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"schema_version": {"type": "string"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner/other 用户 + owner 小说 + 一章正文，返回 tokens。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"agent_owner_{suffix}",
            email=f"agent_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        other = User(
            username=f"agent_other_{suffix}",
            email=f"agent_other_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add_all([owner, other])
        session.flush()
        novel = Novel(
            title=f"Agent Novel {suffix}",
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
            "other_id": other.id,
            "novel_id": novel.id,
            "chapter_id": chapter.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
            "other_token": create_access_token({"sub": str(other.id)}),
        }
    engine.dispose()
    return data


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    """模块级迁移：reset 一次 + upgrade head。"""
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    """绑定 CI PG 的 async_sessionmaker（service 层测试用）。"""
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(migrated_postgres: str):
    """ASGI client，get_db 覆盖为模块迁移库（API 层测试用）。"""
    aengine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, migrated_postgres
    app.dependency_overrides.clear()
    await aengine.dispose()


# ────────────────────────── stub agent loop ──────────────────────────


async def stub_agent_loop(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    question: str,
    skill_version_id: int,
    input_hash: str,
) -> dict[str, Any]:
    """stub agent loop：真实调一次 facade 工具，然后确定性物化证据并产出 Cited Answer。

    返回 finalize 所需的 envelope / frozen_manifest / usage / lineage。
    """
    async with factory() as session:
        novel = await session.get(Novel, novel_id)
        facade = ToolFacade()
        payload = await facade.execute(
            "get_novel", db=session, novel=novel, owner_id=owner_id, params={}
        )
        novel_title = payload.get("title") if isinstance(payload, dict) else None

    evidence_key = "evidence:1"
    frozen_manifest = {
        "evidence_refs": [evidence_key],
        "manifest_checksum": "m" * 64,
        "novel_title": novel_title,
        "question": question,
    }
    model_lineage = {
        "provider": "fixture",
        "model": "stub-model",
        "revision": "stub-1",
    }
    source_versions = {
        "novel": "v1",
        "chapters": {"1": HEX64},
    }
    envelope_dict = {
        "type": "cited_answer",
        "schema_version": "cited-answer.v1",
        "owner_id": owner_id,
        "novel_id": novel_id,
        "branch": None,
        "producing_skill": DEFAULT_SKILL,
        "producing_skill_version": "1.0.0",
        "skill_version_id": skill_version_id,
        "model_lineage": model_lineage,
        "source_versions": source_versions,
        "input_hash": input_hash,
        "evidence_refs": [evidence_key],
        "answer": {
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "阿宁在竹林里看见了使者的身影。",
                    "evidence_refs": [evidence_key],
                }
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        },
        "status": "candidate",
        "parent_revision": None,
    }
    # 26-06：完整性 trail（noop 修复：raw == repaired，零 action）。
    repaired_hash = canonical_content_hash(envelope_dict)
    envelope_dict["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    envelope = CitedAnswerArtifact.model_validate(envelope_dict)
    usage = {"calls": 1, "input_tokens": 120, "output_tokens": 48, "cost_usd": "0.0002"}
    return {
        "envelope": envelope.model_dump(mode="json"),
        "frozen_manifest": frozen_manifest,
        "usage": usage,
        "model_lineage": model_lineage,
        "source_versions": source_versions,
    }


async def _register_skill(
    factory, *, owner_id: int, novel_id: int, contract: SkillVersionRegister
) -> int:
    """service 层注册技能并提交，返回 skill_version id。"""
    async with factory() as session:
        _, version = await register_skill_version(
            session, owner_id=owner_id, novel_id=novel_id, contract=contract
        )
        await session.commit()
        return version.id


async def _accept_run(
    api_client,
    *,
    token: str,
    novel_id: int,
    skill_version_id: int,
    question: str,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    resp = await api_client.post(
        f"/api/agent/novels/{novel_id}/skill-runs",
        json={"skill_version_id": skill_version_id, "input": {"question": question}},
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["internal_token"], "202 must mint a per-run internal token"
    return body


async def _run_to_artifact(
    factory,
    *,
    run_id: int,
    skill_version_id: int,
    input_hash: str,
    owner_id: int,
    novel_id: int,
    stop_reason: str = "stop",
    **finalize_overrides: Any,
):
    """驱动 stub loop → finalize，返回 (outcome, loop_result)。"""
    loop = await stub_agent_loop(
        factory,
        owner_id=owner_id,
        novel_id=novel_id,
        question=FIXED_QUESTION,
        skill_version_id=skill_version_id,
        input_hash=input_hash,
    )
    outcome = await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason=stop_reason,
        envelope=loop["envelope"],
        model_lineage=loop["model_lineage"],
        source_versions=loop["source_versions"],
        usage=loop["usage"],
        frozen_manifest=loop["frozen_manifest"],
        **finalize_overrides,
    )
    return outcome, loop


async def _count_artifacts(factory, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.run_id == run_id)
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


def _assert_d10_fields(content: dict[str, Any]) -> None:
    """断言 Cited Answer 信封全部 D-10 字段存在且非空。"""
    for field in D10_FIELDS:
        assert field in content, f"missing D-10 field {field!r}"
        value = content[field]
        if isinstance(value, (dict, list)):
            assert value, f"D-10 field {field!r} must be non-empty"
        else:
            assert value is not None, f"D-10 field {field!r} must be non-null"
    assert content["status"] == "candidate"


# ────────────────────────── Task 2 行为测试 ──────────────────────────


async def test_register_skill_rejects_unknown_tool(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未注册工具 → 注册拒绝，无 active 行（T-25.2-03-01）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        allowed_tools=list(DEFAULT_TOOLS) + ["read_evil_file"],
    )
    with pytest.raises(SkillContractError):
        await _register_skill(
            runtime_factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            contract=contract,
        )
    # 无任何 active 行（registry 或 version 都不应该产生）。
    async with runtime_factory() as session:
        version_count = await session.scalar(
            select(func.count())
            .select_from(SkillVersion)
            .join(SkillRegistry, SkillVersion.registry_id == SkillRegistry.id)
            .where(
                SkillRegistry.owner_id == seed["owner_id"],
                SkillRegistry.novel_id == seed["novel_id"],
                SkillRegistry.name == DEFAULT_SKILL,
            )
        )
        registry_count = await session.scalar(
            select(func.count())
            .select_from(SkillRegistry)
            .where(
                SkillRegistry.owner_id == seed["owner_id"],
                SkillRegistry.novel_id == seed["novel_id"],
            )
        )
    assert int(version_count or 0) == 0
    assert int(registry_count or 0) == 0


def test_validate_skill_contract_requires_d09_fields():
    """缺 D-09 字段的契约 payload → fail closed。"""
    with pytest.raises(SkillContractError):
        validate_skill_contract({"name": "x", "version": "1.0.0"})


async def test_finalize_happy_path_writes_candidate_artifact(
    runtime_factory, migrated_postgres: str
):
    """stop 分支：写入 candidate 产物 + 首个 revision，D-10 字段非空。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"happy_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=svid,
            status="queued",
            input={"question": FIXED_QUESTION, "novel_id": seed["novel_id"]},
            input_hash="a" * 64,
            budget_snapshot={"max_calls": 10},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    outcome, loop = await _run_to_artifact(
        runtime_factory,
        run_id=run_id,
        skill_version_id=svid,
        input_hash="a" * 64,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
    )
    assert outcome.status == "completed"
    assert outcome.artifact_id is not None
    assert outcome.artifact_revision_id is not None
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
    assert artifact is not None and artifact.status == "candidate"
    assert revision is not None and revision.revision_no == 1
    assert revision.parent_revision_id is None
    assert artifact.current_revision_id == revision.id
    assert (
        run_row is not None
        and run_row.status == "completed"
        and run_row.stop_reason == "stop"
    )
    _assert_d10_fields(revision.content)


async def test_finalize_cancel_no_write(runtime_factory, migrated_postgres: str):
    """取消分支：run=cancelled，0 artifact 行 + 0 revision 行。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=svid,
            status="running",
            input={"question": FIXED_QUESTION, "novel_id": seed["novel_id"]},
            input_hash="a" * 64,
            cancel_requested=True,
            budget_snapshot={"max_calls": 10},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    loop = await stub_agent_loop(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        question=FIXED_QUESTION,
        skill_version_id=svid,
        input_hash="a" * 64,
    )
    outcome = await finalize_skill_run(
        runtime_factory,
        run_id=run_id,
        stop_reason="aborted",
        envelope=loop["envelope"],
        model_lineage=loop["model_lineage"],
        source_versions=loop["source_versions"],
        usage=loop["usage"],
        frozen_manifest=loop["frozen_manifest"],
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 0
    async with runtime_factory() as session:
        run_row = await session.get(SkillRun, run_id)
    assert run_row is not None and run_row.status == "cancelled"


async def test_finalize_unknown_evidence_ref_fails(
    runtime_factory, migrated_postgres: str
):
    """引证不在冻结 manifest 白名单 → run failed(failed_validation)，什么都不写。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"evid_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=svid,
            status="running",
            input={"question": FIXED_QUESTION, "novel_id": seed["novel_id"]},
            input_hash="a" * 64,
            budget_snapshot={"max_calls": 10},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    loop = await stub_agent_loop(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        question=FIXED_QUESTION,
        skill_version_id=svid,
        input_hash="a" * 64,
    )
    # 篡改引证：引用白名单外的 ref。
    envelope = dict(loop["envelope"])
    envelope["answer"] = dict(envelope["answer"])
    envelope["answer"]["answer_blocks"] = [
        {
            "block_id": "b1",
            "text": "x",
            "evidence_refs": ["evidence:forged"],
        }
    ]
    outcome = await finalize_skill_run(
        runtime_factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=envelope,
        model_lineage=loop["model_lineage"],
        source_versions=loop["source_versions"],
        usage=loop["usage"],
        frozen_manifest=loop["frozen_manifest"],
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.artifact_id is None
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 0


async def test_finalize_over_budget_fails_closed(
    runtime_factory, migrated_postgres: str
):
    """预算超限 → run failed(budget_exceeded)，0 写（T-25.2-03-05）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"budget_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            budget={
                "max_calls": 1,
                "max_input_tokens": 10,
                "max_output_tokens": 10,
                "max_cost_usd": "0.001",
            },
        ),
    )
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=svid,
            status="running",
            input={"question": FIXED_QUESTION, "novel_id": seed["novel_id"]},
            input_hash="a" * 64,
            budget_snapshot={"max_calls": 1},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    loop = await stub_agent_loop(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        question=FIXED_QUESTION,
        skill_version_id=svid,
        input_hash="a" * 64,
    )
    outcome = await finalize_skill_run(
        runtime_factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=loop["envelope"],
        model_lineage=loop["model_lineage"],
        source_versions=loop["source_versions"],
        # usage 的 input_tokens 远超 max_input_tokens=10 → fail closed。
        usage={
            "calls": 1,
            "input_tokens": 500,
            "output_tokens": 48,
            "cost_usd": "0.0002",
        },
        frozen_manifest=loop["frozen_manifest"],
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_BUDGET_EXCEEDED
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 0


async def test_artifact_status_only_via_service_and_owner(
    api_client, runtime_factory, migrated_postgres: str
):
    """状态迁移唯一路径：service 非法迁移拒绝、非 owner 404、approve 逐级前进。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"st_{uuid.uuid4().hex[:6]}")
    # API 注册（合法工具集）。
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}
    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(novel_id=seed["novel_id"]).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    accepted = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        question=FIXED_QUESTION,
    )
    run_id = accepted["run"]["id"]
    outcome, _ = await _run_to_artifact(
        runtime_factory,
        run_id=run_id,
        skill_version_id=svid,
        input_hash=accepted["run"]["input_hash"],
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
    )
    artifact_id = outcome.artifact_id
    assert artifact_id is not None

    # 直接 service 伪造迁移：candidate→published 非法（T-25.2-03-03）。
    async with runtime_factory() as session:
        with pytest.raises(ArtifactStateError):
            await transition_artifact_status(
                session,
                artifact_id=artifact_id,
                owner_id=seed["owner_id"],
                to_status="published",
            )

    # 非 owner 走 API approve → 404。
    other_headers = {"Authorization": f"Bearer {seed['other_token']}"}
    forged = await client.post(
        f"/api/agent/artifacts/{artifact_id}/approve", headers=other_headers
    )
    assert forged.status_code == 404

    # owner 逐级 approve：candidate→validated→approved→published。
    step1 = await client.post(
        f"/api/agent/artifacts/{artifact_id}/approve", headers=headers
    )
    assert step1.status_code == 200 and step1.json()["status"] == "validated"
    step2 = await client.post(
        f"/api/agent/artifacts/{artifact_id}/approve", headers=headers
    )
    assert step2.status_code == 200 and step2.json()["status"] == "approved"
    step3 = await client.post(
        f"/api/agent/artifacts/{artifact_id}/approve", headers=headers
    )
    assert step3.status_code == 200 and step3.json()["status"] == "published"
    # 终态后再 approve → 409。
    over = await client.post(
        f"/api/agent/artifacts/{artifact_id}/approve", headers=headers
    )
    assert over.status_code == 409

    # reject 分支：新产物 candidate→rejected。
    accepted2 = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        question=FIXED_QUESTION + "?",
    )
    run2_id = accepted2["run"]["id"]
    outcome2, _ = await _run_to_artifact(
        runtime_factory,
        run_id=run2_id,
        skill_version_id=svid,
        input_hash=accepted2["run"]["input_hash"],
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
    )
    reject = await client.post(
        f"/api/agent/artifacts/{outcome2.artifact_id}/reject", headers=headers
    )
    assert reject.status_code == 200 and reject.json()["status"] == "rejected"


# ────────────────────────── Task 4 端到端 + 重放 ──────────────────────────


async def test_end_to_end_skill_run_and_replay(
    api_client, runtime_factory, migrated_postgres: str
):
    """端到端：注册 → 202 接受 → stub loop 调 facade → finalize → 候选产物全 D-10 非空；
    重放同输入 → 新 run、input_hash/血缘一致、不突变第一个 artifact。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"e2e_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    # 1. 注册技能（API 路径，allowed_tools 排除 get_narrative_memory）。
    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(novel_id=seed["novel_id"]).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    # 2. 接受第一次 run（202 + per-run token）。
    accepted1 = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        question=FIXED_QUESTION,
    )
    run1_id = accepted1["run"]["id"]
    run1_hash = accepted1["run"]["input_hash"]

    # 3. stub agent loop → finalize → 断言候选产物全 D-10 字段非空。
    outcome1, _ = await _run_to_artifact(
        runtime_factory,
        run_id=run1_id,
        skill_version_id=svid,
        input_hash=run1_hash,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
    )
    assert outcome1.status == "completed"
    assert await _count_artifacts(runtime_factory, run_id=run1_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run1_id) == 1

    async with runtime_factory() as session:
        artifact1 = await session.get(Artifact, outcome1.artifact_id)
        revision1 = await session.get(ArtifactRevision, outcome1.artifact_revision_id)
        run1_row = await session.get(SkillRun, run1_id)
    assert artifact1 is not None
    assert revision1 is not None
    _assert_d10_fields(revision1.content)
    assert artifact1.status == "candidate"
    assert artifact1.skill_version_id == svid
    assert artifact1.input_hash == run1_hash
    assert run1_row is not None and run1_row.status == "completed"
    assert run1_row.frozen_manifest["connector_versions"] == []
    assert run1_row.frozen_manifest["evidence_refs"] == ["evidence:1"]
    assert run1_row.frozen_manifest["manifest_checksum"] == "m" * 64
    assert run1_row.frozen_manifest["question"] == FIXED_QUESTION
    first_content_hash = revision1.content_hash
    first_revision_count = await _count_revisions(runtime_factory, run_id=run1_id)

    # 4. REPLAY：同输入第二次 run。
    accepted2 = await _accept_run(
        client,
        token=seed["owner_token"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        question=FIXED_QUESTION,
    )
    run2_id = accepted2["run"]["id"]
    run2_hash = accepted2["run"]["input_hash"]

    outcome2, _ = await _run_to_artifact(
        runtime_factory,
        run_id=run2_id,
        skill_version_id=svid,
        input_hash=run2_hash,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
    )
    assert outcome2.status == "completed"
    assert run2_id != run1_id  # 新 run 行

    async with runtime_factory() as session:
        run2_row = await session.get(SkillRun, run2_id)
        artifact2 = await session.get(Artifact, outcome2.artifact_id)
    assert run2_row is not None
    # 重放可追溯：input_hash / skill_version / model_lineage / source_versions 一致。
    assert run2_hash == run1_hash
    assert run2_row.skill_version_id == run1_row.skill_version_id == svid
    assert run2_row.model_lineage == run1_row.model_lineage
    assert run2_row.source_versions == run1_row.source_versions

    # 不突变第一个 run 的 artifact。
    assert (
        await _count_revisions(runtime_factory, run_id=run1_id)
        == first_revision_count
        == 1
    )
    async with runtime_factory() as session:
        rev1_again = await session.get(ArtifactRevision, outcome1.artifact_revision_id)
    assert rev1_again is not None
    assert rev1_again.content_hash == first_content_hash
    assert artifact2 is not None and artifact2.id != artifact1.id

    # API 读取产物分页形状 {"items","total","skip","limit"}。
    list_resp = await client.get(
        f"/api/agent/novels/{seed['novel_id']}/artifacts", headers=headers
    )
    assert list_resp.status_code == 200
    payload = list_resp.json()
    assert {"items", "total", "skip", "limit"} <= set(payload)
    assert payload["total"] >= 2
    rev_resp = await client.get(
        f"/api/agent/novels/{seed['novel_id']}/artifacts/{artifact1.id}/revisions",
        headers=headers,
    )
    assert rev_resp.status_code == 200
    rev_payload = rev_resp.json()
    assert len(rev_payload["items"]) == 1
    assert rev_payload["items"][0]["content"]["input_hash"] == run1_hash


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("connector_versions", [{"connector_id": 999}]),
        ("evidence_refs", ["evidence:tampered"]),
    ],
)
async def test_finalize_rejects_frozen_manifest_field_drift(
    runtime_factory,
    migrated_postgres: str,
    field: str,
    tampered_value: list[Any],
):
    """accepted 后同名 manifest/evidence 变化必须 fail closed，且零写入。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    frozen = {
        "connector_versions": [{"connector_id": 1}],
        "evidence_refs": ["evidence:1"],
    }
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=svid,
            status="running",
            input={"question": FIXED_QUESTION, "novel_id": seed["novel_id"]},
            input_hash="a" * 64,
            frozen_manifest=frozen,
            budget_snapshot={"max_calls": 10},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    loop = await stub_agent_loop(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        question=FIXED_QUESTION,
        skill_version_id=svid,
        input_hash="a" * 64,
    )
    submitted_manifest = dict(loop["frozen_manifest"])
    submitted_manifest[field] = tampered_value
    outcome = await finalize_skill_run(
        runtime_factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=loop["envelope"],
        model_lineage=loop["model_lineage"],
        source_versions=loop["source_versions"],
        usage=loop["usage"],
        frozen_manifest=submitted_manifest,
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason == "frozen manifest changed after freeze"
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 0
    async with runtime_factory() as session:
        run_row = await session.get(SkillRun, run_id)
    assert run_row is not None and run_row.frozen_manifest == frozen


async def test_finalize_http_endpoint_happy_path(
    api_client, runtime_factory, migrated_postgres: str
):
    """finalize HTTP endpoint (25.2-05 trigger): stop -> candidate artifact written."""
    client, _, _ = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix="fh" + uuid.uuid4().hex[:6])
    contract = _skill_contract(novel_id=seed["novel_id"])
    sv_id = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=contract,
    )
    run_input = {"question": FIXED_QUESTION}
    run_hash = canonical_input_hash(run_input)
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=sv_id,
            input=run_input,
            input_hash=run_hash,
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    headers = {"Authorization": "Bearer " + seed["owner_token"]}
    loop = await stub_agent_loop(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        question=FIXED_QUESTION,
        skill_version_id=sv_id,
        input_hash=run_hash,
    )
    resp = await client.post(
        "/api/agent/novels/%d/skill-runs/%d/finalize" % (seed["novel_id"], run_id),
        json={
            "stop_reason": "stop",
            "envelope": loop["envelope"],
            "model_lineage": loop["model_lineage"],
            "source_versions": loop["source_versions"],
            "usage": loop["usage"],
            "frozen_manifest": loop["frozen_manifest"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed", body
    assert body["artifact_id"] is not None
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 1


async def test_finalize_http_endpoint_cancel_no_write(
    api_client, runtime_factory, migrated_postgres: str
):
    """finalize HTTP endpoint cancel branch: cancelled + zero artifact/revision rows."""
    client, _, _ = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix="fc" + uuid.uuid4().hex[:6])
    contract = _skill_contract(novel_id=seed["novel_id"])
    sv_id = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=contract,
    )
    run_input = {"question": FIXED_QUESTION}
    run_hash = canonical_input_hash(run_input)
    async with runtime_factory() as session:
        run = SkillRun(
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            skill_version_id=sv_id,
            input=run_input,
            input_hash=run_hash,
            status="running",
            cancel_requested=True,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    headers = {"Authorization": "Bearer " + seed["owner_token"]}
    resp = await client.post(
        "/api/agent/novels/%d/skill-runs/%d/finalize" % (seed["novel_id"], run_id),
        json={
            "stop_reason": "aborted",
            "envelope": {},
            "model_lineage": {},
            "source_versions": {},
            "usage": {},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "cancelled", body
    assert body["artifact_id"] is None
    assert await _count_artifacts(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 0
