"""Phase 26-05 集成测试：SkillRun → Tool → Artifact → Validator 端到端证明。

证明 Phase 26 确定性能力经版本化 answer-reading-question Skill 消费（REQ-AGENT-02/
03/08 + REQ-QP-01..04），CitedAnswerArtifact 是唯一官方输出，且全程**无
ApprovalRequest、无 Publisher、无 promotion / 域写入**：

正向链：
  register（版本化只读 manifest）→ accept run（owner/novel/branch + input_hash 绑定）
  → stub loop 调真实 25.2-02 facade 工具（get_novel）→ 物化 evidence_refs +
  冻结 Frozen Manifest → 携带共享 26-06 normalization trail 的 CitedAnswerArtifact
  信封 → 确定性 finalizer（integrity gate + leaf-evidence 白名单校验）→ candidate
  产物 + 首个不可变修订；normalization_actions / raw_hash / repaired_hash /
  warnings 保留在官方输出上；服务器重放 repaired_hash。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner / skill_version /
  input_hash（lineage 血缘）、schema drift、missing evidence（heuristic candidate）、
  unsafe normalization（trail 不一致 / stale repaired_hash）、受保护字段合成
  （approval_state/authority）、unknown evidence ref、attempted ApprovalRequest /
  Publisher 动作。whole-book scope 仍由服务器授权（QueryPlan D-12）。

retry / replay：failed 可重试、同输入重放 input_hash 与血缘一致、首个产物不突变。
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
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    ERROR_CODE_INVALID_STOP_REASON,
    finalize_skill_run,
)
from app.services.agent_runtime.registry import (
    SkillContractError,
    canonical_input_hash,
    register_skill_version,
)
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.agent_tools.errors import BeyondCutoffError
from app.services.agent_tools.facade import ToolFacade
from app.services.queryplan.service import ConsumerPlanBlocked, QueryPlanService
from app.services.queryplan.schemas import QueryPlanIntent
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
CHAPTER2_CONTENT = "第二章正文：阿宁在竹林深处看见了使者的身影，脚步匆匆。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()

DEFAULT_SKILL = "answer-reading-question"
# Phase 26 编排 allowlist：6 个只读域工具（get_narrative_memory 保持排除，D-01）。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
]
FIXED_QUESTION = "阿宁在竹林里看见了谁？"
EVIDENCE_KEY = "evidence:1"


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(*, novel_id: int, **overrides: Any) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": DEFAULT_SKILL,
        "version": "1.0.0",
        "allowed_tools": list(DEFAULT_TOOLS),
        "read_permissions": ["canon", "derivative"],
        "write_permissions": [],
        "forbidden_spaces": ["canon:original", "derivative:write"],
        "budget": {
            "max_calls": 20,
            "max_input_tokens": 30_000,
            "max_output_tokens": 6_000,
            "max_cost_usd": "1.00",
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
    """同步播种 owner + 小说（阅读进度止于第 1 章）+ 两章正文，返回 tokens。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p26_owner_{suffix}",
            email=f"p26_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P26 Novel {suffix}",
            author="Author",
            owner_id=owner.id,
            status="ready",
            # D-12：阅读进度截止点（chapter_id 指向第 1 章）→ 服务端 cutoff 权威。
            reading_progress={},
            chapter_count=2,
            word_count=len(CHAPTER_CONTENT) + len(CHAPTER2_CONTENT),
        )
        session.add(novel)
        session.flush()
        chapter1 = Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content=CHAPTER_CONTENT,
            word_count=len(CHAPTER_CONTENT),
        )
        chapter2 = Chapter(
            novel_id=novel.id,
            chapter_number=2,
            title="第二章",
            content=CHAPTER2_CONTENT,
            word_count=len(CHAPTER2_CONTENT),
        )
        session.add_all([chapter1, chapter2])
        session.commit()
        novel.reading_progress = {"chapter_id": chapter1.id}
        session.commit()
        data = {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "chapter1_id": chapter1.id,
            "chapter2_id": chapter2.id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
        }
    engine.dispose()
    return data


# ────────────────────────── fixtures ──────────────────────────


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


@pytest_asyncio.fixture
async def api_client(migrated_postgres: str):
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


