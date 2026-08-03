"""Phase 28-05 集成测试：SkillRun → Tool → Artifact → Validator 端到端边界证明。

证明 Phase 28 确定性叙事记忆能力经版本化 analyze-chapter / build-story-arc Skill
消费（REQ-NM-01..06 + REQ-AGENT-02/03 + D-08/D-09），ChapterAnalysisArtifact /
StoryArcArtifact 是唯一官方 Agent 输出，且全程**无 ApprovalRequest、无 Publisher、
无 promotion、无 Canon 写入**：

正向链：
  register（版本化 manifest：8 工具 allowlist + 空 write_permissions +
  空 approval_required_for）→ accept run（owner/novel/branch + input_hash 绑定）
  → stub loop 调真实 facade 工具（get_chapter / get_evidence_span /
  get_narrative_memory）→ 物化 leaf evidence + 冻结 Frozen Manifest → 携带共享
  26-06 normalization trail 的 ChapterAnalysisArtifact / StoryArcArtifact 信封
  → 确定性 finalizer（integrity gate + 白名单校验）→ candidate 产物 + 首个不可变
  修订。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner / skill_version /
  input_hash（lineage 血缘）、schema drift、missing evidence（heuristic candidate）、
  受保护字段合成（approval_state/authority）、unknown evidence ref、cutoff 服务器
  权威（beyond_cutoff）、digest 索引/EvidenceRef 滥用（D-08）、future-fact next
  hint、Outline/Mainline Canon 提升尝试（D-09）、情绪记忆字段（out of scope）、
  approval bypass（status 非 candidate）。
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
from app.services.narrative_memory.arc_planner import (
    ChapterTerminalState,
    plan_outline_arcs,
)
from app.services.narrative_memory.builder_contracts import (
    CONTEXT_SUMMARY_MAX_LENGTH,
    CONTINUITY_NOTES_MAX_LENGTH,
    NEXT_HINT_MAX_LENGTH,
    TerminalState,
    build_chapter_analysis_artifact,
)
from app.services.narrative_memory.global_builder import project_mainline_candidate
from app.services.queryplan.contracts import leaf_evidence_key
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
CHAPTER2_CONTENT = "第二章正文：阿宁在竹林深处看见了使者的身影，脚步匆匆。"

# Phase 28 编排 allowlist：8 个只读域工具（含叙事记忆候选通道）。
DEFAULT_TOOLS = [
    "get_chapter",
    "get_evidence_span",
    "get_events",
    "get_character_state",
    "get_relationships",
    "get_clues",
    "get_world_rules",
    "get_narrative_memory",
]

# leaf 证据跨度：截取第一章前 10 个 code-point 作为冻结切片。
SPAN_START = 0
SPAN_END = 10
SPAN_EXCERPT = CHAPTER_CONTENT[SPAN_START:SPAN_END]
SPAN_HASH = hashlib.sha256(SPAN_EXCERPT.encode("utf-8")).hexdigest()

SOURCE_SNAPSHOT_HASH = "a" * 64
POLICY_VERSION = "arc-policy.v1"
SPOILER_POLICY = "spoiler-policy.v1"


def evidence_key_for(chapter_id: int) -> str:
    """leaf 证据键：由真实章节 id 计算（seed 后章节 id 由数据库分配）。"""
    return leaf_evidence_key(
        chapter_id=chapter_id,
        source_start=SPAN_START,
        source_end=SPAN_END,
        content_hash=SPAN_HASH,
    )


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _skill_contract(*, novel_id: int, name: str, **overrides: Any) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
        "version": "1.0.0",
        "allowed_tools": list(DEFAULT_TOOLS),
        "read_permissions": ["canon", "derivative", "narrative_memory"],
        "write_permissions": [],
        "forbidden_spaces": ["canon:original", "derivative:write"],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 60_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "3.00",
        },
        "approval_required_for": [],
        "input_schema": {
            "type": "object",
            "properties": {"novel_id": {"type": "integer"}, "chapter_id": {"type": "integer"}},
            "required": ["novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "chapter_analysis"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说（阅读进度止于第 1 章）+ 两章正文。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p28_owner_{suffix}",
            email=f"p28_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P28 Novel {suffix}",
            author="Author",
            owner_id=owner.id,
            status="ready",
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


def _domain_analysis_payload(*, chapter_id: int) -> dict[str, Any]:
    """用领域构建器生成合法的 ChapterAnalysisArtifact 上下文负载（D-08 bounds）。"""
    artifact = build_chapter_analysis_artifact(
        chapter_id=chapter_id,
        chapter_number=1,
        source_snapshot_hash=SOURCE_SNAPSHOT_HASH,
        input_hash=canonical_input_hash({"chapter_id": chapter_id, "source": "fixture"}),
        spoiler_policy_version=SPOILER_POLICY,
        max_length=max(
            CONTEXT_SUMMARY_MAX_LENGTH,
            NEXT_HINT_MAX_LENGTH,
            CONTINUITY_NOTES_MAX_LENGTH,
        ),
        context_payload={"chapter_id": chapter_id},
        chunk_reprs=[{"leaf": evidence_key_for(chapter_id)}],
        previous_context_summary="第一章之前：阿宁进入竹林。",
        next_context_hint="使者踪迹仍是后续的关注焦点。",
        continuity_notes="source_snapshot:aaaa;input:fixture",
    )
    return artifact.model_dump(mode="json")


def _domain_outline_mainline(*, owner_id: int, novel_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """用领域规划器生成合法的 Outline/Mainline 候选（candidate-only）。"""
    terminal = (
        ChapterTerminalState(
            chapter_number=1,
            terminal_state=TerminalState.COMPLETED,
            reason_code="completed_candidate",
            source_snapshot_hash=SOURCE_SNAPSHOT_HASH,
            input_hash="1" * 64,
        ),
        ChapterTerminalState(
            chapter_number=2,
            terminal_state=TerminalState.COMPLETED,
            reason_code="completed_candidate",
            source_snapshot_hash=SOURCE_SNAPSHOT_HASH,
            input_hash="2" * 64,
        ),
    )
    outline = plan_outline_arcs(
        chapters=terminal,
        policy_version=POLICY_VERSION,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=1,
        hierarchy_build_id="hierarchy:1",
        hierarchy_checksum="b" * 64,
    )
    mainline = project_mainline_candidate(outline=outline)
    return outline.model_dump(mode="json"), mainline.model_dump(mode="json")


def _raw_model_output(*, artifact_type: str, with_evidence: bool = True) -> dict[str, Any]:
    """模拟模型原始结构化输出（含需声明的 alias 偏差；不带 lineage）。"""
    raw: dict[str, Any] = {
        "type": artifact_type,
        "schema_version": "chapter-analysis.v1"
        if artifact_type == "chapter_analysis"
        else "story-arc.v1",
        "skill_name": "analyze-chapter"
        if artifact_type == "chapter_analysis"
        else "build-story-arc",
        "skill_version": "1.0.0",
        "status": "candidate",
    }
    return raw


def _repair(raw: dict[str, Any], lineage: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """按共享 26-06 契约做 alias 修复 + lineage 合并 + 领域 payload 物化（确定性）。"""
    repaired: dict[str, Any] = dict(raw)
    repaired["producing_skill"] = repaired.pop("skill_name")
    repaired["producing_skill_version"] = repaired.pop("skill_version")
    repaired.update(payload)
    for key, value in lineage.items():
        repaired[key] = value
    return repaired


def _build_envelope(
    *,
    artifact_type: str,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    payload: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    with_evidence: bool = True,
    repair: str = "declared",
    extra: dict[str, Any] | None = None,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    stale_hash: bool = False,
    trail_inconsistent: bool = False,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 Phase 28 信封。"""
    evidence_key = "evidence:1"
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
        "source_versions": {"novel": "v1", "narrative_memory": "v1"},
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": [evidence_key] if with_evidence else [],
        "parent_revision": None,
    }
    raw = _raw_model_output(artifact_type=artifact_type, with_evidence=with_evidence)
    repaired = _repair(raw, lineage, payload)
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
                "after": repaired["producing_skill"],
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
    repaired["tool_runs"] = tool_runs
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
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=None,
            input={"novel_id": novel_id, "chapter_id": 1},
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 40},
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
            "calls": 3,
            "input_tokens": 300,
            "output_tokens": 120,
            "cost_usd": "0.0006",
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


