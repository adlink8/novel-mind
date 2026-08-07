"""Phase 29-05 集成测试：SkillRun → Tool → Artifact → Validator 端到端边界证明。

证明 Phase 29 确定性阅读 QA 评估能力经版本化 evaluate-reading-skill-runs Skill
消费（REQ-QA-01..03 + REQ-AGENT-03 + D-02..D-05），SkillEvaluationArtifact 是
唯一官方 Agent 输出，且全程**无 ApprovalRequest、无 Publisher、无 promotion、
无 Canon 写入**：

正向链：
  register（版本化 manifest：4 工具 allowlist + 空 write_permissions +
  空 approval_required_for）→ accept run（owner/novel/branch + input_hash 绑定）
  → stub loop 调真实 facade 工具（get_evidence_span / get_narrative_memory，
  读取冻结数据集/证据记录）→ 物化 leaf evidence + 冻结 Frozen Manifest →
  密封 QualificationReport（verdict 只允许 qualified_candidate / blocked，
  checksum 可重放）→ 携带共享 26-06 normalization trail 的
  SkillEvaluationArtifact 信封 → 确定性 finalizer（integrity gate + 白名单校验）
  → candidate 产物 + 首个不可变修订。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner / skill_version /
  input_hash（lineage 血缘）、schema drift、missing evidence（heuristic candidate）、
  受保护字段合成（approval_state/authority）、unknown evidence ref、
  report checksum 重放失败、verdict promotion 尝试（D-05）、approval bypass
  （status 非 candidate）。blocked 报告是合法终态（blocked 也是唯一官方裁决之一）。
"""

from __future__ import annotations

import hashlib
import uuid
from copy import deepcopy
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
from app.schemas.agent_runtime import SkillVersionRegister
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
from app.services.agent_tools.facade import ToolFacade
from app.services.qualification.gold_set import load_gold_set
from app.services.qualification.runner import run_qualification
from app.services.queryplan.contracts import leaf_evidence_key
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
CHAPTER2_CONTENT = "第二章正文：阿宁在竹林深处看见了使者的身影，脚步匆匆。"

# Phase 29 编排 allowlist：4 个只读冻结数据集/证据记录读取工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_evidence_span",
    "get_narrative_memory",
    "search_novel_text",
]
# 被评估候选 run 的产出具（26 answer-reading-question 工具集子集）。
CANDIDATE_TOOLS = [
    "get_chapter",
    "search_novel_text",
    "get_evidence_span",
    "get_timeline",
    "get_relationships",
    "get_clues",
]

# 密封 QualificationReport 血缘绑定（D-02）。
GOLD_PATH = Path(__file__).resolve().parents[3] / "evals" / "reading_qa_v1.json"
GOLD_SET = load_gold_set(GOLD_PATH)
SOURCE_SNAPSHOT_HASH = GOLD_SET.source_snapshot_hash
DATASET_VERSION = GOLD_SET.dataset_version
COMMIT = "912ca6b423d6c2309bc2972cbfc083c4eaa280e1"

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


