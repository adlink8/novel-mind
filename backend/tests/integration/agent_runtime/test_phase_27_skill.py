"""Phase 27-05 集成测试：SkillRun → Tool → Artifact → Validator/Gate → Approval/Publisher。

证明 Phase 27 确定性世界模型能力经版本化 propose-world-model-candidates Skill
消费（REQ-AGENT-02/03 + REQ-WM-01..04），WorldModelCandidateArtifact 是唯一官方
Agent 输出，且全程**无域写入、无直接 Canon 发布**：

正向链：
  register（版本化 manifest：6 工具 allowlist + world_model:user_interpretation
  审批声明 + 空 write_permissions）→ accept run（owner/novel/branch +
  input_hash 绑定）→ stub loop 调真实 27-05 facade 工具（get_events /
  get_world_rules / get_character_state / get_evidence_span，读真实持久化
  世界模型投影与章节）→ 物化 leaf evidence + 冻结 Frozen Manifest → 携带共享
  26-06 normalization trail 的 WorldModelCandidateArtifact 信封 → 确定性
  finalizer（integrity gate + 白名单校验）→ candidate 产物 + 首个不可变修订。
  然后确定性 WorldModelGate 单独把候选裁决为持久化投影（publisher），Agent
  无法直接发布 canon_fact；user interpretation 需要 owner 作用域审批。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner /
  skill_version / input_hash（lineage 血缘）、schema drift、missing evidence、
  受保护字段合成（authority/approval 伪造）、unknown evidence ref、cutoff
  服务器权威（beyond_cutoff）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
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
from app.models.world_model_event import WorldModelEvent
from app.schemas.agent_approvals import ApprovalRequestCreate
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime import approvals as approval_service
from app.services.agent_runtime.artifacts import (
    ArtifactStateError,
    transition_artifact_status,
)
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    ERROR_CODE_UPSTREAM_ERROR,
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
from app.services.queryplan.contracts import leaf_evidence_key
from app.services.world_model.claims import CausalEdgeClaim, EventClaim
from app.services.world_model.contracts import (
    Authority,
    CausalEdge,
    EventFact,
    WorldModelCandidateProjection,
)
from app.services.world_model.entities import (
    EntityClaim,
    EntityGate,
    EntityLinkClaim,
    build_entity_candidate,
)
from app.services.world_model.entity_repository import WorldEntityRepository
from app.services.world_model.event_repository import WorldModelEventRepository
from app.services.world_model.gates import WorldModelGate, build_candidate
from app.services.world_model.knowledge import (
    EpistemicClaim,
    EpistemicGate,
    build_knowledge_candidate,
)
from app.services.world_model.knowledge_repository import KnowledgeRepository
from app.services.world_model.rules import RuleClaim, RuleExceptionClaim, RuleGate
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "world_model"
FIXTURE_EVENTS = json.loads((FIXTURES / "events_v1.json").read_text(encoding="utf-8"))
FIXTURE_EPISTEMIC = json.loads(
    (FIXTURES / "epistemic_v1.json").read_text(encoding="utf-8")
)
FIXTURE_ENTITIES = json.loads(
    (FIXTURES / "entities_v1.json").read_text(encoding="utf-8")
)

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
CHAPTER2_CONTENT = "第二章正文：阿宁在竹林深处看见了使者的身影，脚步匆匆。"
HEX64 = hashlib.sha256(CHAPTER_CONTENT.encode("utf-8")).hexdigest()

DEFAULT_SKILL = "propose-world-model-candidates"
# Phase 27 编排 allowlist：6 个只读世界模型/关系工具。
DEFAULT_TOOLS = [
    "get_events",
    "get_character_state",
    "get_character_knowledge",
    "get_relationships",
    "get_world_rules",
    "get_evidence_span",
]

# leaf 证据跨度：截取第一章前 10 个 code-point 作为冻结切片。
SPAN_START = 0
SPAN_END = 10
SPAN_EXCERPT = CHAPTER_CONTENT[SPAN_START:SPAN_END]
SPAN_HASH = hashlib.sha256(SPAN_EXCERPT.encode("utf-8")).hexdigest()


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


def _skill_contract(*, novel_id: int, **overrides: Any) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": DEFAULT_SKILL,
        "version": "1.0.0",
        "allowed_tools": list(DEFAULT_TOOLS),
        "read_permissions": ["canon", "derivative", "world_model"],
        "write_permissions": [],
        "forbidden_spaces": ["canon:original", "derivative:write"],
        "budget": {
            "max_calls": 30,
            "max_input_tokens": 40_000,
            "max_output_tokens": 8_000,
            "max_cost_usd": "1.50",
        },
        "approval_required_for": ["world_model:user_interpretation"],
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
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说（阅读进度止于第 1 章）+ 两章正文。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p27_owner_{suffix}",
            email=f"p27_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P27 Novel {suffix}",
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


# ────────────────────────── world-model 投影构建（复用 27-01..04 fixture） ──────────────────────────


def _load_scenario(fixture: dict, name: str) -> dict:
    return fixture["scenarios"][name]


def build_event_projection(
    *, owner_id: int, novel_id: int, version_id: int = 1
) -> WorldModelCandidateProjection:
    """事件 fixture 'valid' → gate → 不可变候选投影（owner/novel 重新作用域）。"""
    scenario = _load_scenario(FIXTURE_EVENTS, "valid")
    scope = scenario["scope"]
    gate = WorldModelGate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    facts: list[EventFact] = []
    for raw in scenario["events"]:
        result = gate.validate_event(
            EventClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            )
        )
        assert result.fact is not None, result.verdicts
        facts.append(result.fact)
    events_by_key = {fact.event_key: fact for fact in facts}
    edges: list[CausalEdge] = []
    for raw in scenario["edges"]:
        result = gate.validate_edge(
            CausalEdgeClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            ),
            events_by_key,
        )
        assert result.edge is not None, result.verdicts
        edges.append(result.edge)
    return build_candidate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        events=facts,
        edges=edges,
    )


def build_knowledge_projection(*, owner_id: int, novel_id: int, version_id: int = 1):
    """epistemic fixture 'valid' → EpistemicGate → 候选投影。"""
    scenario = _load_scenario(FIXTURE_EPISTEMIC, "valid")
    scope = scenario["scope"]
    gate = EpistemicGate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    claims: list[EpistemicClaim] = []
    for raw in scenario["claims"]:
        result = gate.validate_claim(
            EpistemicClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            )
        )
        assert result.claim is not None, result.verdicts
        claims.append(result.claim)
    return build_knowledge_candidate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        claims=claims,
    )


def build_entity_projection(*, owner_id: int, novel_id: int, version_id: int = 1):
    """entities fixture 'valid' → EntityGate + RuleGate → 候选投影。"""
    scenario = _load_scenario(FIXTURE_ENTITIES, "valid")
    scope = scenario["scope"]
    egate = EntityGate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    entities = []
    for raw in scenario["entities"]:
        result = egate.validate_entity(
            EntityClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            )
        )
        assert result.entity is not None, result.verdicts
        entities.append(result.entity)
    links = []
    for raw in scenario["links"]:
        result = egate.validate_link(
            EntityLinkClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            )
        )
        assert result.link is not None, result.verdicts
        links.append(result.link)
    rgate = RuleGate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    rules = []
    for raw in scenario["rules"]:
        result = rgate.validate_rule(
            RuleClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            )
        )
        assert result.rule is not None, result.verdicts
        rules.append(result.rule)
    rule_keys = {rule.rule_key for rule in rules}
    exceptions = []
    for raw in scenario["exceptions"]:
        result = rgate.validate_exception(
            RuleExceptionClaim.model_validate(
                {
                    **raw,
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                }
            ),
            rule_keys,
        )
        assert result.exception is not None, result.verdicts
        exceptions.append(result.exception)
    return build_entity_candidate(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
    )


async def _seed_world_model(factory, *, owner_id: int, novel_id: int) -> None:
    """持久化三个版本化候选投影（events/knowledge/entities+rules，version=1）。"""
    async with factory() as session:
        await WorldModelEventRepository(session).append_projection(
            build_event_projection(owner_id=owner_id, novel_id=novel_id)
        )
        await KnowledgeRepository(session).append_projection(
            build_knowledge_projection(owner_id=owner_id, novel_id=novel_id)
        )
        await WorldEntityRepository(session).append_projection(
            build_entity_projection(owner_id=owner_id, novel_id=novel_id)
        )
        await session.commit()


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


def _raw_model_output(*, chapter_id: int, with_evidence: bool = True) -> dict[str, Any]:
    """模拟模型原始结构化输出（含需声明的 alias/container 偏差；不带 lineage）。"""
    return {
        "type": "world_model_candidate",
        "schema_version": "world-model-candidate.v1",
        "skill_name": "propose-world-model-candidates",  # alias → producing_skill
        "skill_version": "1.0.0",  # alias → producing_skill_version
        "candidates": {
            "projection_version": 1,
            "tool_runs": [
                {"tool_name": "get_events", "calls": 1},
                {"tool_name": "get_evidence_span", "calls": 1},
            ],
            # 单对象 → container_shape wrap_array
            "claims": {
                "claim_kind": "event",
                "claim_key": "e-arrival",
                "proposition": "林安在第一章抵达临安城。",
                "subject": "林安",
                "authority": "probable_inference",
                "confidence": 0.9,
                "disclosure_cutoff": 1,
                "evidence_refs": [evidence_key_for(chapter_id)]
                if with_evidence
                else [],
            },
        },
        "status": "candidate",
    }


def _repair(raw: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    """按共享 26-06 契约做 alias + container_shape 修复 + lineage 合并（确定性）。"""
    repaired: dict[str, Any] = dict(raw)
    repaired["producing_skill"] = repaired.pop("skill_name")
    repaired["producing_skill_version"] = repaired.pop("skill_version")
    candidates = dict(repaired["candidates"])
    candidates["claims"] = [candidates["claims"]]
    repaired["candidates"] = candidates
    for key, value in lineage.items():
        repaired[key] = value
    return repaired


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    skill_version_id: int,
    input_hash: str,
    chapter_id: int = 1,
    with_evidence: bool = True,
    repair: str = "declared",
    extra: dict[str, Any] | None = None,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    stale_hash: bool = False,
    trail_inconsistent: bool = False,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 world_model_candidate 信封。"""
    evidence_key = evidence_key_for(chapter_id)
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
        "source_versions": {"novel": "v1", "world_model": "v1"},
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": [evidence_key] if with_evidence else [],
        "parent_revision": None,
    }
    raw = _raw_model_output(chapter_id=chapter_id, with_evidence=with_evidence)
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
                "after": "propose-world-model-candidates",
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
                "path": "candidates.claims",
                "action": "container_shape",
                "before": {"claim_key": "e-arrival"},
                "after": [{"claim_key": "e-arrival"}],
                "reason": "declared container shape: wrapped single claim into array",
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
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=None,
            input={"novel_id": novel_id, "cutoff": 1},
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 30},
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
            "calls": 2,
            "input_tokens": 200,
            "output_tokens": 90,
            "cost_usd": "0.0004",
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
    """seed owner/novel + 注册 Phase 27 技能 + 创建 running run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(novel_id=seed["novel_id"]),
    )
    input_hash = canonical_input_hash({"novel_id": seed["novel_id"], "cutoff": 1})
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


async def test_phase27_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 World-Model 候选 Skill manifest 注册成功：6 工具 allowlist + 审批声明。"""
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
        assert version.write_permissions == []
        assert version.approval_required_for == ["world_model:user_interpretation"]
        assert version.forbidden_spaces == ["canon:original", "derivative:write"]
        assert int(version.budget["max_calls"]) == 30


