"""Phase 31-04 集成测试：SkillRun → Tool → Artifact → Validator → Approval 端到端边界证明。

证明 Phase 31 确定性关键场景检测能力经版本化 detect-key-scenes Skill 消费
（REQ-VIS-02 + REQ-AGENT-02/03/04 + D-31-01..D-31-05），SceneCandidateArtifact
是唯一官方 Agent 输出，且 Agent 不能绕过域/证据/审批/发布权威：

正向链：
  register（版本化 manifest：5 工具 allowlist 含 get_visual_bible + 空
  write_permissions + [key_scene:approve] 审批动作）→ accept run
  （owner/novel/branch + input_hash 绑定）→ stub loop 调真实 facade 工具
  （get_visual_bible / get_evidence_span，读取 Visual Bible 候选视图与 leaf
  证据）→ 物化 leaf evidence + 冻结 Frozen Manifest → SceneCandidateArtifact
  信封（candidate-only SceneCandidateSetContract：ordered candidates /
  diversity keys / evidence refs / spoiler cutoff / advisory
  speaker_dialogue_signal）→ 确定性 finalizer（integrity gate + 白名单校验 +
  SceneCandidateSetContract 域校验）→ candidate 产物 + 首个不可变修订 → 服务端
  publisher 经 key-scenes API 持久化候选集 → `key_scene:approve` 用户选择/审查
  （review/freeze，evidence gate fail closed）→ frozen set，Chapter 零变更。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner /
  skill_version / input_hash（lineage 血缘）、schema drift、missing evidence、
  approval bypass（envelope status 非 candidate / scene_candidate_set
  review_state 非 candidate / 受保护字段合成）、unknown evidence ref、
  candidate evidence 不在信封 evidence_refs、heuristic 信号被当作证据
  （D-31-05 隔离）、stale source snapshot 无法物化。FastAPI 与确定性
  validators（score/diversity/density/spoiler）保留 permission / evidence /
  state-transition / publication 权威。
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
from sqlalchemy.orm import Session, undefer
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
from app.schemas.key_scene import (
    HeuristicSignalAvailability,
    KeySceneReviewState,
    SceneCandidateContract,
    SceneCandidateSetContract,
    SceneCoordinates,
    SceneEvidenceRange,
    SalienceReason,
    SpeakerDialogueHeuristicSignal,
    SpeakerOffset,
    recompute_manifest_hash,
    validate_candidate_set_contract,
)
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
    ERROR_CODE_INVALID_STOP_REASON,
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
from app.services.key_scenes.boundaries import (
    ChapterRecord,
    compute_source_snapshot_hash,
)
from app.services.key_scenes.candidates import (
    KEY_SCENE_DETECTOR_ID,
    KEY_SCENE_DETECTOR_VERSION,
    KEY_SCENE_SCHEMA_HASH,
)
from app.services.key_scenes.scoring import DEFAULT_SCENE_POLICY, policy_hash
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CH_ACTION = (
    "Arin drew his sword as the rain fell hard across the courtyard walls. "
    '"We attack at dawn!" he said. Mara drew her sword and charged. '
    "The enemy banners would rise with the sun and there would be no going back! "
    "Torches guttered low across the courtyard as the attack exploded."
)
CH_QUIET = (
    "It was a quiet night on the harbor. Arin wept quietly by the rail and "
    "remembered the grief of the long winter. She watched the moon and thought "
    "of everyone they had lost, in a calm that hurt more than any battle."
)
CH_AMBIGUOUS = (
    "Arin walked into the hall. He sat down. Nothing much happened and no one "
    "spoke as the minutes passed."
)

# Phase 31 编排 allowlist：5 个只读域工具。
DEFAULT_TOOLS = [
    "get_events",
    "get_character_state",
    "get_relationships",
    "get_visual_bible",
    "get_evidence_span",
]

HEX64 = "a" * 64
HEX64_B = "b" * 64

SOURCE_SNAPSHOT_ID = "ss-31"
CUTOFF = 2


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        "read_permissions": ["canon", "world_model", "visual_bible", "key_scene"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "key_scene:write",
            "visual_bible:write",
            "derivative:write",
        ],
        "budget": {
            "max_calls": 60,
            "max_input_tokens": 60_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        "approval_required_for": ["key_scene:approve"],
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "version_key": {"type": "string"},
                "cutoff_chapter": {"type": "integer"},
                "source_snapshot": {"type": "object"},
            },
            "required": ["novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "scene_candidate"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说（阅读进度止于第 2 章）+ 三章正文。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p31_owner_{suffix}",
            email=f"p31_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P31 Novel {suffix}",
            author="Author",
            owner_id=owner.id,
            status="ready",
            reading_progress={},
            chapter_count=3,
            word_count=sum(len(c) for c in (CH_ACTION, CH_QUIET, CH_AMBIGUOUS)),
        )
        session.add(novel)
        session.flush()
        chapters: list[Chapter] = []
        for i, content in enumerate((CH_ACTION, CH_QUIET, CH_AMBIGUOUS), start=1):
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=i,
                title=f"第{i}章",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            chapters.append(chapter)
        session.commit()
        novel.reading_progress = {"chapter_id": chapters[1].id}  # cutoff=2
        session.commit()
        data = {
            "owner_id": owner.id,
            "novel_id": novel.id,
            "chapter1_id": chapters[0].id,
            "chapter2_id": chapters[1].id,
            "chapter3_id": chapters[2].id,
            "owner_token": create_access_token({"sub": str(owner.id)}),
            "contents": [CH_ACTION, CH_QUIET, CH_AMBIGUOUS],
        }
    engine.dispose()
    return data


def _snapshot_hash(seed: dict[str, Any]) -> str:
    """对种子小说的当前章节集做确定性 source snapshot hash（D-31 血缘）。"""
    chapters = [
        ChapterRecord(
            chapter_id=seed["chapter1_id"],
            chapter_number=1,
            content=CH_ACTION,
        ),
        ChapterRecord(
            chapter_id=seed["chapter2_id"],
            chapter_number=2,
            content=CH_QUIET,
        ),
        ChapterRecord(
            chapter_id=seed["chapter3_id"],
            chapter_number=3,
            content=CH_AMBIGUOUS,
        ),
    ]
    return compute_source_snapshot_hash(
        owner_id=seed["owner_id"], novel_id=seed["novel_id"], chapters=chapters
    )


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


# ────────────────────────── 契约构建（hash 可重放） ──────────────────────────


def _evidence_range(
    seed: dict[str, Any],
    *,
    content: str,
    snapshot_hash: str,
    evidence_key: str,
    start: int,
    end: int,
) -> SceneEvidenceRange:
    return SceneEvidenceRange(
        evidence_key=evidence_key,
        source_snapshot_id=SOURCE_SNAPSHOT_ID,
        source_snapshot_hash=snapshot_hash,
        chapter_id=seed["chapter1_id"],
        chapter_number=1,
        source_start=start,
        source_end=end,
        content_hash=_sha256(content[start:end]),
        excerpt=content[start:end][:300],
        cutoff_chapter=CUTOFF,
    )


def _candidate(
    seed: dict[str, Any],
    *,
    content: str,
    snapshot_hash: str,
    candidate_key: str,
    scene_id: str,
    evidence_key: str,
    diversity_key: str,
    heuristic_signal: dict[str, Any] | None = None,
) -> SceneCandidateContract:
    start = 0
    end = min(len(content), 40)
    evidence = _evidence_range(
        seed,
        content=content,
        snapshot_hash=snapshot_hash,
        evidence_key=evidence_key,
        start=start,
        end=end,
    )
    return SceneCandidateContract(
        candidate_key=candidate_key,
        candidate_order=0,
        scene_id=scene_id,
        chapter_id=seed["chapter1_id"],
        chapter_number=1,
        source_start=start,
        source_end=end,
        source_hash=_sha256(content[start:end]),
        coordinates=SceneCoordinates(
            cast=["arin", "mara"],
            place="courtyard",
            time="night",
            pov="arin",
        ),
        spoiler_cutoff=CUTOFF,
        salience_reasons=[
            SalienceReason(reason_code="plot_turn", detail="attack turn", score=0.9)
        ],
        score_total=0.9,
        score_breakdown={"action": 0.8, "character_salience": 0.1},
        diversity_key=diversity_key,
        detector_id=KEY_SCENE_DETECTOR_ID,
        detector_version=KEY_SCENE_DETECTOR_VERSION,
        policy_hash=policy_hash(DEFAULT_SCENE_POLICY),
        evidence_ranges=[evidence],
        heuristic_signal=heuristic_signal,
        review_state="candidate",
    )


def _build_set_contract(
    seed: dict[str, Any],
    *,
    snapshot_hash: str,
    version_key: str = "ks-main",
    heuristic_signal: dict[str, Any] | None = None,
) -> tuple[SceneCandidateSetContract, str]:
    """构造一个通过 validate_candidate_set_contract 的候选集契约（+ evidence key）。"""
    evidence_key = f"ev-{version_key}-0"
    candidate = _candidate(
        seed,
        content=CH_ACTION,
        snapshot_hash=snapshot_hash,
        candidate_key=f"ks-{version_key}-0",
        scene_id="scene-ch1-0-40",
        evidence_key=evidence_key,
        diversity_key="dk-action-1",
        heuristic_signal=heuristic_signal,
    )
    set_contract = SceneCandidateSetContract(
        schema_version="key-scene.v1",
        artifact_kind="key_scene",
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_key=version_key,
        revision_number=1,
        parent_set_id=None,
        source_snapshot_id=SOURCE_SNAPSHOT_ID,
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=CUTOFF,
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
    validate_candidate_set_contract(set_contract)
    return set_contract, evidence_key


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    svid: int,
    input_hash: str,
    snapshot_hash: str,
    set_contract: SceneCandidateSetContract,
    evidence_key: str,
    tool_runs: list[dict[str, Any]],
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 Phase 31 SceneCandidateArtifact 信封。"""
    envelope: dict[str, Any] = {
        "type": "scene_candidate",
        "schema_version": "scene-candidate.v1",
        "owner_id": owner_id if not wrong_owner else owner_id + 999,
        "novel_id": novel_id,
        "branch": None,
        "producing_skill": "detect-key-scenes",
        "producing_skill_version": "1.0.0",
        "skill_version_id": svid if not wrong_version else svid + 999,
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": snapshot_hash,
        },
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": [evidence_key],
        "scene_candidate_set": set_contract.model_dump(mode="json"),
        "tool_runs": tool_runs,
        "status": "candidate",
        "parent_revision": None,
    }
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


