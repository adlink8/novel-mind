"""Phase 30-05 集成测试：SkillRun → Tool → Artifact → Validator → Approval 端到端边界证明。

证明 Phase 30 确定性 Visual Bible 域能力经版本化 build-visual-bible Skill 消费
（REQ-VIS-01 + REQ-AGENT-02/03/04 + D-30-01..D-30-04），VisualBibleArtifact 是
唯一官方 Agent 输出，且 Agent 不能绕过域/证据/审批/发布权威：

正向链：
  register（版本化 manifest：5 工具 allowlist + 空 write_permissions +
  [visual_bible:approve] 审批动作）→ accept run（owner/novel/branch +
  input_hash 绑定）→ stub loop 调真实 facade 工具（get_novel / get_chapter /
  get_evidence_span，读取 Canon 正文与 leaf 证据）→ 物化 leaf evidence + 冻结
  Frozen Manifest → VisualBibleArtifact 信封（candidate-only
  VisualBibleVersionContract：entities / claims / evidence refs /
  reference assets + 全量 lineage hash）→ 确定性 finalizer（integrity gate +
  白名单校验）→ candidate 产物 + 首个不可变修订 → 服务端 publisher 经
  visual-bible API 物化证据并持久化 candidate 版本 → `visual_bible:approve`
  用户批准（evidence/rights gate fail closed）→ review_state=approved
  （accepted visual authority），Chapter / cover_url 零变更。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、非 stop reason（timeout 语义）、wrong owner /
  skill_version / input_hash（lineage 血缘）、schema drift、missing evidence、
  approval bypass（envelope status 非 candidate / review_state 非 candidate /
  受保护字段合成）、unknown evidence ref、claim evidence 不在信封 evidence_refs、
  stale source snapshot 无法物化（publisher 409）、rights 未清除的批准尝试
  （approval gate rights_unresolved）。FastAPI 与确定性 validators 保留
  permission/evidence/state-transition/publication 权威。
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
from app.models.visual_bible import VisualBibleVersion
from app.schemas.agent_runtime import SkillVersionRegister
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    VisualClaimContract,
    VisualReviewState,
    claim_content_hash,
    recompute_manifest_hash,
)
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
from app.services.agent_tools.facade import ToolFacade
from app.services.queryplan.contracts import leaf_evidence_key
from app.services.visual_bible.evidence import (
    ChapterRecord,
    compute_source_snapshot_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_CONTENT = "第一章正文：阿宁走进竹林，月光洒在青石上，看见了使者的身影。"
CHAPTER2_CONTENT = "第二章正文：阿宁在竹林深处看见了使者的身影，脚步匆匆。"

# Phase 30 编排 allowlist：5 个只读域工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "get_evidence_span",
    "get_character_state",
    "get_world_rules",
]

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

SOURCE_SNAPSHOT_ID = "ss-30"
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
        "read_permissions": ["canon", "visual_bible", "reference_assets"],
        "write_permissions": [],
        "forbidden_spaces": ["canon:original", "visual_bible:write", "derivative:write"],
        "budget": {
            "max_calls": 60,
            "max_input_tokens": 60_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        "approval_required_for": ["visual_bible:approve"],
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "cutoff": {"type": "integer"},
                "source_snapshot": {"type": "object"},
                "reference_assets": {"type": "array"},
            },
            "required": ["novel_id"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "visual_bible"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说（阅读进度止于第 1 章）+ 两章正文。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        owner = User(
            username=f"p30_owner_{suffix}",
            email=f"p30_owner_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(owner)
        session.flush()
        novel = Novel(
            title=f"P30 Novel {suffix}",
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
            "contents": [CHAPTER_CONTENT, CHAPTER2_CONTENT],
        }
    engine.dispose()
    return data


def _snapshot_hash(seed: dict[str, Any]) -> str:
    """对种子小说的当前章节集做确定性 source snapshot hash（D-30 血缘）。"""
    chapters = [
        ChapterRecord(
            chapter_id=seed["chapter1_id"],
            chapter_number=1,
            content=CHAPTER_CONTENT,
        ),
        ChapterRecord(
            chapter_id=seed["chapter2_id"],
            chapter_number=2,
            content=CHAPTER2_CONTENT,
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


def _evidence_ref(
    *,
    content: str,
    find_text: str,
    evidence_key: str,
    chapter_id: int,
    chapter_number: int,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> dict[str, Any]:
    start = content.find(find_text)
    assert start >= 0, f"{find_text!r} not found in chapter"
    end = start + len(find_text)
    return {
        "evidence_key": evidence_key,
        "source_snapshot_id": source_snapshot_id,
        "source_snapshot_hash": source_snapshot_hash,
        "chapter_id": chapter_id,
        "chapter_number": chapter_number,
        "source_start": start,
        "source_end": end,
        "content_hash": _sha256(content[start:end]),
        "cutoff_chapter": cutoff_chapter,
    }


def _claim_payload(
    *,
    claim_key: str,
    entity_stable_id: str,
    authority: str,
    description: str,
    cutoff_chapter: int,
    evidence: list[dict[str, Any]] | None = None,
    author: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    payload = {
        "claim_key": claim_key,
        "entity_stable_id": entity_stable_id,
        "authority": authority,
        "description": description,
        "author": author,
        "rationale": rationale,
        "cutoff_chapter": cutoff_chapter,
        "claim_hash": "0" * 64,
        "evidence_refs": evidence or [],
    }
    claim = VisualClaimContract.model_validate(payload)
    claim = claim.model_copy(update={"claim_hash": claim_content_hash(claim)})
    return claim.model_dump(mode="json")


def _entity_payload(
    *,
    stable_id: str,
    entity_type: str,
    description: str,
    authority: str,
    disclosure_cutoff: int,
) -> dict[str, Any]:
    return {
        "stable_id": stable_id,
        "entity_key": stable_id,
        "entity_type": entity_type,
        "description": description,
        "authority": authority,
        "disclosure_cutoff": disclosure_cutoff,
    }


def _asset_payload(
    *,
    asset_key: str,
    rights_status: str = "unreviewed",
    asset_id: str = "obj-1",
) -> dict[str, Any]:
    return {
        "asset_key": asset_key,
        "asset_id": asset_id,
        "mime_type": "image/png",
        "bytes_hash": HEX64_B,
        "rights_status": rights_status,
        "provenance": {"source": "user-upload", "license": "pending"},
    }


def _build_version_contract(
    *,
    seed: dict[str, Any],
    snapshot_hash: str,
    assets: list[dict[str, Any]] | None = None,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    """vb-basic-v1：角色/地点 canon claims + probable_inference claim + assets。"""
    ch1 = seed["contents"][0]
    ev_key = leaf_evidence_key(
        chapter_id=seed["chapter1_id"],
        source_start=0,
        source_end=len("阿宁"),
        content_hash=_sha256(ch1[0 : len("阿宁")]),
    )
    entities = [
        _entity_payload(
            stable_id="char-aning",
            entity_type="character",
            description="A determined young traveler in the bamboo grove.",
            authority="canon_fact",
            disclosure_cutoff=CUTOFF,
        ),
        _entity_payload(
            stable_id="place-grove",
            entity_type="place",
            description="A moonlit bamboo grove with mossy stones.",
            authority="canon_fact",
            disclosure_cutoff=CUTOFF,
        ),
    ]
    claims = [
        _claim_payload(
            claim_key="char-aning-grove",
            entity_stable_id="char-aning",
            authority="canon_fact",
            description="阿宁走进竹林",
            cutoff_chapter=CUTOFF,
            evidence=[
                _evidence_ref(
                    content=ch1,
                    find_text="阿宁走进竹林",
                    evidence_key=ev_key,
                    chapter_id=seed["chapter1_id"],
                    chapter_number=1,
                    source_snapshot_id=SOURCE_SNAPSHOT_ID,
                    source_snapshot_hash=snapshot_hash,
                    cutoff_chapter=CUTOFF,
                )
            ],
        ),
        _claim_payload(
            claim_key="place-grove-mood",
            entity_stable_id="place-grove",
            authority="probable_inference",
            description="The grove likely feels serene and moonlit.",
            author="fixture-author",
            rationale="The text emphasizes moonlight over the mossy stones.",
            cutoff_chapter=CUTOFF,
        ),
    ]
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": seed["owner_id"],
        "novel_id": seed["novel_id"],
        "version_key": version_key,
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_snapshot_hash": snapshot_hash,
        "cutoff_chapter": CUTOFF,
        "schema_hash": HEX64,
        "policy_hash": HEX64_B,
        "prompt_hash": HEX64_C,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": None,
        "constraints": None,
        "entities": entities,
        "claims": claims,
        "reference_assets": assets or [],
        "review_state": "candidate",
    }
    version = VisualBibleVersionContract.model_validate(payload)
    version = version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(version)}
    )
    return version.model_dump(mode="json")


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _build_envelope(
    *,
    owner_id: int,
    novel_id: int,
    svid: int,
    input_hash: str,
    snapshot_hash: str,
    version: dict[str, Any],
    tool_runs: list[dict[str, Any]],
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 Phase 30 VisualBibleArtifact 信封。"""
    evidence_keys = {
        ref["evidence_key"]
        for claim in version["claims"]
        for ref in claim["evidence_refs"]
    }
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
            "source_snapshot_hash": snapshot_hash,
        },
        "input_hash": input_hash if not wrong_input_hash else "9" * 64,
        "evidence_refs": sorted(evidence_keys),
        "parent_revision": None,
    }
    envelope: dict[str, Any] = {
        "type": "visual_bible",
        "schema_version": "visual-bible.v1",
        "producing_skill": "build-visual-bible",
        "producing_skill_version": "1.0.0",
        "visual_bible": version,
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
    factory, sync_url: str, *, suffix: str, asset_rights: str | None = "cleared"
) -> dict[str, Any]:
    """seed owner/novel + 注册 build-visual-bible + 构建版本契约 + 创建 run。"""
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    svid = await _register_skill(
        factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="build-visual-bible", tools=DEFAULT_TOOLS
        ),
    )
    snapshot_hash = _snapshot_hash(seed)
    assets = None if asset_rights is None else [_asset_payload(asset_key="ref-cover", rights_status=asset_rights)]
    version = _build_version_contract(
        seed=seed, snapshot_hash=snapshot_hash, assets=assets
    )
    run_input = {
        "novel_id": seed["novel_id"],
        "branch": None,
        "cutoff": CUTOFF,
        "source_snapshot": {"snapshot_hash": snapshot_hash, "dataset_lineage": "novel-source.v1"},
        "reference_assets": assets or [],
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
        "version": version,
        "run_input": run_input,
    }