async def test_phase27_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        allowed_tools=list(DEFAULT_TOOLS) + ["delete_world_model"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Gate/Approval ──────────────────────────


async def test_phase27_happy_path_skillrun_to_artifact(
    runtime_factory, migrated_postgres: str
):
    """正向链：真实 facade 世界模型工具 → 冻结 manifest → normalization trail 信封 →
    finalize → candidate 产物 + 修订；无 ApprovalRequest、无域写入、无发布。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]
    await _seed_world_model(
        runtime_factory, owner_id=ctx["owner_id"], novel_id=ctx["novel_id"]
    )

    # stub agent loop：真实调用 27-05 门面世界模型工具（读真实持久化投影）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        events = await facade.execute(
            "get_events",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"], "version_id": 1},
        )
        assert events["events"], "get_events 必须返回可见事件投影"
        assert events["events"][0]["event_key"] == "e-arrival"

        rules = await facade.execute(
            "get_world_rules",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"], "version_id": 1},
        )
        # D-05 cutoff 过滤：fixture 规则 disclosure_cutoff=3 > 服务端截止点 1
        # → 规则/例外不下发（不泄漏未来事实）；工具响应形状正确。
        assert "rules" in rules and "exceptions" in rules
        assert rules["rules"] == []
        assert rules["exceptions"] == []

        state = await facade.execute(
            "get_character_state",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"], "version_id": 1, "subject": "lin-an"},
        )
        assert state["claims"], "get_character_state 必须返回可见状态声明"

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
        assert span["excerpt"] == SPAN_EXCERPT

    frozen_manifest = {
        "evidence_refs": [evidence_key_for(ctx["chapter1_id"])],
        "manifest_checksum": "m" * 64,
        "projection_version": 1,
    }

    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        chapter_id=ctx["chapter1_id"],
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
    )  # finalize 不自动铸造审批

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
    assert artifact is not None and artifact.status == "candidate"  # 无自动 promotion
    assert artifact.type == "world_model_candidate"
    assert artifact.schema_version == "world-model-candidate.v1"
    assert revision is not None and revision.revision_no == 1
    assert revision.parent_revision_id is None
    assert run_row is not None and run_row.status == "completed"

    content = revision.content
    # normalization 元数据保留：actions/raw_hash/repaired_hash/warnings。
    trail = content["normalization"]
    assert trail["normalization_actions"] != []
    kinds = {action["action"] for action in trail["normalization_actions"]}
    assert {"alias", "container_shape"} <= kinds
    assert trail["warnings"] == ["declared repairs applied"]
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert canonical_content_hash(_strip_trail(content)) == trail["repaired_hash"]
    # 血缘绑定：envelope 字段与 run 行一致。
    assert content["owner_id"] == ctx["owner_id"]
    assert content["novel_id"] == ctx["novel_id"]
    assert content["skill_version_id"] == svid
    assert content["input_hash"] == ctx["input_hash"]
    assert content["evidence_refs"] == [evidence_key_for(ctx["chapter1_id"])]
    # ToolRun 血缘：candidates.tool_runs 记录 allowlist 工具调用。
    assert content["candidates"]["tool_runs"] == [
        {"tool_name": "get_events", "calls": 1},
        {"tool_name": "get_evidence_span", "calls": 1},
    ]
    # claim 的 authority label 原样保留（D-01），未被静默升级。
    assert content["candidates"]["claims"][0]["authority"] == "probable_inference"
    # official 信封未携带受保护合成字段。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content


async def test_phase27_gate_publishes_agent_cannot_publish_canon(
    runtime_factory, migrated_postgres: str
):
    """确定性权威：WorldModelGate 单独发布 typed projections；Agent 无法直接发布
    canon_fact（无审批 → AUTHORITY_UPGRADE，零持久化行）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"pub_{uuid.uuid4().hex[:6]}"
    )

    # Agent 提案一个 probable_inference 事件 claim（来自 Artifact 信封）。
    proposal = EventClaim(
        claim_kind="event",
        event_key="e-arrival",
        title="林安抵达临安城",
        description="林安在第一章抵达临安城。",
        authority=Authority.PROBABLE_INFERENCE,
        confidence=0.9,
        effective={"start": 1, "end": 1},
        disclosure_cutoff=1,
        source_refs=(
            {
                "evidence_id": evidence_key_for(1),
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": SPAN_START,
                "source_end": SPAN_END,
                "content_hash": SPAN_HASH,
                "source_snapshot_hash": "a" * 64,
            },
        ),
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        version_id=1,
    )

    gate = WorldModelGate(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        version_id=1,
        source_snapshot_hash="a" * 64,
        disclosure_cutoff=3,
        approvals=frozenset(),  # 无 canon_fact / user_interpretation 审批
    )
    # probable_inference：无需审批 → gate 通过 → 持久化为 typed projection。
    result = gate.validate_event(proposal)
    assert result.fact is not None, result.verdicts
    projection = build_candidate(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        version_id=1,
        events=[result.fact],
        edges=[],
    )
    async with runtime_factory() as session:
        await WorldModelEventRepository(session).append_projection(projection)
        await session.commit()
        replayed = await WorldModelEventRepository(session).replay_projection(
            owner_id=ctx["owner_id"], novel_id=ctx["novel_id"], version_id=1
        )
    assert replayed.events[0].event_key == "e-arrival"
    assert replayed.events[0].authority == Authority.PROBABLE_INFERENCE

    # Agent 尝试直接发布 canon_fact：无显式审批 → gate 拒绝（authority upgrade）。
    canon_proposal = EventClaim(
        claim_kind="event",
        event_key="e-canon-fake",
        title="伪造事实",
        description="Agent 试图直接发布的 canon fact。",
        authority=Authority.CANON_FACT,
        confidence=0.9,
        effective={"start": 2, "end": 2},
        disclosure_cutoff=2,
        source_refs=(
            {
                "evidence_id": evidence_key_for(1),
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": SPAN_START,
                "source_end": SPAN_END,
                "content_hash": SPAN_HASH,
                "source_snapshot_hash": "a" * 64,
            },
        ),
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        version_id=1,
    )
    rejected = gate.validate_event(canon_proposal)
    assert rejected.fact is None
    assert rejected.verdicts[-1].reason_code.value == "authority_upgrade"

    # 被拒的 canon 提案没有产生任何持久化行。
    async with runtime_factory() as session:
        canon_rows = int(
            await session.scalar(
                select(func.count())
                .select_from(WorldModelEvent)
                .where(WorldModelEvent.event_key == "e-canon-fake")
            )
            or 0
        )
    assert canon_rows == 0