# ────────────────────────── runtime helpers ──────────────────────────


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
            budget_snapshot={"max_calls": 60},
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
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions={
            "novel": "v1",
            "source_snapshot_hash": "0" * 64,
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


async def _set_up(
    factory, sync_url: str, *, suffix: str
) -> dict[str, Any]:
    """seed owner/novel + 注册 detect-key-scenes + 构建候选集契约 + 创建 run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="detect-key-scenes", tools=DEFAULT_TOOLS
        ),
    )
    snapshot_hash = _snapshot_hash(seed)
    set_contract, evidence_key = _build_set_contract(
        seed, snapshot_hash=snapshot_hash
    )
    run_input = {
        "novel_id": seed["novel_id"],
        "branch": None,
        "version_key": "ks-main",
        "cutoff_chapter": CUTOFF,
        "source_snapshot": {"snapshot_hash": snapshot_hash},
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
        "snapshot_hash": snapshot_hash,
        "input_hash": input_hash,
        "run_id": run_id,
        "set_contract": set_contract,
        "evidence_key": evidence_key,
        "run_input": run_input,
    }


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase31_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 detect-key-scenes manifest 注册成功：5 工具 allowlist（含
    get_visual_bible）+ 零写权限 + [key_scene:approve] 审批动作。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="detect-key-scenes", tools=DEFAULT_TOOLS
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "detect-key-scenes"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "get_visual_bible" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == ["key_scene:approve"]
        assert version.forbidden_spaces == [
            "canon:original",
            "key_scene:write",
            "visual_bible:write",
            "derivative:write",
        ]
        assert "key_scene" in version.read_permissions
        assert int(version.budget["max_calls"]) == 60


async def test_phase31_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="detect-key-scenes",
        tools=list(DEFAULT_TOOLS) + ["delete_key_scene"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Validator→Approval ──────────────────────────


async def test_phase31_happy_path_scene_candidate_artifact_and_freeze(
    runtime_factory, api_client, migrated_postgres: str
):
    """正向链：真实 facade 工具 → 冻结 manifest → SceneCandidateArtifact 信封 →
    finalize → candidate 产物 + 修订 → 服务端 publisher 经 key-scenes API
    持久化候选集 → `key_scene:approve` 用户选择/审查（review/freeze）→ frozen；
    Chapter 零变更。"""
    client, factory, sync_url = api_client
    ctx = await _set_up(
        runtime_factory, sync_url, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]
    evidence_key = ctx["evidence_key"]

    # stub agent loop：真实调用 Phase 31 门面工具（读取 Visual Bible 候选视图与 leaf 证据）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        vb = await facade.execute(
            "get_visual_bible",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"]},
        )
        assert vb == {"items": [], "total": 0}  # 无 Visual Bible 版本 → 空列表

        span = await facade.execute(
            "get_evidence_span",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "chapter_id": ctx["chapter1_id"],
                "source_start": 0,
                "source_end": len("Arin drew his sword"),
                "content_hash": _sha256(CH_ACTION[0 : len("Arin drew his sword")]),
            },
        )
        assert span["chapter_number"] == 1

    frozen_manifest = {
        "evidence_refs": [evidence_key],
        "manifest_checksum": "m" * 64,
    }
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=svid,
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
        evidence_key=evidence_key,
        tool_runs=[
            {"tool_name": "get_visual_bible", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": 1},
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
    )  # Agent 不能铸造 ApprovalRequest 权威

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
    assert artifact is not None and artifact.status == "candidate"
    assert artifact.type == "scene_candidate"
    assert artifact.schema_version == "scene-candidate.v1"
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
    assert content["evidence_refs"] == [evidence_key]
    # SceneCandidateSetContract 完整保留且 review_state 恒为 candidate。
    ks = content["scene_candidate_set"]
    assert ks["version_key"] == "ks-main"
    assert ks["review_state"] == "candidate"
    assert ks["source_snapshot_hash"] == ctx["snapshot_hash"]
    assert ks["manifest_hash"] == ctx["set_contract"].manifest_hash
    assert ks["candidates"][0]["review_state"] == "candidate"
    # ToolRun 血缘。
    assert content["tool_runs"] == [
        {"tool_name": "get_visual_bible", "calls": 1},
        {"tool_name": "get_evidence_span", "calls": 1},
    ]
    # official 信封未携带受保护合成字段 / 可变 Agent 状态。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content

    # ── 服务端 publisher：key-scenes API 持久化候选集（确定性服务）──
    headers = {"Authorization": f"Bearer {ctx['owner_token']}"}
    generated = await client.post(
        f"/api/novels/{ctx['novel_id']}/key-scenes/generate",
        json={
            "version_key": "ks-publisher",
            "cutoff_chapter": CUTOFF,
            "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        },
        headers=headers,
    )
    assert generated.status_code == 201, generated.text
    set_view = generated.json()["set"]
    set_id = set_view["id"]
    candidate_key = set_view["candidates"][0]["candidate_key"]

    # ── `key_scene:approve`：用户选择/审查（review 端点，evidence gate 服务端裁决）──
    approved = await client.post(
        f"/api/novels/{ctx['novel_id']}/key-scenes/{set_id}/review",
        json={
            "decision_key": f"ds-approve-{uuid.uuid4().hex[:16]}",
            "action": "approve",
            "actor_source": "human",
            "actor": "owner",
            "reason": "user reviewed and confirmed the scene candidate",
            "from_review_state": "candidate",
            "candidate_key": candidate_key,
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["set"]["candidates"][0]["review_state"] == "approved"

    # ── freeze：显式 set 级批准 → frozen key-scene set（D-31-04）──
    frozen = await client.post(
        f"/api/novels/{ctx['novel_id']}/key-scenes/{set_id}/freeze",
        json={
            "actor_source": "human",
            "actor": "owner",
            "reason": "user froze the key-scene set",
        },
        headers=headers,
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["frozen"]["review_state"] == "approved"

    # Original Canon 零变更（D-31-01）：chapter 内容未被触碰。
    async with runtime_factory() as session:
        chapter_row = await session.scalar(
            select(Chapter).options(undefer(Chapter.content)).where(
                Chapter.id == ctx["chapter1_id"]
            )
        )
    assert chapter_row is not None and chapter_row.content == CH_ACTION


async def test_phase31_get_visual_bible_scope_and_approved_only(
    runtime_factory, api_client, migrated_postgres: str
):
    """get_visual_bible 门面：owner/novel 范围 + approved_only 过滤在服务端强制。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"vb_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        payload = await facade.execute(
            "get_visual_bible",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "approved_only": True,
            },
        )
    assert payload == {"items": [], "total": 0}
    # 未知版本 id → 404-hide（not_found 冻结码）。
    from app.services.agent_tools.errors import NotFoundError

    with pytest.raises(NotFoundError):
        async with runtime_factory() as session:
            novel = await session.get(Novel, ctx["novel_id"])
            facade = ToolFacade()
            await facade.execute(
                "get_visual_bible",
                db=session,
                novel=novel,
                owner_id=ctx["owner_id"],
                params={"novel_id": ctx["novel_id"], "version_id": 999},
            )


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase31_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}"
    )
    run_id = await _create_run(
        runtime_factory,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        skill_version_id=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        input_data=ctx["run_input"],
        cancel_requested=True,
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": [ctx["evidence_key"]]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase31_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(upstream_error)，零官方写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"to_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_wrong_owner_lineage_blocks(runtime_factory, migrated_postgres: str):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ver_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"schema_version": "scene-candidate.v2"},
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