def _evidence_key_of(seed: dict[str, Any]) -> str:
    """从版本契约提取 canon claim 的 leaf evidence key。"""
    for claim in seed["version"]["claims"]:
        for ref in claim["evidence_refs"]:
            return ref["evidence_key"]
    raise AssertionError("version has no evidence refs")


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase30_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 build-visual-bible manifest 注册成功：5 工具 allowlist + 零写权限 +
    [visual_bible:approve] 审批动作 + visual_bible 只读。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="build-visual-bible", tools=DEFAULT_TOOLS
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "build-visual-bible"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert version.write_permissions == []
        assert version.approval_required_for == ["visual_bible:approve"]
        assert version.forbidden_spaces == [
            "canon:original",
            "visual_bible:write",
            "derivative:write",
        ]
        assert "visual_bible" in version.read_permissions
        assert int(version.budget["max_calls"]) == 60


async def test_phase30_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="build-visual-bible",
        tools=list(DEFAULT_TOOLS) + ["delete_visual_bible"],
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


async def test_phase30_happy_path_visual_bible_artifact_and_approval(
    runtime_factory, api_client, migrated_postgres: str
):
    """正向链：真实 facade 工具 → 冻结 manifest → VisualBibleArtifact 信封 →
    finalize → candidate 产物 + 修订 → publisher 持久化 candidate 版本
    （证据物化）→ `visual_bible:approve` 批准 → review_state=approved；
    Chapter / cover_url 零变更。"""
    client, factory, sync_url = api_client
    ctx = await _set_up(
        runtime_factory, sync_url, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]
    evidence_key = _evidence_key_of(ctx)

    # stub agent loop：真实调用 Phase 30 门面工具（读取 Canon 正文与 leaf 证据）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        ch = await facade.execute(
            "get_chapter",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"], "chapter_id": ctx["chapter1_id"]},
        )
        assert ch["chapter_number"] == 1

        span = await facade.execute(
            "get_evidence_span",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "chapter_id": ctx["chapter1_id"],
                "source_start": 0,
                "source_end": len("阿宁"),
                "content_hash": _sha256(CHAPTER_CONTENT[0 : len("阿宁")]),
            },
        )
        assert span["evidence_key"] == evidence_key

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
        version=ctx["version"],
        tool_runs=[
            {"tool_name": "get_chapter", "calls": 1},
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
    assert artifact.type == "visual_bible"
    assert artifact.schema_version == "visual-bible.v1"
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
    # VisualBibleVersionContract 完整保留且 review_state 恒为 candidate。
    assert content["visual_bible"]["version_key"] == "vb-main"
    assert content["visual_bible"]["review_state"] == "candidate"
    assert content["visual_bible"]["source_snapshot_hash"] == ctx["snapshot_hash"]
    # ToolRun 血缘。
    assert content["tool_runs"] == [
        {"tool_name": "get_chapter", "calls": 1},
        {"tool_name": "get_evidence_span", "calls": 1},
    ]
    # official 信封未携带受保护合成字段 / 可变 Agent 状态。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content

    # ── 服务端 publisher：从 Artifact 提取版本契约 → visual-bible API 持久化候选 ──
    headers = {"Authorization": f"Bearer {ctx['owner_token']}"}
    created = await client.post(
        f"/api/novels/{ctx['novel_id']}/visual-bible",
        json={"version": content["visual_bible"]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]
    assert created.json()["version"]["review_state"] == "candidate"

    # ── `visual_bible:approve`：用户批准（evidence/rights gate 服务端裁决）──
    approved = await client.post(
        f"/api/novels/{ctx['novel_id']}/visual-bible/{version_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "owner",
            "reason": "visual bible approved by owner",
            "event_key": f"approve-{uuid.uuid4().hex}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"  # accepted visual authority

    # 重复批准（同一 event_key）→ 幂等，不产生第二个事件/状态变化（D-30-04）。
    again = await client.post(
        f"/api/novels/{ctx['novel_id']}/visual-bible/{version_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "owner",
            "reason": "duplicate approval attempt",
            "event_key": f"approve-{uuid.uuid4().hex}",
            "from_review_state": "approved",
        },
        headers=headers,
    )
    assert again.status_code == 409  # 已决终态，非法再决策（fail closed）

    # Original Canon 零变更：chapter 内容与 cover_url 均未被触碰（D-30-01）。
    async with runtime_factory() as session:
        novel_row = await session.get(Novel, ctx["novel_id"])
        chapter_row = await session.scalar(
            select(Chapter).options(undefer(Chapter.content)).where(
                Chapter.id == ctx["chapter1_id"]
            )
        )
        version_row = await session.get(VisualBibleVersion, version_id)
    assert novel_row is not None and novel_row.cover_url is None
    assert chapter_row is not None and chapter_row.content == CHAPTER_CONTENT
    assert version_row is not None and version_row.review_state == "approved"


async def test_phase30_approval_requires_rights_cleared(
    runtime_factory, api_client, migrated_postgres: str
):
    """reference asset rights 未 cleared → 批准被 approval gate 拒绝
    （rights_unresolved，fail closed），review_state 保持 candidate。"""
    client, factory, sync_url = api_client
    ctx = await _set_up(
        runtime_factory,
        sync_url,
        suffix=f"rght_{uuid.uuid4().hex[:6]}",
        asset_rights="unreviewed",
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "completed", outcome.status_reason

    async with runtime_factory() as session:
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
    assert revision is not None

    headers = {"Authorization": f"Bearer {ctx['owner_token']}"}
    created = await client.post(
        f"/api/novels/{ctx['novel_id']}/visual-bible",
        json={"version": revision.content["visual_bible"]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]

    # rights 未清除 → approval gate 拒绝，无 review 事件、状态不变。
    blocked = await client.post(
        f"/api/novels/{ctx['novel_id']}/visual-bible/{version_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "owner",
            "reason": "attempt to approve with unreviewed asset",
            "event_key": f"approve-{uuid.uuid4().hex}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert blocked.status_code == 409
    assert "rights_unresolved" in blocked.text or "rights" in blocked.text

    async with runtime_factory() as session:
        version_row = await session.get(VisualBibleVersion, version_id)
        chapter_row = await session.scalar(
            select(Chapter).options(undefer(Chapter.content)).where(
                Chapter.id == ctx["chapter1_id"]
            )
        )
    assert version_row is not None and version_row.review_state == "candidate"
    assert chapter_row is not None and chapter_row.content == CHAPTER_CONTENT


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase30_cancellation_no_write(runtime_factory, migrated_postgres: str):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"cancel_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="build-visual-bible", tools=DEFAULT_TOOLS
        ),
    )
    snapshot_hash = _snapshot_hash(seed)
    version = _build_version_contract(seed=seed, snapshot_hash=snapshot_hash)
    run_input = {
        "novel_id": seed["novel_id"],
        "cutoff": CUTOFF,
        "source_snapshot": {"snapshot_hash": snapshot_hash},
    }
    input_hash = canonical_input_hash(run_input)
    run_id = await _create_run(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=input_hash,
        input_data=run_input,
        cancel_requested=True,
    )
    evidence_key = leaf_evidence_key(
        chapter_id=seed["chapter1_id"],
        source_start=0,
        source_end=len("阿宁"),
        content_hash=_sha256(CHAPTER_CONTENT[0 : len("阿宁")]),
    )
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        svid=svid,
        input_hash=input_hash,
        snapshot_hash=snapshot_hash,
        version=version,
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


async def test_phase30_timeout_nonstop_reason_fails(
    runtime_factory, migrated_postgres: str
):
    """timeout 语义（非 stop reason）→ failed(invalid_stop_reason)，零官方写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"to_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        stop_reason="max_tokens",
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_INVALID_STOP_REASON
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_owner=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ver_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_version=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_stale_input_hash_blocks(runtime_factory, migrated_postgres: str):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        wrong_input_hash=True,
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_schema_drift_blocks(runtime_factory, migrated_postgres: str):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"schema_version": "visual-bible.v2"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "schema" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_missing_evidence_blocks(runtime_factory, migrated_postgres: str):
    """VisualBibleArtifact 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"noev_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    envelope["evidence_refs"] = []
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_forged_approval_protected_field_blocks(
    runtime_factory, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval_state/authority 受保护字段）→ blocked。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"syn_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"approval_state": "approved", "authority": "model-claimed"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_approval_bypass_status_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：envelope status 非 candidate（如 approved）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
        extra={"status": "approved"},
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_approval_bypass_review_state_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval bypass：visual_bible.review_state 非 candidate（如 approved）→ blocked。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"rev_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    forged_version = deepcopy(ctx["version"])
    forged_version["review_state"] = "approved"  # Agent 声称已批准
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=forged_version,
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "approval bypass" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_unknown_evidence_ref_blocks(
    runtime_factory, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ref_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    # 白名单只含真实 claim 证据；信封额外声明一个不在 manifest 的未知 ref。
    unknown = "qp:1:0:10:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    envelope["evidence_refs"] = [evidence_key, unknown]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "unknown evidence ref" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_claim_evidence_not_in_envelope_blocks(
    runtime_factory, migrated_postgres: str
):
    """canon claim 的 evidence_key 不在信封 evidence_refs → integrity blocked
    （D-30-02 leaf-evidence 资格门），零写入。"""
    ctx = await _set_up(
        runtime_factory, migrated_postgres, suffix=f"ev_{uuid.uuid4().hex[:6]}"
    )
    evidence_key = _evidence_key_of(ctx)
    envelope = _build_envelope(
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        svid=ctx["skill_version_id"],
        input_hash=ctx["input_hash"],
        snapshot_hash=ctx["snapshot_hash"],
        version=ctx["version"],
        tool_runs=[{"tool_name": "get_evidence_span", "calls": 1}],
    )
    # 信封 evidence_refs 不覆盖 canon claim 的 evidence key → integrity blocked
    # （D-30-02 leaf-evidence 资格门）。
    envelope["evidence_refs"] = ["0" * 64]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [evidence_key, "0" * 64]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "evidence" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase30_stale_snapshot_publisher_blocked(
    runtime_factory, api_client, migrated_postgres: str
):
    """publisher 侧 stale source snapshot 无法物化 → API 409（fail closed），
    无 candidate 版本行、无 review 事件。FastAPI 保留 evidence 权威。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"stale_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    stale_hash = "0" * 64  # 与当前章节集不符
    version = _build_version_contract(seed=seed, snapshot_hash=stale_hash)
    resp = await client.post(
        f"/api/novels/{seed['novel_id']}/visual-bible",
        json={"version": version},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["kind"] == "visual_bible_unresolved"
    assert body["unresolved"]  # reason-coded 未物化

    async with runtime_factory() as session:
        version_count = await session.scalar(
            select(func.count())
            .select_from(VisualBibleVersion)
            .where(VisualBibleVersion.novel_id == seed["novel_id"])
        )
        chapter_row = await session.scalar(
            select(Chapter).options(undefer(Chapter.content)).where(
                Chapter.id == seed["chapter1_id"]
            )
        )
    assert int(version_count or 0) == 0  # 零域写入
    assert chapter_row is not None and chapter_row.content == CHAPTER_CONTENT


# ────────────────────────── HTTP 端到端（register → accept → finalize → no agent-granted approval） ──────────────────────────


async def test_phase30_http_end_to_end_candidate_artifact_no_agent_approval(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ stub loop → finalize →
    candidate VisualBibleArtifact + revision；Agent 不能铸造/授予 ApprovalRequest，
    review_state 只能由用户经 review API 批准。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['owner_token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(
            novel_id=seed["novel_id"], name="build-visual-bible", tools=DEFAULT_TOOLS
        ).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    snapshot_hash = _snapshot_hash(seed)
    version = _build_version_contract(seed=seed, snapshot_hash=snapshot_hash)
    run_input = {
        "novel_id": seed["novel_id"],
        "question": "请为这本小说构建 Visual Bible。",
        "branch": None,
        "cutoff": CUTOFF,
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

    evidence_key = leaf_evidence_key(
        chapter_id=seed["chapter1_id"],
        source_start=0,
        source_end=len("阿宁"),
        content_hash=_sha256(CHAPTER_CONTENT[0 : len("阿宁")]),
    )
    envelope = _build_envelope(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        svid=svid,
        input_hash=run_hash,
        snapshot_hash=snapshot_hash,
        version=version,
        tool_runs=[
            {"tool_name": "get_novel", "calls": 1},
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
    assert artifact.type == "visual_bible"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