def _skill_contract(
    *, novel_id: int, name: str, tools: list[str], **overrides: Any
) -> SkillVersionRegister:
    base: dict[str, Any] = {
        "novel_id": novel_id,
        "name": name,
        "version": "1.0.0",
        "allowed_tools": list(tools),
        "read_permissions": ["canon", "narrative_memory", "qualification"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "qualification:write",
            "derivative:write",
        ],
        "budget": {
            "max_calls": 80,
            "max_input_tokens": 60_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "5.00",
        },
        "approval_required_for": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "dataset": {"type": "object"},
                "evaluated_run": {"type": "object"},
            },
            "required": ["novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "skill_evaluation"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说（阅读进度止于第 1 章）+ 两章正文。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p29_owner_{suffix}",
            email=f"p29_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P29 Novel {suffix}",
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


def _common_fields() -> dict:
    return {
        "faithfulness": 1.0,
        "relevance": 1.0,
        "latency_ms": 12.0,
        "calls": 2,
        "input_tokens": 60,
        "output_tokens": 40,
        "cost_usd": 0.002,
        "fallback_used": False,
        "provider_error": None,
    }


def _clean_artifacts() -> dict[str, dict]:
    """干净 candidate/baseline artifact（与 golden source answers 一致）。"""
    out: dict[str, dict] = {}
    for sample in GOLD_SET.samples:
        if sample.expected_answerability == "answerable":
            sa = sample.source_answers[0]
            out[sample.id] = {
                "answer": sa.answer,
                "cited_evidence": [r.model_dump(mode="json") for r in sa.evidence],
                "retrieved_leaf_ids": [r.evidence_key() for r in sa.evidence],
                "abstained": False,
            }
        else:
            out[sample.id] = {
                "answer": "",
                "cited_evidence": [],
                "retrieved_leaf_ids": [],
                "abstained": True,
            }
        out[sample.id].update(_common_fields())
    return out


def _qualification_report(*, blocked: bool = False) -> dict[str, Any]:
    """用确定性 runner 产出密封 QualificationReport（D-02..D-05）。"""
    header = {
        "db_fingerprint": "db-fp-phase29-001",
        "dataset_version": DATASET_VERSION,
        "source_snapshot": SOURCE_SNAPSHOT_HASH,
        "commit": COMMIT,
        "model": "queryplan-nm-candidate.v1",
        "prompt": "prompt-hash-001",
        "schema_version": "reading-qa-canon.v1",
        "config": "config-hash-001",
        "budget": {
            "max_calls": 100,
            "max_input_tokens": 50_000,
            "max_output_tokens": 20_000,
            "max_cost_usd": "5.00",
        },
    }
    cand = _clean_artifacts()
    base = deepcopy(cand)
    if blocked:
        # provider unavailable → verdict blocked（blocked 是唯一官方裁决之一）。
        cand["local_01"]["provider_error"] = "provider_timeout"
    report = run_qualification(
        gold_set=GOLD_SET,
        header=header,
        candidate_artifacts=cand,
        baseline_artifacts=base,
    )
    assert report.verdict in ("qualified_candidate", "blocked")
    assert report.checksum_valid
    return report.model_dump(mode="json")


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    svid: int,
    input_hash: str,
    evaluated: dict[str, Any],
    report: dict[str, Any],
    evidence_key: str,
    tool_runs: list[dict[str, Any]],
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    extra: dict[str, Any] | None = None,
    report_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 Phase 29 SkillEvaluationArtifact 信封。"""
    lineage = {
        "owner_id": owner_id if not wrong_owner else owner_id + 999,
        "novel_id": novel_id,
        "branch": None,
        "skill_version_id": svid if not wrong_version else svid + 999,
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "dataset_version": DATASET_VERSION,
            "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
        },
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": [evidence_key],
        "parent_revision": None,
    }
    envelope: dict[str, Any] = {
        "type": "skill_evaluation",
        "schema_version": "skill-evaluation.v1",
        "producing_skill": "evaluate-reading-skill-runs",
        "producing_skill_version": "1.0.0",
        "evaluated_run": {
            "run_id": evaluated["run_id"],
            "status": "completed",
            "branch": None,
            "input_hash": evaluated["input_hash"],
            "tool_runs": [
                {"tool_name": "get_chapter", "calls": 1},
                {"tool_name": "get_evidence_span", "calls": 1},
            ],
        },
        "evaluated_artifact": {
            "artifact_id": evaluated["artifact_id"],
            "revision_id": evaluated["revision_id"],
            "type": "cited_answer",
            "schema_version": "cited-answer.v1",
            "content_hash": evaluated["content_hash"],
            "status": "candidate",
        },
        "report": report_override if report_override is not None else report,
        "tool_runs": tool_runs,
        "status": "candidate",
    }
    envelope.update(lineage)
    if extra:
        envelope.update(extra)

    # 26-06 trail：repaired_hash 是对不含 trail 的 payload 的 canonical SHA-256。
    repaired_hash = canonical_content_hash(_strip_trail(envelope))
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


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
    input_data: dict[str, Any],
    cancel_requested: bool = False,
) -> int:
    async with factory() as session:
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=skill_version_id,
            status="running",
            branch=None,
            input=input_data,
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 80},
            cancel_requested=cancel_requested,
        )
        session.add(run)
        await session.commit()
        return run.id


async def _seed_evaluated_records(
    factory,
    *,
    owner_id: int,
    novel_id: int,
    candidate_svid: int,
    evidence_key: str,
    content_hash: str,
) -> dict[str, Any]:
    """直接创建被评估的**冻结** SkillRun + Artifact + Revision（不可变记录）。"""
    async with factory() as session:
        eval_input = {"novel_id": novel_id, "question": "被评估的冻结候选运行"}
        eval_input_hash = canonical_input_hash(eval_input)
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=candidate_svid,
            status="completed",
            branch=None,
            input=eval_input,
            input_hash=eval_input_hash,
            frozen_manifest={"evidence_refs": [evidence_key]},
            budget_snapshot={"max_calls": 40},
            model_lineage={
                "provider": "fixture",
                "model": "stub-model",
                "revision": "stub-1",
            },
            source_versions={"novel": "v1"},
            cancel_requested=False,
            retry_count=0,
        )
        session.add(run)
        await session.flush()
        artifact = Artifact(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=candidate_svid,
            run_id=run.id,
            branch=None,
            type="cited_answer",
            schema_version="cited-answer.v1",
            status="candidate",
            model_lineage={
                "provider": "fixture",
                "model": "stub-model",
                "revision": "stub-1",
            },
            source_versions={"novel": "v1"},
            input_hash=eval_input_hash,
        )
        session.add(artifact)
        await session.flush()
        revision = ArtifactRevision(
            artifact_id=artifact.id,
            owner_id=owner_id,
            novel_id=novel_id,
            revision_no=1,
            content_hash=content_hash,
            parent_revision_id=None,
            evidence_refs=[evidence_key],
            content={
                "type": "cited_answer",
                "schema_version": "cited-answer.v1",
                "evidence_refs": [evidence_key],
            },
        )
        session.add(revision)
        await session.flush()
        artifact.current_revision_id = revision.id
        await session.commit()
        return {
            "run_id": run.id,
            "artifact_id": artifact.id,
            "revision_id": revision.id,
            "content_hash": content_hash,
            "input_hash": eval_input_hash,
        }


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
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions={
            "novel": "v1",
            "dataset_version": DATASET_VERSION,
            "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
        },
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


async def _set_up(factory, sync_url: str, *, suffix: str) -> dict[str, Any]:
    """seed owner/novel + 注册 Phase 29 评估技能 + 冻结被评估记录 + 创建评估 run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="evaluate-reading-skill-runs",
            tools=DEFAULT_TOOLS,
        ),
    )
    candidate_svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="answer-reading-question",
            tools=CANDIDATE_TOOLS,
        ),
    )
    evidence_key = evidence_key_for(seed["chapter1_id"])
    content_hash = canonical_content_hash(
        {"type": "cited_answer", "evidence_refs": [evidence_key]}
    )
    evaluated = await _seed_evaluated_records(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        candidate_svid=candidate_svid,
        evidence_key=evidence_key,
        content_hash=content_hash,
    )
    run_input = {
        "novel_id": seed["novel_id"],
        "branch": None,
        "dataset": {
            "dataset_version": DATASET_VERSION,
            "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
            "dataset_lineage": "reading-qa-gold.v1",
        },
        "evaluated_run": {
            "run_id": evaluated["run_id"],
            "artifact_id": evaluated["artifact_id"],
            "revision_id": evaluated["revision_id"],
            "content_hash": evaluated["content_hash"],
        },
        "evaluation": {"top_k": 8},
    }
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
        input_data=run_input,
    )
    return {
        **seed,
        "skill_version_id": svid,
        "candidate_svid": candidate_svid,
        "input_hash": input_hash,
        "run_id": run_id,
        "evidence_key": evidence_key,
        "evaluated": evaluated,
        "run_input": run_input,
    }


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase29_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 evaluate-reading-skill-runs manifest 注册成功：4 工具 allowlist +
    零写权限 + 零审批动作 + qualification 只读。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="evaluate-reading-skill-runs",
            tools=DEFAULT_TOOLS,
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "evaluate-reading-skill-runs"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert version.write_permissions == []
        assert version.approval_required_for == []
        assert version.forbidden_spaces == [
            "canon:original",
            "qualification:write",
            "derivative:write",
        ]
        assert "qualification" in version.read_permissions
        assert int(version.budget["max_calls"]) == 80