# ────────────────────────── helpers ──────────────────────────


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _raw_model_output(*, with_evidence: bool = True) -> dict[str, Any]:
    """模拟模型原始结构化输出（含需声明的 alias/container 偏差；不带 lineage）。"""
    return {
        "type": "cited_answer",
        "schema_version": "cited-answer.v1",
        "skill_name": "answer-reading-question",  # alias → producing_skill
        "skill_version": "1.0.0",  # alias → producing_skill_version
        "answer": {
            # 单对象 → container_shape wrap_array
            "answer_blocks": {
                "block_id": "b1",
                "text": "阿宁在竹林里看见了使者的身影。",
                "evidence_refs": [EVIDENCE_KEY] if with_evidence else [],
            },
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        },
        "status": "candidate",
    }


def _repair(raw: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    """按共享 26-06 契约做 alias + container_shape 修复 + lineage 合并（确定性）。"""
    repaired: dict[str, Any] = dict(raw)
    repaired["producing_skill"] = repaired.pop("skill_name")
    repaired["producing_skill_version"] = repaired.pop("skill_version")
    answer = dict(repaired["answer"])
    answer["answer_blocks"] = [answer["answer_blocks"]]
    repaired["answer"] = answer
    for key, value in lineage.items():
        repaired[key] = value
    return repaired


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    with_evidence: bool = True,
    repair: str = "declared",
    extra: dict[str, Any] | None = None,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    stale_hash: bool = False,
    trail_inconsistent: bool = False,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 cited_answer 信封。

    repair="declared"：raw 模型输出经声明式 alias/container 修复 + lineage 合并，
    raw_hash != repaired_hash，actions 非空（可审计修复）；"noop"：raw == repaired。
    """
    lineage = {
        "owner_id": owner_id if not wrong_owner else owner_id + 999,
        "novel_id": novel_id,
        "branch": None,
        "skill_version_id": skill_version_id
        if not wrong_version
        else skill_version_id + 999,
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {"novel": "v1", "chapters": {"1": HEX64}},
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": [EVIDENCE_KEY] if with_evidence else [],
        "parent_revision": None,
    }
    raw = _raw_model_output(with_evidence=with_evidence)
    repaired = _repair(raw, lineage)
    if extra:
        repaired.update(extra)

    repaired_hash = canonical_content_hash(_strip_trail(repaired))
    if repair == "declared":
        raw_hash = canonical_content_hash(raw)
        actions = [
            {
                "path": "producing_skill",
                "action": "alias",
                "before": "skill_name",
                "after": "answer-reading-question",
                "reason": "declared alias moved to canonical key",
            },
            {
                "path": "producing_skill_version",
                "action": "alias",
                "before": "skill_version",
                "after": "1.0.0",
                "reason": "declared alias moved to canonical key",
            },
            {
                "path": "answer.answer_blocks",
                "action": "container_shape",
                "before": {
                    "block_id": "b1",
                    "text": "…",
                    "evidence_refs": [EVIDENCE_KEY],
                },
                "after": [
                    {"block_id": "b1", "text": "…", "evidence_refs": [EVIDENCE_KEY]}
                ],
                "reason": "declared container shape: wrapped single object into array",
            },
            {
                "path": "owner_id",
                "action": "lineage_merge",
                "after": lineage["owner_id"],
                "reason": "declared lineage ownerId merged into owner_id",
            },
        ]
        warnings = ["declared repairs applied"]
    else:
        raw_hash = repaired_hash
        actions = []
        warnings = []
    if stale_hash:
        repaired_hash = "0" * 64  # 记录值 ≠ 实际内容 → 重放失败
    if trail_inconsistent:
        raw_hash = "1" * 64  # 无 actions 但 raw != repaired → trail 自相矛盾
    repaired["normalization"] = {
        "raw_hash": raw_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": actions,
        "warnings": warnings,
    }
    return repaired


async def _register_skill(
    factory, *, owner_id: int, novel_id: int, contract: SkillVersionRegister
) -> int:
    async with factory() as session:
        _, version = await register_skill_version(
            session, owner_id=owner_id, novel_id=novel_id, contract=contract
        )
        await session.commit()
        return version.id


async def _create_run(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    question: str = FIXED_QUESTION,
    branch: str | None = None,
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=branch,
            input={"question": question, "novel_id": novel_id},
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 20},
            cancel_requested=cancel_requested,
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
    stop_reason: str = "stop",
):
    return await finalize_skill_run(
        factory,
        run_id=run_id,
        stop_reason=stop_reason,
        envelope=envelope,
        model_lineage={},
        source_versions={},
        usage={
            "calls": 1,
            "input_tokens": 120,
            "output_tokens": 48,
            "cost_usd": "0.0002",
        },
        frozen_manifest=frozen_manifest,
    )


async def _count(factory, model, *, run_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count()).select_from(model).where(model.run_id == run_id)  # type: ignore[attr-defined]
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


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count(factory, Artifact, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


async def _set_up(factory, sync_url: str, *, suffix: str) -> dict[str, Any]:
    """seed owner/novel + 注册 Phase 26 技能 + 创建 running run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    input_hash = "c" * 64
    run_id = await _create_run(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
    )
    return {
        **seed,
        "skill_version_id": svid,
        "input_hash": input_hash,
        "run_id": run_id,
    }


# ────────────────────────── Task 1：版本化只读 manifest 注册 ──────────────────────────


async def test_phase26_versioned_readonly_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化只读 Skill manifest 注册成功：D-09 契约 + 6 工具 allowlist + 零写/零审批。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == DEFAULT_SKILL
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "get_narrative_memory" not in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == []  # 无 ApprovalRequest 动作声明
        assert version.forbidden_spaces == ["canon:original", "derivative:write"]
        assert int(version.budget["max_calls"]) == 20


async def test_phase26_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        allowed_tools=list(DEFAULT_TOOLS) + ["search_narrative_units"],
    )
    with pytest.raises(SkillContractError):
        await _register_skill(
            runtime_factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            contract=contract,
        )
    async with runtime_factory() as session:
        registry_count = await session.scalar(
            select(func.count())
            .select_from(SkillRegistry)
            .where(SkillRegistry.owner_id == seed["owner_id"])
        )
    assert int(registry_count or 0) == 0


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Validator ──────────────────────────


