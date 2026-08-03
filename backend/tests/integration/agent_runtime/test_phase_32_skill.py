"""Phase 32-05 集成测试：SkillRun → Tool → Artifact → Validator → Approval 端到端边界证明。

证明 Phase 32 确定性 Scene Spec / Prompt 编译能力经版本化 compile-scene-spec Skill
消费（REQ-VIS-03 + REQ-AGENT-02/03/04 + D-32-01..D-32-04），SceneSpecArtifact 与
PromptArtifact 是唯一官方 Agent 输出，且 Agent 不能绕过域/证据/审批/发布权威：

正向链：
  register（版本化 manifest：2 工具 allowlist get_visual_bible/get_evidence_span +
  空 write_permissions + [scene_spec:approve] 审批动作）→ accept run
  （owner/novel/branch + input_hash 绑定）→ stub loop 调真实 facade 工具
  （get_visual_bible / get_evidence_span，读取已批准 Visual Bible 修订与 leaf
  证据）→ 物化 leaf evidence + 冻结 Frozen Manifest → 服务端确定性编译器按引用
  消费 frozen key-scene set + approved Visual Bible revision 编译
  SceneSpecContract / PromptRevisionContract → SceneSpecArtifact / PromptArtifact
  信封（candidate-only，review_state 恒为 candidate）→ 确定性 finalizer
  （integrity gate + 白名单校验 + SceneSpec/Prompt 域校验）→ candidate 产物 +
  首个不可变修订。`scene_spec:approve` 用户审查/批准只授权 Phase 33 消费
  （D-32-04），Canon / Visual Bible / key-scene 集 / scene_spec 域表零变更。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、wrong owner / skill_version / input_hash（lineage 血缘）、
  schema drift、unsupported detail（content_hash 重放失败）、missing evidence、
  approval bypass（envelope status 非 candidate / scene_spec review_state 非
  candidate / 受保护字段合成）、stale visual_bible_revision_hash、
  prompt scene_spec_hash 不匹配、evidence 前缀不匹配、source snapshot 漂移、
  unknown evidence ref。FastAPI 与确定性 validators（Canon/Visual Bible 一致性 +
  未支持细节拒绝）保留 permission / evidence / state-transition / publication
  权威。
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
from app.models.scene_spec import SceneSpecVersion as SceneSpecVersionRow
from app.models.visual_bible import VisualBibleVersion as VisualBibleVersionRow
from app.schemas.agent_runtime import SkillVersionRegister
from app.schemas.visual_bible import (
    VisualBibleVersionContract,
    VisualClaimContract,
    claim_content_hash,
    recompute_manifest_hash,
)
from app.services.agent_runtime.finalize import (
    ERROR_CODE_FAILED_VALIDATION,
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
    detect_chapter_boundaries,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 32 编排 allowlist：2 个只读域工具。
DEFAULT_TOOLS = ["get_visual_bible", "get_evidence_span"]

HEX64 = "a" * 64

CH1 = "Ayla was a tall young woman with braided amber hair and green eyes. She wore a grey wool cloak and drew her sword."
CH2 = "The stone hall of the northern keep stood cold; its tall windows let in pale light."
CH3 = "Mara watched the courtyard in the rain and tightened the string of her bow."


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
        "read_permissions": ["canon", "visual_bible", "key_scene", "scene_spec"],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "visual_bible:write",
            "key_scene:write",
            "scene_spec:write",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        "approval_required_for": ["scene_spec:approve"],
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "spec_key": {"type": "string"},
                "candidate_set_id": {"type": "integer"},
                "candidate_key": {"type": "string"},
                "visual_bible_version_id": {"type": "integer"},
                "source_snapshot_id": {"type": "string"},
            },
            "required": ["novel_id", "spec_key", "candidate_set_id", "candidate_key"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "scene_spec"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说 + 三章正文（与 test_scope.py 同口径）。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p32_{suffix}",
            email=f"p32_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"P32 Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=3,
            word_count=sum(len(c) for c in (CH1, CH2, CH3)),
        )
        session.add(novel)
        session.flush()
        chapter_ids: list[int] = []
        for i, content in enumerate((CH1, CH2, CH3), start=1):
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=i,
                title=f"C{i}",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            chapter_ids.append(chapter.id)
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_ids": chapter_ids,
            "contents": [CH1, CH2, CH3],
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


def _snapshot_hash(ids: dict[str, Any]) -> str:
    """Key-scene domain snapshot hash（the set's evidence lineage）。"""
    chapters = [
        ChapterRecord(
            chapter_id=chapter_id,
            chapter_number=i + 1,
            content=content,
        )
        for i, (chapter_id, content) in enumerate(
            zip(ids["chapter_ids"], ids["contents"])
        )
    ]
    return compute_source_snapshot_hash(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], chapters=chapters
    )