async def _set_up(factory, sync_url: str, *, suffix: str, skill: str = "analyze-chapter") -> dict[str, Any]:
    """seed owner/novel + 注册 Phase 28 技能 + 创建 running run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"], name=skill),
    )
    input_hash = canonical_input_hash({"novel_id": seed["novel_id"], "chapter_id": 1})
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


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase28_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 analyze-chapter / build-story-arc manifest 注册成功：8 工具 allowlist +
    零写权限 + 零审批动作 + 叙事记忆只读。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    for skill in ("analyze-chapter", "build-story-arc"):
        svid = await _register_skill(
            runtime_factory,
            owner_id=seed["owner_id"],
            novel_id=seed["novel_id"],
            contract=_skill_contract(novel_id=seed["novel_id"], name=skill),
        )
        async with runtime_factory() as session:
            version = await session.get(SkillVersion, svid)
            assert version is not None
            assert version.name == skill
            assert version.version == "1.0.0"
            assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
            assert version.write_permissions == []
            assert version.approval_required_for == []
            assert version.forbidden_spaces == ["canon:original", "derivative:write"]
            assert "narrative_memory" in version.read_permissions
            assert int(version.budget["max_calls"]) == 40


async def test_phase28_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="analyze-chapter",
        allowed_tools=list(DEFAULT_TOOLS) + ["delete_narrative_memory"],
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


async def test_phase28_happy_path_analyze_chapter_artifact(
    runtime_factory, migrated_postgres: str
):
    """正向链 analyze-chapter：真实 facade 工具 → 冻结 manifest → ChapterAnalysisArtifact
    信封 → finalize → candidate 产物 + 修订；digest 绝不 EvidenceRef；无审批/发布/Canon。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ac_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]

    # stub agent loop：真实调用 Phase 28 门面工具（读真实章节 + 叙事记忆候选版本视图）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        chapter = await facade.execute(
            "get_chapter",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"], "chapter_id": ctx["chapter1_id"]},
        )
        assert chapter["chapter_number"] == 1

        span = await facade.execute(
            "get_evidence_span",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "chapter_id": ctx["chapter1_id"],
                "source_start": SPAN_START,
                "source_end": SPAN_END,
                "content_hash": SPAN_HASH,
            },
        )
        assert span["evidence_key"] == evidence_key_for(ctx["chapter1_id"])

        nm = await facade.execute(
            "get_narrative_memory",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"view": "versions"},
        )
        assert nm["release_status"] == "candidate"  # ADR-0002：候选发布

    frozen_manifest = {
        "evidence_refs": [evidence_key_for(ctx["chapter1_id"])],
        "manifest_checksum": "m" * 64,
        "chapter_id": ctx["chapter1_id"],
    }

    analysis = _domain_analysis_payload(chapter_id=ctx["chapter1_id"])
    # 与冻结切片一致的 leaf 证据（D-07/D-08）。
    analysis["chunk_digests"] = [canonical_content_hash({"leaf": evidence_key_for(ctx["chapter1_id"])})]
    analysis["chapter_digest"] = canonical_content_hash({"chapter": ctx["chapter1_id"]})

    payload = {"analysis": analysis}
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        payload=payload,
        tool_runs=[
            {"tool_name": "get_chapter", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": 1},
            {"tool_name": "get_narrative_memory", "calls": 1},
        ],
        repair="declared",
    )
    # envelope 的 evidence_refs 必须与冻结切片一致。
    envelope["evidence_refs"] = [evidence_key_for(ctx["chapter1_id"])]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]

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
    assert artifact.type == "chapter_analysis"
    assert artifact.schema_version == "chapter-analysis.v1"
    assert revision is not None and revision.revision_no == 1
    assert revision.parent_revision_id is None
    assert run_row is not None and run_row.status == "completed"

    content = revision.content
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert canonical_content_hash(_strip_trail(content)) == content["normalization"][
        "repaired_hash"
    ]
    # 血缘绑定。
    assert content["owner_id"] == ctx["owner_id"]
    assert content["novel_id"] == ctx["novel_id"]
    assert content["skill_version_id"] == svid
    assert content["input_hash"] == ctx["input_hash"]
    assert content["evidence_refs"] == [evidence_key_for(ctx["chapter1_id"])]
    # ToolRun 血缘。
    assert content["tool_runs"] == [
        {"tool_name": "get_chapter", "calls": 1},
        {"tool_name": "get_evidence_span", "calls": 1},
        {"tool_name": "get_narrative_memory", "calls": 1},
    ]
    # D-08：digests 是压缩负载，绝不作为 EvidenceRef。
    assert content["analysis"]["chapter_digest"] not in content["evidence_refs"]
    assert content["analysis"]["chapter_digest"].startswith("narrative") is False
    assert all(
        digest not in content["evidence_refs"]
        for digest in content["analysis"]["chunk_digests"]
    )
    # 候选上下文 bound：source/input hash 血缘 + spoiler policy。
    assert content["analysis"]["source_snapshot_hash"] == SOURCE_SNAPSHOT_HASH
    assert content["analysis"]["spoiler_policy_version"] == SPOILER_POLICY
    # official 信封未携带受保护合成字段 / 情绪记忆字段。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state", "emotional_memory"):
        assert forbidden not in content