async def test_phase31_missing_evidence_blocks(runtime_factory, migrated_postgres: str):
    """SceneCandidateArtifact 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_forged_approval_protected_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval_state/authority 受保护字段）→ blocked。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"syn_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_approval_bypass_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：envelope status 非 candidate（如 approved）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
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


async def test_phase31_approval_bypass_review_state_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：scene_candidate_set.review_state 非 candidate（如 approved）→ blocked。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"rev_{uuid.uuid4().hex[:6]}"
    )
    forged = ctx["set_contract"].model_copy(
        update={"review_state": KeySceneReviewState.APPROVED}
    )  # Agent 声称已批准
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=forged,
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
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
        and "approval bypass" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase31_candidate_evidence_mismatch_blocks(
    runtime_factory, migrated_postgres: str
):
    """candidate evidence key 不在信封 evidence_refs → integrity blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ev_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    # 信封 evidence_refs 不覆盖候选的 evidence key → integrity blocked
    # （D-31-02 leaf-evidence 资格门）。
    envelope["evidence_refs"] = ["0" * 64]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [ctx["evidence_key"], "0" * 64]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "evidence" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase31_unknown_evidence_ref_blocks(
    runtime_factory, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ref_{uuid.uuid4().hex[:6]}"
    )
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=ctx["set_contract"],
        evidence_key=ctx["evidence_key"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    # 白名单只含真实候选证据；信封额外声明一个不在 manifest 的未知 ref。
    unknown = "qp:1:0:10:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    envelope["evidence_refs"] = [ctx["evidence_key"], unknown]
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


async def test_phase31_heuristic_signal_isolation_enforced(
    runtime_factory, migrated_postgres: str
):
    """D-31-05 隔离：speaker/dialogue heuristic 信号绝不成为证据——候选证据必须
    真实物化；heuristic 偏移越界（signal 被视为证据）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"sig_{uuid.uuid4().hex[:6]}"
    )
    # 构造一个把 heuristic 偏移当作证据的非法候选：heuristic offset 越出 primary
    # evidence range（validate_candidate_set_contract 的隔离门必须拦截）。
    evidence_key = ctx["evidence_key"]
    candidate = ctx["set_contract"].candidates[0]
    forged_candidate = candidate.model_copy(
        update={
            "heuristic_signal": SpeakerDialogueHeuristicSignal(
                availability=HeuristicSignalAvailability.AVAILABLE,
                speaker_offsets=[
                    SpeakerOffset(
                        offset_start=100, offset_end=110, speaker_key="arin"
                    )
                ],
                dialogue_offsets=[],
                confidence=0.9,
                warnings=[],
                detector_id=KEY_SCENE_DETECTOR_ID,
                detector_version=KEY_SCENE_DETECTOR_VERSION,
            )
        }
    )
    forged_set = ctx["set_contract"].model_copy(
        update={
            "candidates": [forged_candidate],
            "manifest_hash": "0" * 64,
        }
    )
    forged_set = forged_set.model_copy(
        update={"manifest_hash": recompute_manifest_hash(forged_set)}
    )
    # 隔离门在 build 阶段即应失败（fail closed）。
    with pytest.raises(Exception):
        validate_candidate_set_contract(forged_set)

    # 信封走 finalize：heuristic offset 越界候选无法通过域校验 → blocked，零写入。
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        set_contract=forged_set,
        evidence_key=evidence_key,
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase31_http_end_to_end_candidate_artifact_no_agent_approval(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ finalize →
    candidate SceneCandidateArtifact + revision；Agent 不能铸造/授予 ApprovalRequest，
    review_state 只能由用户经 review/freeze API 迁移。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(
            novel_id=seed["novel_id"], name="detect-key-scenes", tools=DEFAULT_TOOLS
        ).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    snapshot_hash = _snapshot_hash(seed)
    set_contract, evidence_key = _build_set_contract(
        seed, snapshot_hash=snapshot_hash
    )
    run_input = {
        "novel_id": seed["novel_id"],
        "question": "请为这本小说检测关键场景候选。",
        "branch": None,
        "version_key": "ks-http",
        "cutoff_chapter": CUTOFF,
        "source_snapshot": {"snapshot_hash": snapshot_hash},
    }
    accepted = await client.post(
        f"/api/agent/novels/{seed['novel_id']}/skill-runs",
        json={"skill_version_id": svid, "input": run_input},
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
        svid=svid,
        input_hash=run_hash,
        snapshot_hash=snapshot_hash,
        set_contract=set_contract,
        evidence_key=evidence_key,
        tool_runs=[
            {"tool_name": "get_visual_bible", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": 1},
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
            "source_versions": {"novel": "v1", "source_snapshot_hash": snapshot_hash},
            "usage": {
                "calls": 3,
                "input_tokens": 300,
                "output_tokens": 120,
                "cost_usd": "0.0006",
            },
            "frozen_manifest": {"evidence_refs": [evidence_key]},
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
    assert artifact.type == "scene_candidate"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