def _visual_bible_snapshot_hash(ids: dict[str, Any]) -> str:
    """Visual Bible domain snapshot hash（its own lineage domain）。"""
    from app.services.visual_bible.evidence import (
        ChapterRecord as VisualBibleChapterRecord,
        compute_source_snapshot_hash as compute_visual_bible_snapshot_hash,
    )

    chapters = [
        VisualBibleChapterRecord(
            chapter_id=chapter_id,
            chapter_number=i + 1,
            content=content,
        )
        for i, (chapter_id, content) in enumerate(
            zip(ids["chapter_ids"], ids["contents"])
        )
    ]
    return compute_visual_bible_snapshot_hash(
        owner_id=ids["owner_id"], novel_id=ids["novel_id"], chapters=chapters
    )


def _scene_coordinates(chapter_number: int) -> dict[str, Any]:
    if chapter_number == 1:
        return {
            "cast": ["ayla"],
            "place": "northern keep",
            "time": "night",
            "pov": "ayla",
        }
    if chapter_number == 2:
        return {"cast": ["ayla"], "place": "hall", "time": "day", "pov": "ayla"}
    return {"cast": ["mara"], "place": "courtyard", "time": "night", "pov": "mara"}


def _evidence_payload(
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
        "excerpt": content[start:end],
        "cutoff_chapter": cutoff_chapter,
    }