async def test_phase28_happy_path_build_story_arc_artifact(
    runtime_factory, migrated_postgres: str
):
    """正向链 build-story-arc：Outline/Mainline 候选（candidate-only）→ StoryArcArtifact
    信封 → finalize → candidate 产物；绝不进入 Canon。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"sa_{uuid.uuid4().hex[:6]}",
        skill="build-story-arc",
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]

    # stub agent loop：真实调用叙事记忆候选版本视图 + 章节证据跨度。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        nm = await facade.execute(
            "get_narrative_memory",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"view": "versions"},
        )
        assert nm["release_status"] == "candidate"
        span = await facade.execute(
            "get_evidence_span",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "chapter_id": ctx["chapter1_id"],
                "source_start": SPAN_START,
                "source_end": SPAN_END,
                "content_hash": SPAN_HASH,
            },
        )
        assert span["evidence_key"] == evidence_key_for(ctx["chapter1_id"])

    frozen_manifest = {
        "evidence_refs": [evidence_key_for(ctx["chapter1_id"])],
        "manifest_checksum": "m" * 64,
    }

    outline, mainline = _domain_outline_mainline(
        owner_id=ctx["owner_id"], novel_id=ctx["novel_id"]
    )
    payload = {
        "outline_candidate": outline,
        "mainline_candidate": mainline,
    }
    envelope = _build_envelope(
        artifact_type="story_arc",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        payload=payload,
        tool_runs=[
            {"tool_name": "get_narrative_memory", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": 1},
        ],
        repair="declared",
    )
    envelope["evidence_refs"] = [evidence_key_for(ctx["chapter1_id"])]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]

    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_revisions(runtime_factory, run_id=run_id) == 1
    assert await _count_approvals(runtime_factory, run_id=run_id) == 0

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    assert artifact is not None and artifact.status == "candidate"
    assert artifact.type == "story_arc"
    assert artifact.schema_version == "story-arc.v1"
    assert revision is not None
    content = revision.content
    # candidate-only：Outline/Mainline 保留 candidate status，绝不进入 Canon。
    assert content["outline_candidate"]["candidate_status"] == "candidate"
    assert content["mainline_candidate"]["candidate_status"] == "candidate"
    assert content["outline_candidate"]["policy_version"] == POLICY_VERSION
    assert content["mainline_candidate"]["source_snapshot_hash"] == SOURCE_SNAPSHOT_HASH
    assert content["outline_candidate"]["lineage"]
    assert content["mainline_candidate"]["lineage"]
    assert content["evidence_refs"] == [evidence_key_for(ctx["chapter1_id"])]


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase28_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"], name="analyze-chapter"),
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=canonical_input_hash({"novel_id": seed["novel_id"], "chapter_id": 1}),
        cancel_requested=True,
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=canonical_input_hash({"novel_id": seed["novel_id"], "chapter_id": 1}),
        payload={"analysis": _domain_analysis_payload(chapter_id=seed["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase28_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(invalid_stop_reason)，零官方写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"to_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        stop_reason="max_tokens",
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_INVALID_STOP_REASON
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        wrong_owner=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ver_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        wrong_version=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        wrong_input_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    envelope["schema_version"] = "chapter-analysis.v2"
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "schema" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_missing_evidence_heuristic_candidate_blocks(
    runtime_factory, migrated_postgres: str
):
    """ChapterAnalysisArtifact 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        with_evidence=False,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_forged_approval_protected_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval_state/authority 受保护字段）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"syn_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        extra={"approval_state": "approved", "authority": "model-claimed"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_unknown_evidence_ref_blocks(
    runtime_factory, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ref_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    envelope["evidence_refs"] = ["qp:1:0:10:forgednot64hexhash000000000000000000000000000000000000000000000000"]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "unknown evidence ref" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_cutoff_server_authority(runtime_factory, migrated_postgres: str):
    """cutoff 服务器权威：阅读进度止于第 1 章；get_chapter 第 2 章 → beyond_cutoff。"""
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