async def test_phase27_user_interpretation_approval_boundary(
    runtime_factory, migrated_postgres: str
):
    """审批边界：user interpretation 需要 owner 作用域确认；非 owner 确认被 404-hide；
    finalize 后 Agent 不能绕过审批直接迁移 Artifact 状态。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}")
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
        input_hash=canonical_input_hash({"novel_id": seed["novel_id"]}),
    )
    # 铸造 user interpretation 审批请求（agent-service 触发，owner 以认证用户为权威）。
    async with runtime_factory() as session:
        request = await approval_service.create(
            session,
            owner_id=seed["owner_id"],
            payload=ApprovalRequestCreate(
                run_id=run_id,
                skill_version_id=svid,
                novel_id=seed["novel_id"],
                action="world_model:user_interpretation",
                payload_summary={"claim_key": "e-treaty-reading"},
            ),
        )
        await session.commit()
        request_id = request.id

    # 伪造 owner：显式 payload.owner_id 与认证用户不符 → 拒绝铸造。
    with pytest.raises(approval_service.ApprovalStateError):
        async with runtime_factory() as session:
            await approval_service.create(
                session,
                owner_id=seed["owner_id"],
                payload=ApprovalRequestCreate(
                    run_id=run_id,
                    skill_version_id=svid,
                    novel_id=seed["novel_id"],
                    owner_id=seed["owner_id"] + 999,
                    action="world_model:user_interpretation",
                    payload_summary={},
                ),
            )

    # 非 owner 确认 → None（404-hide，无 403 oracle）。
    async with runtime_factory() as session:
        denied = await approval_service.confirm(
            session,
            request_id=request_id,
            owner_id=seed["owner_id"] + 999,
            mode="once",
        )
        assert denied is None

    # owner 确认 → approved（唯一决策路径）。
    async with runtime_factory() as session:
        approved = await approval_service.confirm(
            session,
            request_id=request_id,
            owner_id=seed["owner_id"],
            mode="once",
        )
        await session.commit()
        assert approved is not None and approved.status == "approved"

    # Agent 无法绕过审批直接迁移 Artifact 状态：没有 artifact 时迁移必然失败。
    async with runtime_factory() as session:
        with pytest.raises(ArtifactStateError):
            await transition_artifact_status(
                session,
                artifact_id=999_999,
                owner_id=seed["owner_id"],
                to_status="published",
            )


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase27_cancellation_no_write(runtime_factory, migrated_postgres: str):
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
        input_hash=canonical_input_hash({"novel_id": seed["novel_id"]}),
        cancel_requested=True,
    )
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=canonical_input_hash({"novel_id": seed["novel_id"]}),
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


async def test_phase27_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(upstream_error)，零官方写入。"""
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_UPSTREAM_ERROR
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_wrong_owner_lineage_blocks(
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_wrong_skill_version_lineage_blocks(
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_schema_drift_blocks(runtime_factory, migrated_postgres: str):
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
    envelope["schema_version"] = "world-model-candidate.v2"
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


async def test_phase27_missing_evidence_heuristic_candidate_blocks(
    runtime_factory, migrated_postgres: str
):
    """world_model_candidate 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_forged_approval_protected_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval/authority 受保护字段）→ blocked，零写入。"""
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
        frozen_manifest={"evidence_refs": [evidence_key_for(1)]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase27_unknown_evidence_ref_blocks(
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
    envelope["evidence_refs"] = [
        "qp:1:0:10:forgednot64hexhash000000000000000000000000000000000000000000000000"
    ]
    envelope["candidates"]["claims"][0]["evidence_refs"] = envelope["evidence_refs"]
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


async def test_phase27_cutoff_server_authority(runtime_factory, migrated_postgres: str):
    """cutoff 服务器权威：阅读进度止于第 1 章；显式 cutoff 超限 → beyond_cutoff。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cut_{uuid.uuid4().hex[:6]}")
    async with runtime_factory() as session:
        novel = await session.get(Novel, seed["novel_id"])
        facade = ToolFacade()
        # 显式 cutoff=9 超过服务端截止点（第 1 章）→ beyond_cutoff（防剧透）。
        with pytest.raises(BeyondCutoffError) as excinfo:
            await facade.execute(
                "get_events",
                db=session,
                novel=novel,
                owner_id=seed["owner_id"],
                params={"novel_id": seed["novel_id"], "version_id": 1, "cutoff": 9},
            )
        assert excinfo.value.code == "beyond_cutoff"


# ────────────────────────── HTTP 端到端（register → accept → finalize） ──────────────────────────


async def test_phase27_http_end_to_end_no_approval_no_publisher(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ stub loop → finalize →
    candidate WorldModelCandidateArtifact + revision；无 ApprovalRequest、无发布路径。"""
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
        json={
            "skill_version_id": svid,
            "input": {
                "question": "请提案第一章的世界模型候选（事件/角色/规则）。",
                "novel_id": seed["novel_id"],
                "cutoff": 1,
            },
        },
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
        chapter_id=seed["chapter1_id"],
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
            "source_versions": {"novel": "v1", "world_model": "v1"},
            "usage": {
                "calls": 2,
                "input_tokens": 200,
                "output_tokens": 90,
                "cost_usd": "0.0004",
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
    assert artifact.type == "world_model_candidate"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