def build_version_payload(
    ids: dict[str, Any],
    *,
    snapshot_hash: str,
    snapshot_id: str,
    cutoff_chapter: int,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    """One approved-candidate Visual Bible version payload with canon claims."""
    ayla_evidence = _evidence_payload(
        content=ids["contents"][0],
        find_text="braided amber hair and green eyes",
        evidence_key="ev-ayla-hair",
        chapter_id=ids["chapter_ids"][0],
        chapter_number=1,
        source_snapshot_id=snapshot_id,
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=cutoff_chapter,
    )
    mara_evidence = _evidence_payload(
        content=ids["contents"][2],
        find_text="tightened the string of her bow",
        evidence_key="ev-mara-bow",
        chapter_id=ids["chapter_ids"][2],
        chapter_number=3,
        source_snapshot_id=snapshot_id,
        source_snapshot_hash=snapshot_hash,
        cutoff_chapter=cutoff_chapter,
    )
    entities = [
        {
            "stable_id": "ayla",
            "entity_key": "ayla",
            "entity_type": "character",
            "description": "Ayla, a tall woman with braided amber hair and green eyes",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
        {
            "stable_id": "mara",
            "entity_key": "mara",
            "entity_type": "character",
            "description": "Mara, a watchful archer who keeps her bow ready",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
        {
            "stable_id": "northern keep",
            "entity_key": "northern keep",
            "entity_type": "place",
            "description": "The stone hall of the northern keep with tall cold windows",
            "authority": "canon_fact",
            "disclosure_cutoff": cutoff_chapter,
        },
    ]
    ayla_claim = VisualClaimContract.model_validate(
        {
            "claim_key": "c-ayla-appearance",
            "entity_stable_id": "ayla",
            "authority": "canon_fact",
            "description": "Ayla has braided amber hair and green eyes",
            "author": None,
            "rationale": None,
            "cutoff_chapter": cutoff_chapter,
            "claim_hash": "0" * 64,
            "evidence_refs": [ayla_evidence],
        }
    )
    ayla_claim = ayla_claim.model_copy(
        update={"claim_hash": claim_content_hash(ayla_claim)}
    )
    mara_claim = VisualClaimContract.model_validate(
        {
            "claim_key": "c-mara-bow",
            "entity_stable_id": "mara",
            "authority": "canon_fact",
            "description": "Mara is an archer who tightens her bowstring",
            "author": None,
            "rationale": None,
            "cutoff_chapter": cutoff_chapter,
            "claim_hash": "0" * 64,
            "evidence_refs": [mara_evidence],
        }
    )
    mara_claim = mara_claim.model_copy(
        update={"claim_hash": claim_content_hash(mara_claim)}
    )
    payload = {
        "schema_version": "visual-bible.v1",
        "artifact_kind": "visual_bible",
        "owner_id": ids["owner_id"],
        "novel_id": ids["novel_id"],
        "version_key": version_key,
        "revision_number": 1,
        "parent_version_id": None,
        "source_snapshot_id": snapshot_id,
        "source_snapshot_hash": snapshot_hash,
        "cutoff_chapter": cutoff_chapter,
        "schema_hash": HEX64,
        "policy_hash": HEX64,
        "prompt_hash": HEX64,
        "model_hash": None,
        "config_hash": None,
        "manifest_hash": "0" * 64,
        "style_profile": {"palette": "muted cold tones", "lighting": "overcast"},
        "constraints": [
            {
                "constraint_key": "nc-no-modern-era",
                "scope": "era",
                "source": "visual_bible",
                "text": "the scene must stay in the medieval era; no modern objects",
            },
            {
                "constraint_key": "nc-no-ornate-armor",
                "scope": "costume",
                "source": "visual_bible",
                "text": "do not add ornate armor to Ayla",
            },
        ],
        "entities": entities,
        "claims": [
            ayla_claim.model_dump(mode="json"),
            mara_claim.model_dump(mode="json"),
        ],
        "reference_assets": [],
        "review_state": "candidate",
    }
    version = VisualBibleVersionContract.model_validate(payload)
    version = version.model_copy(
        update={"manifest_hash": recompute_manifest_hash(version)}
    )
    return {"version": version.model_dump(mode="json")}


def _generate_payload(
    ids: dict[str, Any],
    *,
    coordinates: dict[str, dict[str, Any]],
    scene_ids: list[str],
    snapshot_hash: str,
    vb_version_id: int,
    vb_manifest_hash: str,
    cutoff: int = 3,
) -> dict[str, Any]:
    return {
        "version_key": f"ks-{uuid.uuid4().hex[:8]}",
        "cutoff_chapter": cutoff,
        "source_snapshot_id": "ss-main",
        "coordinates": coordinates,
        "approved_visual_bible_revision_id": vb_version_id,
        "approved_visual_bible_revision_hash": vb_manifest_hash,
    }


def _scene_ids(
    ids: dict[str, Any], snapshot_hash: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    scene_ids: list[str] = []
    coordinates: dict[str, dict[str, Any]] = {}
    for i, (chapter_id, content) in enumerate(
        zip(ids["chapter_ids"], ids["contents"]), start=1
    ):
        outcome = detect_chapter_boundaries(
            novel_id=ids["novel_id"],
            chapter_id=chapter_id,
            chapter_number=i,
            content=content,
            source_snapshot_hash=snapshot_hash,
        )
        for boundary in outcome.boundaries:
            scene_ids.append(boundary.scene_id)
            coordinates[boundary.scene_id] = _scene_coordinates(i)
    return scene_ids, coordinates


async def _create_and_approve_visual_bible(
    client: Any,
    ids: dict[str, Any],
    headers: dict[str, str],
    *,
    snapshot_hash: str,
    version_key: str = "vb-main",
) -> dict[str, Any]:
    payload = build_version_payload(
        ids,
        snapshot_hash=snapshot_hash,
        snapshot_id="ss-main",
        cutoff_chapter=3,
        version_key=version_key,
    )
    base = f"/api/novels/{ids['novel_id']}/visual-bible"
    created = await client.post(base, json=payload, headers=headers)
    assert created.status_code == 201, created.text
    version_id = created.json()["version"]["id"]
    manifest_hash = created.json()["version"]["manifest_hash"]
    approved = await client.post(
        f"{base}/{version_id}/review",
        json={
            "action": "approve",
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "approved for key-scene freeze",
            "event_key": f"approve-{version_key}-{uuid.uuid4().hex[:8]}",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_state"] == "approved"
    return {"id": version_id, "manifest_hash": manifest_hash}


async def _freeze_key_scene_set(
    client: Any,
    ids: dict[str, Any],
    headers: dict[str, str],
    *,
    snapshot_hash: str,
    vb_version_id: int,
    vb_manifest_hash: str,
) -> dict[str, Any]:
    scene_ids, coordinates = _scene_ids(ids, snapshot_hash)
    base = f"/api/novels/{ids['novel_id']}/key-scenes"
    payload = _generate_payload(
        ids,
        coordinates=coordinates,
        scene_ids=scene_ids,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb_version_id,
        vb_manifest_hash=vb_manifest_hash,
    )
    generated = await client.post(f"{base}/generate", json=payload, headers=headers)
    assert generated.status_code == 201, generated.text
    set_view = generated.json()["set"]
    set_id = set_view["id"]

    for candidate in set_view["candidates"]:
        review = await client.post(
            f"{base}/{set_id}/review",
            json={
                "decision_key": f"approve-{candidate['candidate_key']}-{uuid.uuid4().hex[:8]}",
                "action": "approve",
                "actor_source": "human",
                "actor": "test-reviewer",
                "reason": "approved candidate for freeze",
                "from_review_state": "candidate",
                "candidate_key": candidate["candidate_key"],
            },
            headers=headers,
        )
        assert review.status_code == 200, review.text

    frozen = await client.post(
        f"{base}/{set_id}/freeze",
        json={
            "actor_source": "human",
            "actor": "test-reviewer",
            "reason": "freeze set for scene-spec compile",
        },
        headers=headers,
    )
    assert frozen.status_code == 200, frozen.text

    evidence_keys: list[str] = []
    for candidate in set_view["candidates"]:
        for ev in candidate.get("evidence_ranges") or []:
            evidence_keys.append(ev["evidence_key"])
    return {
        "set_id": set_id,
        "candidate_key": set_view["candidates"][0]["candidate_key"],
        "candidate_keys": [c["candidate_key"] for c in set_view["candidates"]],
        "snapshot_hash": set_view["source_snapshot_hash"],
        "coordinates": coordinates,
        "scene_ids": scene_ids,
        "evidence_keys": sorted(set(evidence_keys)),
    }


def _spec_preview_payload(
    frozen: dict[str, Any],
    *,
    spec_key: str,
    vb_version_id: int,
) -> dict[str, Any]:
    return {
        "spec_key": spec_key,
        "candidate_set_id": frozen["set_id"],
        "candidate_key": frozen["candidate_key"],
        "visual_bible_version_id": vb_version_id,
        "source_snapshot_id": "ss-main",
        "revision_number": 1,
    }


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
            "calls": 2,
            "input_tokens": 400,
            "output_tokens": 200,
            "cost_usd": "0.0008",
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


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


# ────────────────────────── 确定性编译 + 信封构建 ──────────────────────────


async def _compile_domain_contracts(factory, *, ctx: dict[str, Any]) -> dict[str, Any]:
    """服务端确定性编译器按引用消费 frozen candidate + approved Visual Bible，
    产出 SceneSpecContract + PromptRevisionContract（D-32-01..D-32-04）。"""
    from app.services.scene_spec.compiler import (
        SceneSpecPreviewRequest,
        SceneSpecService,
        build_prompt_revision_from_spec,
        compile_scene_spec,
    )

    async with factory() as session:
        service = SceneSpecService(session)
        compile_input = await service.compile_input(
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            request=SceneSpecPreviewRequest(
                spec_key=ctx["spec_key"],
                candidate_set_id=ctx["frozen"]["set_id"],
                candidate_key=ctx["frozen"]["candidate_key"],
                visual_bible_version_id=ctx["vb"]["id"],
                source_snapshot_id="ss-main",
                revision_number=1,
            ),
        )
        compiled = compile_scene_spec(compile_input)
        spec = compiled.spec
        prompt = build_prompt_revision_from_spec(
            spec, prompt_key=f"{ctx['spec_key']}-prompt"
        )
    return {"spec": spec, "prompt": prompt}


def _build_envelope(
    ctx: dict[str, Any],
    *,
    prompt: bool = False,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 SceneSpecArtifact / PromptArtifact 信封。"""
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"] if not wrong_owner else ctx["owner_id"] + 999,
        "novel_id": ctx["novel_id"],
        "branch": None,
        "producing_skill": "compile-scene-spec",
        "producing_skill_version": "1.0.0",
        "skill_version_id": (
            ctx["skill_version_id"]
            if not wrong_version
            else ctx["skill_version_id"] + 999
        ),
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": ctx["snapshot_hash"],
        },
        "input_hash": ctx["input_hash"] if not wrong_input_hash else "9" * 64,
        "evidence_refs": list(ctx["evidence_keys"]),
        "tool_runs": [
            {"tool_name": "get_visual_bible", "calls": 1},
            {"tool_name": "get_evidence_span", "calls": len(ctx["evidence_keys"])},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    spec_payload = ctx["contracts"]["spec"].model_dump(mode="json")
    if prompt:
        envelope: dict[str, Any] = {
            "type": "prompt",
            "schema_version": "prompt-revision.v1",
            **common,
            "prompt_revision": ctx["contracts"]["prompt"].model_dump(mode="json"),
            "scene_spec": spec_payload,
        }
    else:
        envelope = {
            "type": "scene_spec",
            "schema_version": "scene-spec.v1",
            **common,
            "scene_spec": spec_payload,
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


async def _set_up(
    runtime_factory, api_client, sync_url: str, *, suffix: str
) -> dict[str, Any]:
    """seed owner/novel + Visual Bible（approved）+ frozen key-scene set +
    注册 compile-scene-spec + 确定性编译 spec/prompt + 创建 run。"""
    client, factory, _sync_url = api_client
    seed = _seed_owner_novel(sync_url, suffix=suffix)
    headers = {"Authorization": f"Bearer {seed['token']}"}
    vb_snapshot_hash = _visual_bible_snapshot_hash(seed)
    snapshot_hash = _snapshot_hash(seed)
    vb = await _create_and_approve_visual_bible(
        client, seed, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        seed,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="compile-scene-spec", tools=DEFAULT_TOOLS
        ),
    )
    ctx: dict[str, Any] = {
        **seed,
        "headers": headers,
        "skill_version_id": svid,
        "snapshot_hash": snapshot_hash,
        "vb": vb,
        "frozen": frozen,
        "evidence_keys": list(frozen["evidence_keys"]),
        "spec_key": "spec-agent",
    }
    contracts = await _compile_domain_contracts(runtime_factory, ctx=ctx)
    ctx["contracts"] = contracts
    run_input = {
        "novel_id": seed["novel_id"],
        "branch": None,
        "spec_key": ctx["spec_key"],
        "candidate_set_id": frozen["set_id"],
        "candidate_key": frozen["candidate_key"],
        "visual_bible_version_id": vb["id"],
        "source_snapshot_id": "ss-main",
        "revision_number": 1,
    }
    ctx["run_input"] = run_input
    ctx["input_hash"] = canonical_input_hash(run_input)
    ctx["run_id"] = await _create_run(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        input_data=run_input,
    )
    return ctx


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase32_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 compile-scene-spec manifest 注册成功：2 工具 allowlist（含
    get_visual_bible / get_evidence_span）+ 零写权限 + [scene_spec:approve]
    审批动作。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="compile-scene-spec", tools=DEFAULT_TOOLS
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "compile-scene-spec"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "get_visual_bible" in version.allowed_tools
        assert "get_evidence_span" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == ["scene_spec:approve"]
        assert version.forbidden_spaces == [
            "canon:original",
            "visual_bible:write",
            "key_scene:write",
            "scene_spec:write",
        ]
        assert "scene_spec" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase32_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具 → 注册拒绝，无 active 行（unknown tools fail closed）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="compile-scene-spec",
        tools=list(DEFAULT_TOOLS) + ["delete_scene_spec"],
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


async def test_phase32_happy_path_scene_spec_artifact(
    runtime_factory, api_client, migrated_postgres: str
):
    """正向链（SceneSpecArtifact）：真实 facade 工具 → 冻结 manifest → 确定性编译
    SceneSpecContract → 信封 → finalize → candidate 产物 + 修订；Canon / Visual
    Bible / key-scene 集 / scene_spec 域表零变更，无 ApprovalRequest。"""
    client, factory, sync_url = api_client
    ctx = await _set_up(
        runtime_factory, api_client, sync_url, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    svid, run_id = ctx["skill_version_id"], ctx["run_id"]
    evidence_keys = ctx["evidence_keys"]

    # stub agent loop：真实调用 Phase 32 门面工具（读取已批准 Visual Bible 修订与
    # leaf 证据；validated SceneCandidate 由服务端确定性编译器按引用消费）。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        vb = await facade.execute(
            "get_visual_bible",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "version_id": ctx["vb"]["id"],
                "approved_only": True,
            },
        )
        assert vb is not None and vb["version_key"] == "vb-main"

        # get_evidence_span 按 queryplan leaf 证据域（纯 sha256）物化 chapter 切片；
        # 引证白名单（frozen manifest）使用候选集的 `ev-...` evidence keys——
        # 与 Phase 31 一致：工具验证物化路径，citation 权威在服务端候选集。
        content = CH1
        start, end = 0, min(len(content), 40)
        span = await facade.execute(
            "get_evidence_span",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={
                "novel_id": ctx["novel_id"],
                "chapter_id": ctx["chapter_ids"][0],
                "source_start": start,
                "source_end": end,
                "content_hash": _sha256(content[start:end]),
            },
        )
        assert span is not None and span["chapter_number"] == 1
        assert evidence_keys

    frozen_manifest = {
        "evidence_refs": evidence_keys,
        "manifest_checksum": "m" * 64,
    }
    envelope = _build_envelope(ctx)
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
        domain_specs = await session.scalar(
            select(func.count())
            .select_from(SceneSpecVersionRow)
            .where(SceneSpecVersionRow.owner_id == ctx["owner_id"])
        )
        vb_count = await session.scalar(
            select(func.count())
            .select_from(VisualBibleVersionRow)
            .where(VisualBibleVersionRow.owner_id == ctx["owner_id"])
        )
        chapter_row = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_ids"][0])
        )
    assert artifact is not None and artifact.status == "candidate"
    assert artifact.type == "scene_spec"
    assert artifact.schema_version == "scene-spec.v1"
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
    assert content["evidence_refs"] == evidence_keys
    assert content["tool_runs"] == [
        {"tool_name": "get_visual_bible", "calls": 1},
        {"tool_name": "get_evidence_span", "calls": len(evidence_keys)},
    ]
    spec = content["scene_spec"]
    assert spec["spec_key"] == ctx["spec_key"]
    assert spec["review_state"] == "candidate"
    assert spec["visual_bible_revision_hash"] == ctx["vb"]["manifest_hash"]
    assert spec["source_snapshot_hash"] == ctx["snapshot_hash"]
    assert spec["content_hash"] == ctx["contracts"]["spec"].content_hash
    assert spec["details"], "compiled spec must carry deterministic details"
    # official 信封未携带受保护合成字段 / 可变 Agent 状态。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content
    # 域零变更：Agent 不写 scene_spec 域表、不写 Visual Bible、不碰 Canon。
    assert int(domain_specs or 0) == 0
    assert int(vb_count or 0) == 1
    assert chapter_row is not None and chapter_row.content == CH1


async def test_phase32_happy_path_prompt_artifact(
    runtime_factory, api_client, migrated_postgres: str
):
    """正向链（PromptArtifact）：同一确定性编译的 spec + prompt → 信封 → finalize →
    candidate 产物；prompt 字符串不是权威（D-32-01），Canon / Visual Bible 零变更。"""
    client, factory, sync_url = api_client
    ctx = await _set_up(
        runtime_factory, api_client, sync_url, suffix=f"pr_{uuid.uuid4().hex[:6]}"
    )
    run_id = ctx["run_id"]

    frozen_manifest = {
        "evidence_refs": ctx["evidence_keys"],
        "manifest_checksum": "m" * 64,
    }
    envelope = _build_envelope(ctx, prompt=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        frozen_manifest=frozen_manifest,
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_id is not None
    assert await _count(runtime_factory, Artifact, run_id=run_id) == 1
    assert await _count_approvals(runtime_factory, run_id=run_id) == 0

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        domain_specs = await session.scalar(
            select(func.count())
            .select_from(SceneSpecVersionRow)
            .where(SceneSpecVersionRow.owner_id == ctx["owner_id"])
        )
        chapter_row = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_ids"][0])
        )
    assert artifact is not None and artifact.type == "prompt"
    assert artifact.schema_version == "prompt-revision.v1"
    assert artifact.status == "candidate"
    content = revision.content
    assert content["type"] == "prompt"
    assert content["prompt_revision"]["review_state"] == "candidate"
    assert (
        content["prompt_revision"]["scene_spec_hash"]
        == ctx["contracts"]["spec"].content_hash
    )
    assert (
        content["prompt_revision"]["input_hash"]
        != content["prompt_revision"]["prompt_hash"]
    )
    assert content["scene_spec"]["spec_key"] == ctx["spec_key"]
    assert int(domain_specs or 0) == 0
    assert chapter_row is not None and chapter_row.content == CH1


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase32_cancellation_no_write(
    runtime_factory, api_client, migrated_postgres: str
):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"cancel_{uuid.uuid4().hex[:6]}",
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
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=run_id,
        envelope=envelope,
        stop_reason="aborted",
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "cancelled"
    assert outcome.artifact_id is None
    await _assert_zero_writes(runtime_factory, run_id=run_id)


async def test_phase32_wrong_owner_lineage_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """envelope owner 血缘与 run 不符 → blocked，零写入（不补默认值）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"own_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_owner=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "owner_id" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_wrong_skill_version_lineage_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"ver_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_version=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "skill_version_id" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_stale_input_hash_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"hash_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, wrong_input_hash=True)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "input_hash" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_schema_drift_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """schema drift（schema_version 非法）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, extra={"schema_version": "scene-spec.v2"})
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "schema" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_unsupported_detail_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """unsupported detail：scene_spec 负载 content_hash 被篡改（未支持细节伪装成
    Canon）→ 确定性域校验失败 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"det_{uuid.uuid4().hex[:6]}",
    )
    forged_spec = (
        ctx["contracts"]["spec"]
        .model_copy(update={"content_hash": "e" * 64})
        .model_dump(mode="json")
    )
    envelope = _build_envelope(ctx)
    envelope["scene_spec"] = forged_spec
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "scene spec payload" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_missing_evidence_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """SceneSpecArtifact 无 evidence_refs（heuristic candidate）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"noev_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    envelope["evidence_refs"] = []
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "heuristic candidate" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_forged_approval_protected_field_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """attempted 审批伪造（信封携带 approval_state/authority 受保护字段）→ blocked。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"syn_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, extra={"approval_state": "approved", "authority": "model-claimed"}
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_approval_bypass_status_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """approval bypass：envelope status 非 candidate（如 approved）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"appr_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, extra={"status": "approved"})
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_approval_bypass_review_state_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """approval bypass：scene_spec.review_state 非 candidate（如 approved）→ blocked。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"rev_{uuid.uuid4().hex[:6]}",
    )
    # 直接构造伪造负载 dict（避免 pydantic serializer 对 enum 字段的警告）。
    forged_spec = dict(ctx["contracts"]["spec"].model_dump(mode="json"))
    forged_spec["review_state"] = "approved"
    envelope = _build_envelope(ctx)
    envelope["scene_spec"] = forged_spec
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None and "approval bypass" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_stale_visual_bible_revision_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """stale Visual Bible revision：scene_spec 负载 revision hash 与内部 refs 漂移 →
    确定性域校验失败 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"vb_{uuid.uuid4().hex[:6]}",
    )
    forged_spec = (
        ctx["contracts"]["spec"]
        .model_copy(update={"visual_bible_revision_hash": "f" * 64})
        .model_dump(mode="json")
    )
    envelope = _build_envelope(ctx)
    envelope["scene_spec"] = forged_spec
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "scene spec payload" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_prompt_spec_hash_mismatch_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """prompt 派生血缘漂移：prompt_revision.scene_spec_hash 与 scene_spec 不一致 →
    blocked，零写入（D-32-03）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"ph_{uuid.uuid4().hex[:6]}",
    )
    forged_prompt = (
        ctx["contracts"]["prompt"]
        .model_copy(update={"scene_spec_hash": "c" * 64})
        .model_dump(mode="json")
    )
    envelope = _build_envelope(ctx, prompt=True)
    envelope["prompt_revision"] = forged_prompt
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "prompt revision payload" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_evidence_mismatch_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """evidence 前缀不匹配：信封 evidence_refs 不含 spec clause 的 raw leaf key →
    integrity blocked，零写入（D-32-02 leaf-evidence 资格门）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"ev_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    # 信封只声明一个与 spec clause 无关的 key → 前缀匹配门必须拦截。
    envelope["evidence_refs"] = ["0" * 64]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": [*ctx["evidence_keys"], "0" * 64]},
    )
    assert outcome.status == "failed"
    assert outcome.status_reason is not None and "evidence" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_source_snapshot_drift_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """source snapshot 漂移：信封 source_versions.source_snapshot_hash 与 spec 血缘
    不符 → blocked，零写入（D-32-03）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"snap_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    envelope["source_versions"] = {
        "novel": "v1",
        "source_snapshot_hash": "0" * 64,
    }
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "source_snapshot_hash" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase32_unknown_evidence_ref_blocks(
    runtime_factory, api_client, migrated_postgres: str
):
    """evidence_ref 不在冻结 manifest 白名单 → blocked，零写入（leaf-evidence 权威）。"""
    ctx = await _set_up(
        runtime_factory,
        api_client,
        migrated_postgres,
        suffix=f"ref_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    unknown = (
        "qp:1:0:10:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    )
    envelope["evidence_refs"] = [*ctx["evidence_keys"], unknown]
    envelope["normalization"]["repaired_hash"] = canonical_content_hash(
        _strip_trail(envelope)
    )
    envelope["normalization"]["raw_hash"] = envelope["normalization"]["repaired_hash"]
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert (
        outcome.status_reason is not None
        and "unknown evidence ref" in outcome.status_reason
    )
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


# ────────────────────────── 审批边界（只授权 Phase 33 消费） ──────────────────────────


async def test_phase32_approval_authorizes_phase33_consumption_only(
    api_client, runtime_factory, migrated_postgres: str
):
    """`scene_spec:approve` 是服务端显式、append-only 的状态迁移（D-32-04）：
    确定性服务经 API 持久化 spec/prompt candidate → 用户 review/approve →
    review_state=approved（Phase 33 消费输入）；Canon / Visual Bible / spec 域行
    零改写；Agent 不能铸造 ApprovalRequest。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"appr_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['token']}"}
    snapshot_hash = _snapshot_hash(seed)
    vb_snapshot_hash = _visual_bible_snapshot_hash(seed)
    vb = await _create_and_approve_visual_bible(
        client, seed, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        seed,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    base = f"/api/novels/{seed['novel_id']}"

    created = await client.post(
        f"{base}/scene-specs",
        json=_spec_preview_payload(
            frozen, spec_key="spec-approve", vb_version_id=vb["id"]
        ),
        headers=headers,
    )
    assert created.status_code == 201, created.text
    spec_id = created.json()["spec"]["id"]
    assert created.json()["spec"]["review_state"] == "candidate"

    prompt_created = await client.post(
        f"{base}/prompt-revisions",
        json={"spec_id": spec_id, "prompt_key": "prompt-approve"},
        headers=headers,
    )
    assert prompt_created.status_code == 201, prompt_created.text
    revision_id = prompt_created.json()["revision"]["id"]
    assert prompt_created.json()["revision"]["review_state"] == "candidate"

    approved = await client.post(
        f"{base}/prompt-revisions/{revision_id}/review",
        json={
            "event_key": f"approve-{uuid.uuid4().hex[:16]}",
            "action": "approve",
            "actor_source": "human",
            "actor": "owner",
            "reason": "approve compiled prompt for Phase 33 consumption",
            "from_review_state": "candidate",
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["revision"]["review_state"] == "approved"

    # 审批只标记 Phase 33 输入：scene spec 域行仍为 candidate，无 Canon/VB 改写。
    spec_detail = await client.get(f"{base}/scene-specs/{spec_id}", headers=headers)
    assert spec_detail.status_code == 200
    assert spec_detail.json()["spec"]["review_state"] == "candidate"

    async with factory() as session:
        chapter_row = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == seed["chapter_ids"][0])
        )
        vb_count = await session.scalar(
            select(func.count())
            .select_from(VisualBibleVersionRow)
            .where(VisualBibleVersionRow.owner_id == seed["owner_id"])
        )
        approvals = await session.scalar(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.owner_id == seed["owner_id"])
        )
    assert chapter_row is not None and chapter_row.content == CH1
    assert int(vb_count or 0) == 1  # Visual Bible 零改写（无新版本、无状态迁移）
    assert int(approvals or 0) == 0  # Agent 不能铸造 ApprovalRequest


# ────────────────────────── HTTP 端到端 ──────────────────────────


async def test_phase32_http_end_to_end_scene_spec_artifact(
    api_client, runtime_factory, migrated_postgres: str
):
    """HTTP 端到端：注册（API）→ 202 接受（per-run token）→ finalize →
    candidate SceneSpecArtifact + revision；Agent 不能铸造/授予 ApprovalRequest，
    review_state 只能由服务端 review 迁移。"""
    client, factory, sync_url = api_client
    seed = _seed_owner_novel(migrated_postgres, suffix=f"http_{uuid.uuid4().hex[:6]}")
    headers = {"Authorization": f"Bearer {seed['token']}"}

    resp = await client.post(
        "/api/agent/skills",
        json=_skill_contract(
            novel_id=seed["novel_id"], name="compile-scene-spec", tools=DEFAULT_TOOLS
        ).model_dump(),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    svid = resp.json()["id"]

    snapshot_hash = _snapshot_hash(seed)
    vb_snapshot_hash = _visual_bible_snapshot_hash(seed)
    vb = await _create_and_approve_visual_bible(
        client, seed, headers, snapshot_hash=vb_snapshot_hash
    )
    frozen = await _freeze_key_scene_set(
        client,
        seed,
        headers,
        snapshot_hash=snapshot_hash,
        vb_version_id=vb["id"],
        vb_manifest_hash=vb["manifest_hash"],
    )
    ctx: dict[str, Any] = {
        **seed,
        "skill_version_id": svid,
        "snapshot_hash": snapshot_hash,
        "vb": vb,
        "frozen": frozen,
        "evidence_keys": list(frozen["evidence_keys"]),
        "spec_key": "spec-http",
    }
    contracts = await _compile_domain_contracts(runtime_factory, ctx=ctx)
    ctx["contracts"] = contracts
    run_input = {
        "novel_id": seed["novel_id"],
        "question": "请为这本小说编译场景规格。",
        "branch": None,
        "spec_key": "spec-http",
        "candidate_set_id": frozen["set_id"],
        "candidate_key": frozen["candidate_key"],
        "visual_bible_version_id": vb["id"],
        "source_snapshot_id": "ss-main",
        "revision_number": 1,
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

    ctx["input_hash"] = run_hash
    envelope = _build_envelope(ctx)
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
                "calls": 2,
                "input_tokens": 400,
                "output_tokens": 200,
                "cost_usd": "0.0008",
            },
            "frozen_manifest": {"evidence_refs": list(frozen["evidence_keys"])},
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
    assert artifact.type == "scene_spec"
    assert int(approvals or 0) == 0
    assert artifact.status != "published"