async def test_phase28_digest_evidence_ref_misuse_blocks(
    runtime_factory, migrated_postgres: str
):
    """digest 索引/EvidenceRef 滥用（chapter_digest 顶替 EvidenceRef）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"dig_{uuid.uuid4().hex[:6]}"
    )
    analysis = _domain_analysis_payload(chapter_id=ctx["chapter1_id"])
    # 把 chapter_digest 顶替成 evidence_ref（D-08：digest 绝不 double as EvidenceRef）。
    digest = canonical_content_hash({"chapter": ctx["chapter1_id"]})
    analysis["chapter_digest"] = digest
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": analysis},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    envelope["evidence_refs"] = [digest]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [digest]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "digest" in outcome.status_reason
        and "EvidenceRef" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_future_fact_next_hint_blocks(
    runtime_factory, migrated_postgres: str
):
    """future-fact next hint（hint 引用 cutoff 之后章节）→ blocked，零写入（D-08）。

    领域 builder 会在构建期把越界 hint 置 null + 记录稳定原因码；这里绕过 builder，
    直接构造携带越界 hint 的 analysis 负载，证明 integrity gate 的
    ``hint_safe_at_cutoff`` 独立拦截（fail closed）。
    """
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hint_{uuid.uuid4().hex[:6]}"
    )
    analysis = _domain_analysis_payload(chapter_id=ctx["chapter1_id"])
    # 直接注入引用第 9 章（> 截止点 1）的 hint → 泄漏未来事实。
    analysis["next_context_hint"] = "后续第 9 章将揭示使者的身份。"
    analysis["next_hint_reason_code"] = None
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": analysis},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    envelope["evidence_refs"] = [evidence_key_for(ctx["chapter1_id"])]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "hint" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_outline_mainline_canon_attempt_blocks(
    runtime_factory, migrated_postgres: str
):
    """Outline/Mainline Canon 提升尝试（candidate_status != candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"canon_{uuid.uuid4().hex[:6]}",
        skill="build-story-arc",
    )
    outline, mainline = _domain_outline_mainline(
        owner_id=ctx["owner_id"], novel_id=ctx["novel_id"]
    )
    outline["candidate_status"] = "published"  # Canon 提升尝试
    payload = {
        "outline_candidate": outline,
        "mainline_candidate": mainline,
    }
    envelope = _build_envelope(
        artifact_type="story_arc",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload=payload,
        tool_runs=[{"tool_name": "get_narrative_memory", "calls": 1}],
    )
    envelope["evidence_refs"] = [evidence_key_for(ctx["chapter1_id"])]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_emotional_memory_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """情绪记忆字段（out of scope）→ blocked，零写入（wire schema extra=forbid）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"emo_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
        extra={"emotional_memory": {"valence": "sad"}},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase28_approval_bypass_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：envelope status 非 candidate（如 approved）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        payload={"analysis": _domain_analysis_payload(chapter_id=ctx["chapter1_id"])},
        tool_runs=[{"tool_name": "get_chapter", "calls": 1}],
    )
    envelope["status"] = "approved"
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


# ────────────────────────── HTTP 端到端（register → accept → finalize） ──────────────────────────


async def test_phase28_http_end_to_end_no_approval_no_publisher(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ stub loop → finalize →
    candidate ChapterAnalysisArtifact + revision；无 ApprovalRequest、无发布路径。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(
            novel_id=seed["novel_id"], name="analyze-chapter"
        ).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    accepted = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs",
        json={
            "skill_version_id": svid,
            "input": {
                "novel_id": seed["novel_id"],
                "chapter_id": seed["chapter1_id"],
                "question": "请分析第一章的章节状态与连续性。",
            },
        },
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["internal_token"], "202 must mint a per-run internal token"
    run_id = body["run"]["id"]
    run_hash = body["run"]["input_hash"]

    analysis = _domain_analysis_payload(chapter_id=seed["chapter1_id"])
    analysis["chapter_digest"] = canonical_content_hash({"chapter": seed["chapter1_id"]})
    analysis["chunk_digests"] = [
        canonical_content_hash({"leaf": evidence_key_for(seed["chapter1_id"])})
    ]
    envelope = _build_envelope(
        artifact_type="chapter_analysis",
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=run_hash,
        payload={"analysis": analysis},
        tool_runs=[
            {"tool_name": "get_chapter", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": 1},
            {"tool_name": "get_narrative_memory", "calls": 1},
        ],
        repair="declared",
    )
    envelope["evidence_refs"] = [evidence_key_for(seed["chapter1_id"])]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]

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
            "source_versions": {"novel": "v1", "narrative_memory": "v1"},
            "usage": {
                "calls": 3,
                "input_tokens": 300,
                "output_tokens": 120,
                "cost_usd": "0.0006",
            },
            "frozen_manifest": {
                "evidence_refs": [evidence_key_for(seed["chapter1_id"])],
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
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.run_id == run_id)
        )
    assert artifact is not None and artifact.status == "candidate"
    assert artifact.type == "chapter_analysis"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