async def test_phase29_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="evaluate-reading-skill-runs",
        tools=list(DEFAULT_TOOLS) + ["delete_qualification"],
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


async def test_phase29_happy_path_skill_evaluation_artifact(
    runtime_factory, migrated_postgres: str
):
    """正向链：真实 facade 工具 → 冻结 manifest → SkillEvaluationArtifact 信封
    （密封 qualified_candidate 报告）→ finalize → candidate 产物 + 修订；
    无审批/发布/Canon。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]
    evidence_key = ctx["evidence_key"]

    # stub agent loop：真实调用 Phase 29 门面工具（读取冻结数据集/证据记录）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
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
        assert span["evidence_key"] == evidence_key

        nm = await facade.execute(
            "get_narrative_memory",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"view": "versions"},
        )
        assert nm["release_status"] == "candidate"  # ADR-0002：候选发布

    frozen_manifest = {
        "evidence_refs": [evidence_key],
        "manifest_checksum": "m" * 64,
    }
    report = _qualification_report()
    assert report["verdict"] == "qualified_candidate"

    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=svid,
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=evidence_key,
        tool_runs=[
            {"tool_name": "get_evidence_span", "calls": 1},
            {"tool_name": "get_narrative_memory", "calls": 1},
        ],
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
    assert artifact.type == "skill_evaluation"
    assert artifact.schema_version == "skill-evaluation.v1"
    assert revision is not None and revision.revision_no == 1
    assert revision.parent_revision_id is None
    assert run_row is not None and run_row.status == "completed"

    content = revision.content
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    # 血缘绑定。
    assert content["owner_id"] == ctx["owner_id"]
    assert content["novel_id"] == ctx["novel_id"]
    assert content["skill_version_id"] == svid
    assert content["input_hash"] == ctx["input_hash"]
    assert content["evidence_refs"] == [evidence_key]
    # 被评估冻结血缘（SkillRun + ToolRun + Artifact revision）。
    assert content["evaluated_run"]["run_id"] == ctx["evaluated"]["run_id"]
    assert content["evaluated_run"]["status"] == "completed"
    assert (
        content["evaluated_artifact"]["artifact_id"] == ctx["evaluated"]["artifact_id"]
    )
    assert (
        content["evaluated_artifact"]["revision_id"] == ctx["evaluated"]["revision_id"]
    )
    assert (
        content["evaluated_artifact"]["content_hash"]
        == ctx["evaluated"]["content_hash"]
    )
    # 密封报告：two-value verdict + checksum 可重放。
    assert content["report"]["verdict"] == "qualified_candidate"
    assert content["report"]["checksum"] == report["checksum"]
    # D-05：report 只允许 qualified_candidate/blocked 词表。
    for banned in ("promote", "promotion", "active_pointer", "production_ready"):
        assert banned not in content["report"]
    # ToolRun 血缘。
    assert content["tool_runs"] == [
        {"tool_name": "get_evidence_span", "calls": 1},
        {"tool_name": "get_narrative_memory", "calls": 1},
    ]
    # official 信封未携带受保护合成字段 / 可变 Agent 状态。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content


async def test_phase29_blocked_report_is_official_outcome(
    runtime_factory, migrated_postgres: str
):
    """blocked 是唯一官方裁决之一（D-05）：blocked 报告同样 finalize 为
    candidate SkillEvaluationArtifact（verdict=blocked），零审批/发布。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"blk_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = ctx["evidence_key"]
    report = _qualification_report(blocked=True)
    assert report["verdict"] == "blocked"
    assert report["blocked_reasons"]

    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=evidence_key,
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "completed", outcome.status_reason
    assert await _count(runtime_factory, Artifact, run_id=ctx["run_id"]) == 1
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0
    async with runtime_factory() as session:
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    assert revision is not None
    assert revision.content["report"]["verdict"] == "blocked"
    assert revision.content["report"]["blocked_reasons"]
    assert revision.content["status"] == "candidate"


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase29_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="evaluate-reading-skill-runs",
            tools=DEFAULT_TOOLS,
        ),
    )
    run_input = {
        "novel_id": seed["novel_id"],
        "dataset": {
            "dataset_version": DATASET_VERSION,
            "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
        },
        "evaluated_run": {
            "run_id": 1,
            "artifact_id": 1,
            "revision_id": 1,
            "content_hash": "b" * 64,
        },
    }
    run_id = await _create_run(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=canonical_input_hash(run_input),
        input_data=run_input,
        cancel_requested=True,
    )
    report = _qualification_report()
    evidence_key = evidence_key_for(1)
    evaluated = {
        "run_id": 1,
        "artifact_id": 1,
        "revision_id": 1,
        "content_hash": "b" * 64,
        "input_hash": "e" * 64,
    }
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        svid=svid,
        input_hash=canonical_input_hash(run_input),
        evaluated=evaluated,
        report=report,
        evidence_key=evidence_key,
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase29_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(upstream_error)，零官方写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"to_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        stop_reason="max_tokens",
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_UPSTREAM_ERROR
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_owner=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ver_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_version=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_input_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"schema_version": "skill-evaluation.v2"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "schema" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_missing_evidence_heuristic_candidate_blocks(
    runtime_factory, migrated_postgres: str
):
    """SkillEvaluationArtifact 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    envelope["evidence_refs"] = []
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_forged_approval_protected_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval_state/authority 受保护字段）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"syn_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"approval_state": "approved", "authority": "model-claimed"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_unknown_evidence_ref_blocks(
    runtime_factory, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ref_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    envelope["evidence_refs"] = [
        "qp:1:0:10:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    ]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "unknown evidence ref" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_report_checksum_tamper_blocks(
    runtime_factory, migrated_postgres: str
):
    """report checksum 重放失败（verdict 被篡改而未重算 checksum）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ck_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    tampered = {**report, "verdict": "blocked", "blocked_reasons": ["tampered"]}
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        report_override=tampered,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "checksum" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_report_verdict_promotion_blocks(
    runtime_factory, migrated_postgres: str
):
    """verdict promotion 尝试（verdict 非 two-value）→ blocked，零写入（D-05）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"pro_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    promoted = {**report, "verdict": "promoted"}
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        report_override=promoted,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None and "qualification" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_approval_bypass_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：envelope status 非 candidate（如 approved）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"status": "approved"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_mutable_evaluated_run_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """被评估 run 不是冻结终态（status=running）→ blocked，零写入（绝不重跑可变状态）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"mut_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    envelope["evaluated_run"]["status"] = "running"  # 可变状态绝不作为评估证据
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase29_wrong_source_snapshot_blocks(
    runtime_factory, migrated_postgres: str
):
    """report header source snapshot 与信封 source_versions 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"snap_{uuid.uuid4().hex[:6]}"
    )
    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        evaluated=ctx["evaluated"],
        report=report,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    envelope["source_versions"]["source_snapshot_hash"] = "0" * 64  # ≠ report header
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "snapshot" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


# ────────────────────────── HTTP 端到端（register → accept → finalize） ──────────────────────────


async def test_phase29_http_end_to_end_no_approval_no_publisher(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ stub loop → finalize →
    candidate SkillEvaluationArtifact + revision；无 ApprovalRequest、无发布路径。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(
            novel_id=seed["novel_id"],
            name="evaluate-reading-skill-runs",
            tools=DEFAULT_TOOLS,
        ).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    candidate_svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"],
            name="answer-reading-question",
            tools=CANDIDATE_TOOLS,
        ),
    )
    evidence_key = evidence_key_for(seed["chapter1_id"])
    content_hash = canonical_content_hash(
        {"type": "cited_answer", "evidence_refs": [evidence_key]}
    )
    evaluated = await _seed_evaluated_records(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        candidate_svid=candidate_svid,
        evidence_key=evidence_key,
        content_hash=content_hash,
    )

    accepted = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs",
        json={
            "skill_version_id": svid,
            "input": {
                "novel_id": seed["novel_id"],
                "question": "请评估该候选 SkillRun 的阅读 QA 质量。",
                "branch": None,
                "dataset": {
                    "dataset_version": DATASET_VERSION,
                    "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
                },
                "evaluated_run": {
                    "run_id": evaluated["run_id"],
                    "artifact_id": evaluated["artifact_id"],
                    "revision_id": evaluated["revision_id"],
                    "content_hash": evaluated["content_hash"],
                },
                "evaluation": {"top_k": 8},
            },
        },
        headers=headers,
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["internal_token"], "202 must mint a per-run internal token"
    run_id = body["run"]["id"]
    run_hash = body["run"]["input_hash"]

    report = _qualification_report()
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        svid=svid,
        input_hash=run_hash,
        evaluated=evaluated,
        report=report,
        evidence_key=evidence_key,
        tool_runs=[
            {"tool_name": "get_evidence_span", "calls": 1},
            {"tool_name": "get_narrative_memory", "calls": 1},
        ],
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
            "source_versions": {
                "novel": "v1",
                "dataset_version": DATASET_VERSION,
                "source_snapshot_hash": SOURCE_SNAPSHOT_HASH,
            },
            "usage": {
                "calls": 3,
                "input_tokens": 300,
                "output_tokens": 120,
                "cost_usd": "0.0006",
            },
            "frozen_manifest": {
                "evidence_refs": [evidence_key],
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
    assert artifact.type == "skill_evaluation"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