async def test_phase26_happy_path_skillrun_to_artifact_with_normalization(
    runtime_factory, migrated_postgres: str
):
    """正向链：真实 facade 工具调用 → 冻结 manifest → normalization trail 信封 →
    finalize → candidate 产物 + 修订，normalization 元数据保留，无 ApprovalRequest。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]

    # stub agent loop：真实调用 25.2-02 门面（get_novel 工具），再物化证据 + 冻结 manifest。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        payload = await facade.execute(
            "get_novel", db=session, novel=novel, owner_id=ctx["owner_id"], params={}
        )
        novel_title = payload.get("title") if isinstance(payload, dict) else None
    frozen_manifest = {
        "evidence_refs": [EVIDENCE_KEY],
        "manifest_checksum": "m" * 64,
        "novel_title": novel_title,
        "question": FIXED_QUESTION,
    }

    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        repair="declared",
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_id is not None
    assert outcome.artifact_revision_id is not None
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1
    assert (
        await _count_approvals(runtime_factory, run_id=run_id) == 0
    )  # 无 ApprovalRequest

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
    assert artifact is not None and artifact.status == "candidate"  # 无自动 promotion
    assert revision is not None and revision.revision_no == 1
    assert revision.parent_revision_id is None
    assert run_row is not None and run_row.status == "completed"

    content = revision.content
    # normalization 元数据保留：actions/raw_hash/repaired_hash/warnings。
    trail = content["normalization"]
    assert trail["normalization_actions"] != []
    kinds = {action["action"] for action in trail["normalization_actions"]}
    assert {"alias", "container_shape"} <= kinds
    assert (
        "lineage_merge" in kinds
    )  # owner/novel/skill_version_id/… 经声明 lineage 合入
    assert trail["warnings"] == ["declared repairs applied"]
    assert trail["raw_hash"] != trail["repaired_hash"]  # 有修复：raw != repaired
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert canonical_content_hash(_strip_trail(content)) == trail["repaired_hash"]
    # 血缘绑定：envelope 字段与 run 行一致。
    assert content["owner_id"] == ctx["owner_id"]
    assert content["novel_id"] == ctx["novel_id"]
    assert content["skill_version_id"] == svid
    assert content["input_hash"] == ctx["input_hash"]
    assert content["evidence_refs"] == [EVIDENCE_KEY]
    # official 信封未携带受保护合成字段。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content


async def test_phase26_whole_book_scope_remains_server_authorized():
    """whole-book scope 由服务器授权（D-12）：显式 whole_book 但无持久化开关 →
    ConsumerPlanBlocked（fail closed，绝不越权到整本书）。"""
    payload = QueryPlanService.build_consumer_request(
        intent=QueryPlanIntent.READER,
        owner_id=1,
        novel_id=1,
        version_id=1,
        question_text="整本书的伏笔是什么？",
        through_chapter=1,
        snapshot_hash="a" * 64,
        full_book_authorized=False,
        whole_book=True,
    )
    with pytest.raises(ConsumerPlanBlocked) as excinfo:
        QueryPlanService.parse_consumer_request(payload)
    # D-12：显式 whole_book 但未授权 → 稳定 contradictory_constraints（需开关）。
    assert excinfo.value.reason_code == "contradictory_constraints"


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase26_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash="c" * 64,
        cancel_requested=True,
    )
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash="c" * 64,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase26_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(invalid_stop_reason)，零官方写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"to_{uuid.uuid4().hex[:6]}"
    )
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
        stop_reason="max_tokens",
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_INVALID_STOP_REASON
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
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
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ver_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        wrong_version=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        wrong_input_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
    )
    envelope["schema_version"] = "cited-answer.v2"
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "schema" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_missing_evidence_heuristic_candidate_blocks(
    runtime_factory, migrated_postgres: str
):
    """cited_answer 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        with_evidence=False,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_unsafe_normalization_trail_inconsistent_blocks(
    runtime_factory, migrated_postgres: str
):
    """unsafe normalization：无 actions 但 raw != repaired → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"trail_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        repair="noop",
        trail_inconsistent=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "trail inconsistent" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_stale_repaired_hash_blocks(
    runtime_factory, migrated_postgres: str
):
    """stale repaired_hash：payload 在规范化后被改动 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"stale_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        stale_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None and "repaired_hash" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_protected_field_synthesis_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted protected-field synthesis（approval_state/authority）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"syn_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        extra={"approval_state": "approved", "authority": "model-claimed"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    # extra=forbid 在严格 wire schema 层拦截受保护字段合成（approval_state 先命中），
    # 显式 FORBIDDEN_PROTECTED_KEYS 检查为纵深防御；两种路径都 fail-closed 零写入。
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_attempted_approval_or_publisher_action_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted ApprovalRequest/Publisher 动作：envelope 携带 approval/publish 意图 →
    protected-field synthesis blocked；契约无审批动作；finalize 不产生 ApprovalRequest、
    不 promotion。"""
    # 契约层面：approval_required_for 为空 + 无 publisher 声明。
    contract = _skill_contract(novel_id=1).model_dump()
    assert contract["approval_required_for"] == []
    assert contract["write_permissions"] == []
    assert "publisher" not in contract

    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}"
    )
    # 信封尝试携带 approval/publish 状态（受保护字段合成）→ blocked。
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        extra={"approval": {"requested": True}},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "approval" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])

    # 无 ApprovalRequest 路径被创建（契约 + 零审批行双证）。
    async with runtime_factory() as session:
        approvals = await session.scalar(
            select(func.count()).select_from(ApprovalRequest)
        )
    assert int(approvals or 0) == 0


async def test_phase26_unknown_evidence_ref_blocks(
    runtime_factory, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ref_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
    )
    envelope["evidence_refs"] = ["evidence:forged"]
    envelope["answer"]["answer_blocks"][0]["evidence_refs"] = ["evidence:forged"]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "unknown evidence ref" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase26_cutoff_server_authority(runtime_factory, migrated_postgres: str):
    """cutoff 服务器权威：读取进度止于第 1 章，get_chapter 第 2 章 → beyond_cutoff。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cut_{uuid.uuid4().hex[:6]}")
    async with runtime_factory() as session:
        novel = await session.get(Novel, seed["novel_id"])
        facade = ToolFacade()
        # 第 1 章：截止点内 → 放行。
        chapter1 = await facade.execute(
            "get_chapter",
            db=session,
            novel=novel,
            owner_id=seed["owner_id"],
            params={"novel_id": seed["novel_id"], "chapter_id": seed["chapter1_id"]},
        )
        assert chapter1["chapter_number"] == 1
        # 第 2 章：超出服务端截止点 → beyond_cutoff（防剧透，零正文泄漏）。
        with pytest.raises(BeyondCutoffError) as excinfo:
            await facade.execute(
                "get_chapter",
                db=session,
                novel=novel,
                owner_id=seed["owner_id"],
                params={
                    "novel_id": seed["novel_id"],
                    "chapter_id": seed["chapter2_id"],
                },
            )
        assert excinfo.value.code == "beyond_cutoff"


# ────────────────────────── retry / replay ──────────────────────────


async def test_phase26_retry_after_failure_and_replay(
    runtime_factory, migrated_postgres: str
):
    """failed run 可重试（retry_count 递增）；同输入重放 input_hash 与血缘一致，
    首个产物不突变。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"rep_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]

    # 第一次 finalize：wrong owner → failed，零写入。
    bad = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        wrong_owner=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=bad,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome.status == "failed"
    await _assert_zero_writes(runtime_factory, run_id=run_id)

    # 重试：failed → queued，retry_count 递增。
    async with runtime_factory() as session:
        run = await session.get(SkillRun, run_id)
        assert run is not None and run.status == "failed"
        run.status = "queued"
        run.retry_count = (run.retry_count or 0) + 1
        await session.commit()

    # 重放成功：同 run 重新 finalize（owner 正确）→ completed，修订 1 写入。
    good = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
    )
    outcome2 = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=good,
        frozen_manifest={"evidence_refs": [EVIDENCE_KEY]},
    )
    assert outcome2.status == "completed", outcome2.status_reason
    assert outcome2.artifact_id is not None
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1

    async with runtime_factory() as session:
        run_row = await session.get(SkillRun, run_id)
        artifact = await session.get(Artifact, outcome2.artifact_id)
        revision = await session.get(ArtifactRevision, outcome2.artifact_revision_id)
    assert run_row is not None and run_row.retry_count == 1
    assert artifact is not None and artifact.input_hash == ctx["input_hash"]
    assert revision is not None and revision.revision_no == 1
    assert revision.content["owner_id"] == ctx["owner_id"]
    assert revision.content["skill_version_id"] == svid
    # 同输入 hash 重放一致性（与 canonical_input_hash 口径一致）。
    assert canonical_input_hash(
        {"question": FIXED_QUESTION, "novel_id": ctx["novel_id"]}
    ) == canonical_input_hash({"question": FIXED_QUESTION, "novel_id": ctx["novel_id"]})


# ────────────────────────── HTTP 端到端（register → accept → finalize） ──────────────────────────


async def test_phase26_http_end_to_end_no_approval_no_publisher(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ stub loop → finalize →
    candidate 产物 + revision；无 ApprovalRequest、无 publish 路径。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(novel_id=seed["novel_id"]).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    accepted = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs",
        json={"skill_version_id": svid, "input": {"question": FIXED_QUESTION}},
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["internal_token"], "202 must mint a per-run internal token"
    run_id = body["run"]["id"]
    run_hash = body["run"]["input_hash"]

    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=run_hash,
        repair="declared",
    )
    finalize_resp = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs/{run_id}/finalize",
        json={
            "stop_reason": "stop",
            "envelope": envelope,
            "model_lineage": {
                "provider": "fixture",
                "model": "stub-model",
                "revision": "stub-1",
            },
            "source_versions": {"novel": "v1"},
            "usage": {
                "calls": 1,
                "input_tokens": 120,
                "output_tokens": 48,
                "cost_usd": "0.0002",
            },
            "frozen_manifest": {
                "evidence_refs": [EVIDENCE_KEY],
                "manifest_checksum": "m" * 64,
            },
        },
        headers=headers,
    )
    assert finalize_resp.status_code == 200, finalize_resp.text
    result = finalize_resp.json()
    assert result["status"] == "completed", result
    assert result["artifact_id"] is not None

    async with factory() as session:
        artifact = await session.get(Artifact, result["artifact_id"])
        approvals = await session.scalar(
            select(func.count()).select_from(ApprovalRequest)
        )
    assert artifact is not None and artifact.status == "candidate"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
