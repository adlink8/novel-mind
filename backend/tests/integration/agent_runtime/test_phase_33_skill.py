"""Phase 33-05 集成测试：SkillRun → Tool → Artifact → Validator → Phase 34 handoff 边界证明。

证明 Phase 33 确定性插图域能力（D-33-01..D-33-04 / REQ-VIS-04 + REQ-AGENT-02/03/04）
经版本化 illustrate-scene Skill 消费，IllustrationRevision 是唯一官方 Agent 输出，
且 Agent 不能绕过域/证据/审批/发布权威：

正向链：
  register（版本化 manifest：8 工具 allowlist = 7 只读 +
  generate_image_candidate action + 空 write_permissions + 空
  approval_required_for——Phase 33 无 ApprovalRequest/Publisher/published）→
  accept run（owner/novel/branch + input_hash 绑定）→ stub loop 调真实 facade
  工具（get_novel / generate_image_candidate 创建候选作业）→ durable worker
  产出候选 AssetRevision → consistency evaluator 产出 review signal → 确定性
  validator（evaluate_illustration_proposal_gate + 血缘重放）推进
  candidate → validated → proposal_ready → Phase 34 只读 handoff
  （build_proposal_ref → FrozenAssetRevisionView 只接受 proposal_ready）。

对抗路径（全部稳定 blocked/cancelled 且零官方写入）：
  未知工具注册、取消、wrong owner / skill_version / input_hash（lineage 血缘）、
  schema drift、approval bypass（envelope review_state 非 candidate）、
  wrong branch/fork、stale base revision、forbidden Tool/action、
  publisher/ApprovalRequest 注入、Original 突变。FastAPI 与 Phase 33 确定性
  validator 保留 permission / evidence / state-transition / publication 权威。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, undefer
from sqlalchemy.pool import NullPool

from app.models import Chapter, Novel, User
from app.models.agent_runtime import (
    ApprovalRequest,
    Artifact,
    ArtifactRevision,
    SkillRegistry,
    SkillRun,
    SkillVersion,
)
from app.models.illustration import AssetRevision, ConsistencyReport
from app.models.illustration_job import (
    IllustrationAttempt,
    IllustrationBudgetReservation,
    IllustrationJob,
    IllustrationReviewEvent,
)
from app.models.prompt_revision import PromptRevision
from app.models.scene_spec import SceneSpecVersion
from app.models.visual_bible import VisualBibleVersion
from app.schemas.agent_runtime import SkillVersionRegister
from app.schemas.illustration import (
    FrozenAssetRevisionView,
    IllustrationActorSource,
    IllustrationApprovalState,
    IllustrationGateError,
    IllustrationReviewAction,
    IllustrationReviewEventInput,
)
from app.services.agent_runtime.artifacts import content_hash_of
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
from app.services.illustrations.consistency import (
    CandidateConsistencyEvidence,
    ConsistencyEvaluator,
    ConsistencyReportService,
    mock_consistency_fixture_registry,
)
from app.services.illustrations.gateway import (
    IllustrationGateway,
    MockIllustrationTransport,
)
from app.services.illustrations.review import (
    IllustrationReviewService,
    build_proposal_ref,
    evaluate_illustration_proposal_gate,
)
from app.services.illustrations.storage import AssetStorage
from app.services.illustrations.worker import (
    IllustrationWorkerRuntime,
    run_illustration_worker,
)
from app.services.key_scenes.boundaries import SceneBoundaryService
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

# Phase 33 编排 allowlist：7 个只读域工具 + 1 个候选生成 action 工具。
DEFAULT_TOOLS = [
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "generate_image_candidate",
]

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
        "read_permissions": [
            "canon",
            "visual_bible",
            "key_scene",
            "scene_spec",
            "prompt_revision",
            "illustration",
        ],
        "write_permissions": [],
        "forbidden_spaces": [
            "canon:original",
            "illustration:write",
            "illustration:publish",
            "approval_request",
            "publisher",
        ],
        "budget": {
            "max_calls": 40,
            "max_input_tokens": 40_000,
            "max_output_tokens": 12_000,
            "max_cost_usd": "4.00",
        },
        # Phase 33 无 ApprovalRequest / Publisher / published（Phase 34 拥有）。
        "approval_required_for": [],
        "input_schema": {
            "type": "object",
            "properties": {
                "novel_id": {"type": "integer"},
                "prompt_revision_id": {"type": "integer"},
                "job_key": {"type": "string"},
            },
            "required": ["novel_id", "prompt_revision_id", "job_key"],
        },
        "output_schema": {
            "type": "object",
            "properties": {"type": {"const": "illustration_revision"}},
        },
    }
    base.update(overrides)
    return SkillVersionRegister.model_validate(base)


def _seed_owner_novel(sync_url: str, *, suffix: str) -> dict[str, Any]:
    """同步播种 owner + 小说 + 三章正文（与 test_phase_32_skill.py 同口径）。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"p33_{suffix}",
            email=f"p33_{suffix}@example.com",
            hashed_password="hash",
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"P33 Novel {suffix}",
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
        }
    engine.dispose()
    return data


async def _source_snapshot_hash(factory, *, owner_id: int, novel_id: int) -> str:
    async with factory() as session:
        snapshot_hash, _ = await SceneBoundaryService(session).load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        return snapshot_hash


async def _make_prompt_chain(
    factory,
    ids: dict[str, Any],
    *,
    vb_hash: str | None = None,
    snapshot_hash: str | None = None,
) -> dict[str, Any]:
    """ORB 播种：已批准 Visual Bible + SceneSpec + PromptRevision（非 stale）。

    与 tests/integration/illustrations/test_generation.py 的链同口径：spec/prompt
    冻结同一个 Visual Bible manifest hash 与当前 source snapshot，prompt 因此
    非 stale——generation gate（check_generation_prompt_gate）只接受 approved +
    非 stale 的 PromptRevision（D-33-01）。
    """
    vb_hash = vb_hash or _sha256(f"vb-{uuid.uuid4().hex}")
    snapshot_hash = snapshot_hash or await _source_snapshot_hash(
        factory, owner_id=ids["owner_id"], novel_id=ids["novel_id"]
    )
    owner_id, novel_id = ids["owner_id"], ids["novel_id"]

    async with factory() as session:
        vb = VisualBibleVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            version_key=f"vb-{vb_hash[:12]}",
            revision_number=1,
            source_snapshot_id="ss-1",
            source_snapshot_hash=snapshot_hash,
            cutoff_chapter=3,
            review_state="approved",
            schema_version="visual-bible.v1",
            schema_hash=HEX64,
            policy_hash=HEX64,
            manifest_hash=vb_hash,
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=_sha256(f"vb-{vb_hash}"),
            projection_hash=HEX64,
        )
        session.add(vb)
        await session.flush()

        spec = SceneSpecVersion(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=f"spec-{vb_hash[:12]}",
            revision_number=1,
            scene_candidate_id=None,
            scene_candidate_hash=HEX64,
            visual_bible_revision_id=vb.id,
            visual_bible_revision_hash=vb_hash,
            source_snapshot_id="ss-1",
            source_snapshot_hash=snapshot_hash,
            cutoff_chapter=3,
            review_state="approved",
            schema_version="scene-spec.v1",
            schema_hash=HEX64,
            compiler_id="mock-compiler",
            compiler_version="1.0.0",
            policy_hash=HEX64,
            content_hash="3" * 64,
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=_sha256(f"spec-{vb_hash}"),
            projection_hash=HEX64,
        )
        session.add(spec)
        await session.flush()

        prompt = PromptRevision(
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_key=f"prompt-{vb_hash[:12]}",
            revision_number=1,
            parent_prompt_revision_id=None,
            scene_spec_id=spec.id,
            scene_spec_hash="3" * 64,
            visual_bible_revision_id=vb.id,
            visual_bible_revision_hash=vb_hash,
            source_snapshot_id="ss-1",
            source_snapshot_hash=snapshot_hash,
            cutoff_chapter=3,
            review_state="approved",
            schema_version="prompt-revision.v1",
            schema_hash=HEX64,
            prompt_schema_hash=HEX64,
            compiler_version="1.0.0",
            adapter_id="mock-provider",
            adapter_version="1.0.0",
            config_hash=HEX64,
            input_hash="7" * 64,
            prompt_hash="5" * 64,
            sections={},
            negative_constraints=[],
            uncertainties=[],
            prompt_text="A cinematic wide shot of Ayla in the northern keep.",
            redacted_preview=None,
            canonical_payload={},
            canonical_payload_hash=HEX64,
            idempotency_key=_sha256(f"prompt-{vb_hash}"),
            projection_hash=HEX64,
        )
        session.add(prompt)
        await session.flush()
        await session.commit()
        return {
            "vb": vb,
            "vb_id": vb.id,
            "vb_hash": vb_hash,
            "spec": spec,
            "spec_id": spec.id,
            "prompt": prompt,
            "prompt_id": prompt.id,
            "snapshot_hash": snapshot_hash,
        }


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
        source_versions=dict(envelope.get("source_versions") or {}),
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


async def _count_domain_review_events(factory, *, asset_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(IllustrationReviewEvent)
                .where(IllustrationReviewEvent.asset_revision_id == asset_id)
            )
            or 0
        )


async def _assert_zero_writes(factory, *, run_id: int) -> None:
    assert await _count(factory, Artifact, run_id=run_id) == 0
    assert await _count_revisions(factory, run_id=run_id) == 0
    assert await _count_approvals(factory, run_id=run_id) == 0


def _strip_trail(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k != "normalization"}


def _evidence_payload(
    *,
    content: str,
    find_text: str,
    evidence_key: str,
    chapter_id: int,
    chapter_number: int,
) -> dict[str, Any]:
    start = content.find(find_text)
    assert start >= 0, f"{find_text!r} not found in chapter"
    end = start + len(find_text)
    return {
        "evidence_key": evidence_key,
        "chapter_id": chapter_id,
        "chapter_number": chapter_number,
        "source_start": start,
        "source_end": end,
        "content_hash": _sha256(content[start:end]),
        "excerpt": content[start:end],
    }


async def _run_tool_generate_candidate(
    factory,
    *,
    ids: dict[str, Any],
    chain: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """真实 facade action 工具：创建候选生成作业（服务端 generation gate）。"""
    async with factory() as session:
        novel = await session.get(Novel, ids["novel_id"])
        facade = ToolFacade()
        job_view = await facade.execute(
            "generate_image_candidate",
            db=session,
            novel=novel,
            owner_id=ids["owner_id"],
            params={
                "novel_id": ids["novel_id"],
                "prompt_revision_id": chain["prompt_id"],
                "job_key": "job-ill-main",
                **({} if params is None else params),
            },
        )
        await session.commit()
        return job_view


async def _run_worker_for_job(
    factory, tmp_path, *, job_id: int
) -> tuple[AssetRevision, str]:
    """durable worker 产出候选 AssetRevision（D-33-03）。"""
    storage = AssetStorage(tmp_path / "assets")
    runtime = IllustrationWorkerRuntime(
        sessions=factory,
        gateway=IllustrationGateway(MockIllustrationTransport(mode="success")),
        storage=storage,
    )
    await run_illustration_worker(job_id, runtime=runtime)
    async with factory() as session:
        asset = await session.scalar(
            select(AssetRevision).where(AssetRevision.job_id == job_id).limit(1)
        )
        assert asset is not None
        return asset, asset.id


async def _evaluate_consistency(
    factory, *, ids: dict[str, Any], asset: AssetRevision
) -> ConsistencyReport:
    """确定性 consistency evaluator 产出 review signal（D-33-04，evidence 非 canon）。"""
    fixtures = mock_consistency_fixture_registry()
    async with factory() as session:
        service = ConsistencyReportService(
            session, evaluator=ConsistencyEvaluator(fixtures)
        )
        report, _replayed = await service.evaluate(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            asset_revision_id=asset.id,
            report_key="arin:scene1",
            evidence=CandidateConsistencyEvidence(
                character_key="arin",
                scene_key="scene1",
                identity_attributes=(
                    "black_hair",
                    "amber_eyes",
                    "lean_build",
                    "scar_left_brow",
                ),
                style_attributes=("ink_painting", "warm_palette", "soft_lighting"),
                negative_constraints_present=(),
            ),
        )
        await session.commit()
        return report


async def _clear_rights(factory, *, asset_id: int) -> AssetRevision:
    """rights authority 清权（raw SQL：ORM append-only guard 有意禁止 in-place
    rights 变异；rights clearing 是 Phase 33 外部权威动作，见 test_review.py）。"""
    from sqlalchemy import text

    async with factory() as session:
        await session.execute(
            text("UPDATE asset_revisions SET rights_status = 'cleared' WHERE id = :id"),
            {"id": asset_id},
        )
        await session.commit()
        fresh = await session.scalar(
            select(AssetRevision).where(AssetRevision.id == asset_id)
        )
        assert fresh is not None and fresh.rights_status == "cleared"
        return fresh


def _build_envelope(
    ctx: dict[str, Any],
    *,
    wrong_owner: bool = False,
    wrong_version: bool = False,
    wrong_input_hash: bool = False,
    review_state: str = "candidate",
    branch: str | None | object = None,
    authority_space: str = "original",
    fork: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建携带 26-06 normalization trail 的 IllustrationRevision 信封。"""
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"] if not wrong_owner else ctx["owner_id"] + 999,
        "novel_id": ctx["novel_id"],
        "branch": ctx["branch"] if branch is None else branch,
        "producing_skill": "illustrate-scene",
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
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "generate_image_candidate", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    payload: dict[str, Any] = {
        "schema_version": "illustration-revision.v1",
        "artifact_kind": "illustration_revision",
        "revision_key": f"{ctx['job_key']}:rev1",
        "revision_number": 1,
        "asset_revision_id": ctx["asset_id"],
        "authority_space": authority_space,
        "fork": fork,
        "scene_spec_hash": ctx["chain"]["spec"].content_hash,
        "prompt_revision_id": ctx["chain"]["prompt_id"],
        "prompt_revision_hash": ctx["chain"]["prompt"].prompt_hash,
        "visual_bible_revision_id": ctx["chain"]["vb_id"],
        "visual_bible_revision_hash": ctx["chain"]["vb_hash"],
        "source_snapshot_id": "ss-1",
        "source_snapshot_hash": ctx["snapshot_hash"],
        "cutoff_chapter": ctx["chain"]["prompt"].cutoff_chapter,
        "provider": ctx["asset"].provider,
        "provider_model": ctx["asset"].provider_model,
        "provider_request_id": ctx["asset"].provider_request_id,
        "config_hash": ctx["asset"].config_hash,
        "generator_version": ctx["asset"].provider_model,
        "rights_status": ctx["asset"].rights_status,
        "consistency_verdict": ctx["report"].verdict,
        "fixture_set_hash": ctx["report"].fixture_set_hash,
        "budget_settled_calls": 1,
        "budget_settled_cost_usd": "0.0448",
        "review_state": review_state,
    }
    envelope: dict[str, Any] = {
        "type": "illustration_revision",
        "schema_version": "illustration-revision.v1",
        **common,
        "illustration_revision": payload,
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


async def _append_revision(
    factory,
    *,
    artifact_id: int,
    owner_id: int,
    novel_id: int,
    parent_revision_id: int,
    content: dict[str, Any],
) -> int:
    """不可变修订追加：only Phase 33 确定性 validator 用它推进 review_state。"""
    async with factory() as session:
        parent = await session.get(ArtifactRevision, parent_revision_id)
        assert parent is not None
        revision = ArtifactRevision(
            artifact_id=artifact_id,
            owner_id=owner_id,
            novel_id=novel_id,
            revision_no=parent.revision_no + 1,
            content_hash=content_hash_of(content),
            parent_revision_id=parent_revision_id,
            evidence_refs=list(content.get("evidence_refs") or []),
            content=content,
        )
        session.add(revision)
        await session.flush()
        artifact = await session.get(Artifact, artifact_id)
        assert artifact is not None
        artifact.current_revision_id = revision.id
        await session.commit()
        return revision.id


async def _load_artifact_context(factory, *, run_id: int) -> dict[str, Any]:
    """读取 run 的 artifact + 当前 revision + run 行（校验器输入）。"""
    async with factory() as session:
        artifact = await session.scalar(
            select(Artifact).where(Artifact.run_id == run_id)
        )
        assert artifact is not None
        revision = await session.get(ArtifactRevision, artifact.current_revision_id)
        run_row = await session.get(SkillRun, run_id)
        assert revision is not None and run_row is not None
        return {
            "artifact": artifact,
            "revision": revision,
            "run": run_row,
        }


async def _phase33_validate(
    factory,
    *,
    ctx: dict[str, Any],
    run_id: int,
    to_state: str,
) -> int:
    """Phase 33 确定性 validator（唯一推进 review_state 的 authority）。

    - 只允许 candidate → validated → proposal_ready 前向迁移；
    - validated：确定性 budget/rights/fidelity/consistency gate
      （evaluate_illustration_proposal_gate + 血缘重放）必须通过；
    - proposal_ready：域 AssetRevision 必须先经显式 review approve（服务端
      IllustrationReviewService）到达 proposal_ready 且 rights cleared；
    - 任何非法迁移 / stale base / wrong scope → IllustrationGateError，零写入。
    返回新修订 ID。
    """
    state_order = {"candidate": 0, "validated": 1, "proposal_ready": 2}
    async with factory() as session:
        artifact = await session.scalar(
            select(Artifact).where(Artifact.run_id == run_id)
        )
        assert artifact is not None
        revision = await session.get(ArtifactRevision, artifact.current_revision_id)
        assert revision is not None
        current_state = revision.content["illustration_revision"]["review_state"]
        if current_state not in state_order or to_state not in state_order:
            raise IllustrationGateError("unknown illustration revision state")
        if state_order[to_state] != state_order[current_state] + 1:
            raise IllustrationGateError(
                f"illegal state transition {current_state!r} -> {to_state!r} "
                "(candidate -> validated -> proposal_ready only)"
            )
        # scope：owner/novel/branch 必须与 run 血缘一致。
        run_row = await session.get(SkillRun, run_id)
        assert run_row is not None
        content = revision.content
        if content["owner_id"] != run_row.owner_id or content["novel_id"] != run_row.novel_id:
            raise IllustrationGateError("illustration revision scope mismatch")
        if content.get("branch") != run_row.branch:
            raise IllustrationGateError("illustration revision branch mismatch")

        asset = await session.get(AssetRevision, ctx["asset_id"])
        assert asset is not None
        job = await session.get(IllustrationJob, asset.job_id)
        assert job is not None

        if to_state == "validated":
            # 确定性 gate：job succeeded + rights cleared + budget settled +
            # consistency report 可见 + 血缘完整。
            gate = evaluate_illustration_proposal_gate(
                job_status=job.status,
                rights_status=asset.rights_status,
                budget_settled=await _has_settled_budget(session, job.id),
                has_consistency_report=await _has_consistency_report(
                    session, ctx["owner_id"], ctx["novel_id"], asset.id
                ),
                lineage={
                    "scene_spec_hash": asset.scene_spec_hash,
                    "prompt_revision_hash": asset.prompt_revision_hash,
                    "visual_bible_revision_hash": asset.visual_bible_revision_hash,
                    "source_snapshot_id": asset.source_snapshot_id,
                    "source_snapshot_hash": asset.source_snapshot_hash,
                    "cutoff_chapter": asset.cutoff_chapter,
                    "config_hash": asset.config_hash,
                },
            )
            if not gate.ok:
                raise IllustrationGateError(
                    f"validated blocked by {gate.reason_code}: {gate.detail}"
                )
            if asset.scene_spec_hash != ctx["chain"]["spec"].content_hash:
                raise IllustrationGateError("scene-spec fidelity drift")
            report = await _latest_report(
                session, ctx["owner_id"], ctx["novel_id"], asset.id
            )
            if report is None or report.verdict == "fail":
                raise IllustrationGateError("visual consistency gate failed")
        elif to_state == "proposal_ready":
            # 必须先经显式 review approve 到达域 proposal_ready（服务端权威）。
            if asset.approval_state != IllustrationApprovalState.PROPOSAL_READY.value:
                raise IllustrationGateError(
                    "proposal_ready requires the explicit domain review approve first"
                )
            if asset.rights_status != "cleared":
                raise IllustrationGateError("proposal_ready requires cleared rights")
            gate = evaluate_illustration_proposal_gate(
                job_status=job.status,
                rights_status=asset.rights_status,
                budget_settled=await _has_settled_budget(session, job.id),
                has_consistency_report=await _has_consistency_report(
                    session, ctx["owner_id"], ctx["novel_id"], asset.id
                ),
                lineage={
                    "scene_spec_hash": asset.scene_spec_hash,
                    "prompt_revision_hash": asset.prompt_revision_hash,
                    "visual_bible_revision_hash": asset.visual_bible_revision_hash,
                    "source_snapshot_id": asset.source_snapshot_id,
                    "source_snapshot_hash": asset.source_snapshot_hash,
                    "cutoff_chapter": asset.cutoff_chapter,
                    "config_hash": asset.config_hash,
                },
            )
            if not gate.ok:
                raise IllustrationGateError(
                    f"proposal_ready blocked by {gate.reason_code}: {gate.detail}"
                )

    updated = dict(content)
    updated["illustration_revision"] = {
        **dict(updated["illustration_revision"]),
        "review_state": to_state,
    }
    return await _append_revision(
        factory,
        artifact_id=artifact.id,
        owner_id=ctx["owner_id"],
        novel_id=ctx["novel_id"],
        parent_revision_id=revision.id,
        content=updated,
    )


async def _has_settled_budget(session: AsyncSession, job_id: int) -> bool:
    row = await session.scalar(
        select(IllustrationBudgetReservation.id)
        .where(
            IllustrationBudgetReservation.reservation_key.like(f"job:{job_id}:%"),
            IllustrationBudgetReservation.status == "settled",
        )
        .limit(1)
    )
    return row is not None


async def _has_consistency_report(
    session: AsyncSession, owner_id: int, novel_id: int, asset_revision_id: int
) -> bool:
    row = await session.scalar(
        select(ConsistencyReport.id)
        .where(
            ConsistencyReport.owner_id == owner_id,
            ConsistencyReport.novel_id == novel_id,
            ConsistencyReport.asset_revision_id == asset_revision_id,
        )
        .limit(1)
    )
    return row is not None


async def _latest_report(
    session: AsyncSession, owner_id: int, novel_id: int, asset_revision_id: int
) -> ConsistencyReport | None:
    return await session.scalar(
        select(ConsistencyReport)
        .where(
            ConsistencyReport.owner_id == owner_id,
            ConsistencyReport.novel_id == novel_id,
            ConsistencyReport.asset_revision_id == asset_revision_id,
        )
        .order_by(ConsistencyReport.id.desc())
        .limit(1)
    )


async def _approve_domain_asset(
    factory, *, ids: dict[str, Any], asset_id: int
) -> None:
    """显式服务端 review approve：proposal gate 通过 → 域 approval_state 前移。"""
    async with factory() as session:
        service = IllustrationReviewService(session)
        await service.append_event(
            owner_id=ids["owner_id"],
            novel_id=ids["novel_id"],
            event=IllustrationReviewEventInput(
                owner_id=ids["owner_id"],
                novel_id=ids["novel_id"],
                asset_revision_id=asset_id,
                event_key=f"approve-{uuid.uuid4().hex[:8]}",
                action=IllustrationReviewAction.APPROVE,
                actor_source=IllustrationActorSource.HUMAN,
                actor="test-reviewer",
                reason="candidate reviewed and approved for Phase 34 handoff",
                from_approval_state=IllustrationApprovalState.CANDIDATE,
            ),
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


# ────────────────────────── setup helper ──────────────────────────


async def _set_up(
    runtime_factory, sync_url: str, tmp_path, *, suffix: str, branch: str | None = None
) -> dict[str, Any]:
    """seed owner/novel + 已批准 VB/SceneSpec/Prompt 链 + 注册 skill + 候选作业
    + worker 产出 AssetRevision + consistency evidence + rights cleared。"""
    ids = _seed_owner_novel(sync_url, suffix=suffix)
    snapshot_hash = await _source_snapshot_hash(
        runtime_factory, owner_id=ids["owner_id"], novel_id=ids["novel_id"]
    )
    chain = await _make_prompt_chain(
        runtime_factory, ids, snapshot_hash=snapshot_hash
    )
    svid = await _register_skill(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        contract=_skill_contract(
            novel_id=ids["novel_id"], name="illustrate-scene", tools=DEFAULT_TOOLS
        ),
    )
    job_view = await _run_tool_generate_candidate(
        runtime_factory,
        ids=ids,
        chain=chain,
        params={"job_key": "job-ill-main"},
    )
    job_id = job_view["id"]
    asset, asset_id = await _run_worker_for_job(
        runtime_factory, tmp_path, job_id=job_id
    )
    report = await _evaluate_consistency(runtime_factory, ids=ids, asset=asset)
    # rights 权威清权后重新读取资产（ctx 必须携带 cleared 状态）。
    asset = await _clear_rights(runtime_factory, asset_id=asset_id)

    evidence_keys = [
        _evidence_payload(
            content=ids["contents"][0],
            find_text="braided amber hair and green eyes",
            evidence_key="ev-ayla-hair",
            chapter_id=ids["chapter_ids"][0],
            chapter_number=1,
        )["evidence_key"],
    ]
    ctx: dict[str, Any] = {
        **ids,
        "chain": chain,
        "skill_version_id": svid,
        "job_id": job_id,
        "asset": asset,
        "asset_id": asset_id,
        "report": report,
        "evidence_keys": evidence_keys,
        "snapshot_hash": snapshot_hash,
        "branch": branch,
        "job_key": "job-ill-main",
    }
    run_input = {
        "novel_id": ids["novel_id"],
        "branch": branch,
        "scene_spec_revision_id": chain["spec_id"],
        "prompt_revision_id": chain["prompt_id"],
        "visual_bible_version_id": chain["vb_id"],
        "source_snapshot_id": "ss-1",
        "job_key": "job-ill-main",
        "provider": "mock",
        "model": "mock-img-v1",
    }
    ctx["run_input"] = run_input
    ctx["input_hash"] = canonical_input_hash(run_input)
    ctx["run_id"] = await _create_run(
        runtime_factory,
        owner_id=ids["owner_id"],
        novel_id=ids["novel_id"],
        skill_version_id=svid,
        input_hash=ctx["input_hash"],
        input_data=run_input,
        branch=branch,
    )
    return ctx


# ────────────────────────── Task 1：版本化 manifest 注册 ──────────────────────────


async def test_phase33_versioned_skill_registers(
    runtime_factory, migrated_postgres: str
):
    """版本化 illustrate-scene manifest 注册成功：8 工具 allowlist（7 只读 +
    generate_image_candidate action）+ 零写权限 + 空 approval_required_for
    （Phase 33 无 ApprovalRequest/Publisher/published）。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"reg_{uuid.uuid4().hex[:6]}")
    svid = await _register_skill(
        runtime_factory,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        contract=_skill_contract(
            novel_id=seed["novel_id"], name="illustrate-scene", tools=DEFAULT_TOOLS
        ),
    )
    async with runtime_factory() as session:
        version = await session.get(SkillVersion, svid)
        assert version is not None
        assert version.name == "illustrate-scene"
        assert version.version == "1.0.0"
        assert set(version.allowed_tools) == set(DEFAULT_TOOLS)
        assert "generate_image_candidate" in version.allowed_tools
        assert version.write_permissions == []
        assert version.approval_required_for == []
        assert version.forbidden_spaces == [
            "canon:original",
            "illustration:write",
            "illustration:publish",
            "approval_request",
            "publisher",
        ]
        assert "illustration" in version.read_permissions
        assert int(version.budget["max_calls"]) == 40


async def test_phase33_unknown_tool_registration_rejected(
    runtime_factory, migrated_postgres: str
):
    """allowed_tools 含未知工具（publish_illustration）→ 注册拒绝，零 active 行。"""
    seed = _seed_owner_novel(migrated_postgres, suffix=f"unk_{uuid.uuid4().hex[:6]}")
    contract = _skill_contract(
        novel_id=seed["novel_id"],
        name="illustrate-scene",
        tools=list(DEFAULT_TOOLS) + ["publish_illustration"],
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


# ────────────────────────── Task 2：端到端 Runtime→Tool→Artifact→Validator→Phase 34 ──────────────────────────


async def test_phase33_happy_path_proposal_ready(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """正向链：真实 facade action 工具创建候选作业 → worker 产出 AssetRevision →
    一致性 evidence → finalize 写入 candidate IllustrationRevision → 确定性
    validator 推进 candidate → validated → proposal_ready → Phase 34 只读
    handoff 接受。零 ApprovalRequest、零 publisher、零 published、零域写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"ok_{uuid.uuid4().hex[:6]}",
    )
    run_id = ctx["run_id"]

    # stub agent loop：真实调用 facade 只读工具 + action 工具。
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        novel_view = await facade.execute(
            "get_novel",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params={"novel_id": ctx["novel_id"]},
        )
        assert novel_view is not None and novel_view["id"] == ctx["novel_id"]

    frozen_manifest = {
        "evidence_refs": ctx["evidence_keys"],
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
    assert await _count_approvals(runtime_factory, run_id=run_id) == 0

    async with runtime_factory() as session:
        artifact = await session.get(Artifact, outcome.artifact_id)
        revision = await session.get(ArtifactRevision, outcome.artifact_revision_id)
        run_row = await session.get(SkillRun, run_id)
        job = await session.get(IllustrationJob, ctx["job_id"])
        asset = await session.get(AssetRevision, ctx["asset_id"])
        chapter_row = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_ids"][0])
        )
    assert artifact is not None and artifact.type == "illustration_revision"
    assert artifact.schema_version == "illustration-revision.v1"
    assert artifact.status == "candidate"
    assert run_row is not None and run_row.status == "completed"
    assert job is not None and job.status == "succeeded"
    assert asset is not None and asset.approval_state == "candidate"

    content = revision.content
    # 服务器重放：剥离 trail 后重算 repaired_hash 必须一致。
    assert (
        canonical_content_hash(_strip_trail(content))
        == content["normalization"]["repaired_hash"]
    )
    # 血缘绑定。
    assert content["owner_id"] == ctx["owner_id"]
    assert content["novel_id"] == ctx["novel_id"]
    assert content["skill_version_id"] == ctx["skill_version_id"]
    assert content["input_hash"] == ctx["input_hash"]
    assert content["evidence_refs"] == ctx["evidence_keys"]
    assert content["tool_runs"] == [
        {"tool_name": "get_novel", "calls": 1},
        {"tool_name": "generate_image_candidate", "calls": 1},
    ]
    payload = content["illustration_revision"]
    assert payload["review_state"] == "candidate"
    assert payload["authority_space"] == "original"
    assert payload["asset_revision_id"] == ctx["asset_id"]
    assert payload["scene_spec_hash"] == ctx["chain"]["spec"].content_hash
    assert payload["visual_bible_revision_hash"] == ctx["chain"]["vb_hash"]
    assert payload["source_snapshot_hash"] == ctx["snapshot_hash"]
    assert payload["rights_status"] == "cleared"
    assert payload["consistency_verdict"] == "pass"
    assert payload["budget_settled_calls"] == 1
    # official 信封未携带受保护合成字段 / 可变 Agent 状态。
    for forbidden in ("authority", "cutoff", "fork", "approval", "approval_state"):
        assert forbidden not in content
    # Original 零变更：章节正文未动、无 ApprovalRequest、无 published。
    assert chapter_row is not None and chapter_row.content == CH1
    assert job.status == "succeeded"
    assert "published" not in json_str(content).lower()

    # ── Phase 33 确定性 validator：candidate → validated ──
    validated_rev_id = await _phase33_validate(
        runtime_factory, ctx=ctx, run_id=run_id, to_state="validated"
    )
    async with runtime_factory() as session:
        validated_rev = await session.get(ArtifactRevision, validated_rev_id)
        validated_artifact = await session.get(Artifact, outcome.artifact_id)
    assert validated_rev is not None
    assert (
        validated_rev.content["illustration_revision"]["review_state"] == "validated"
    )
    assert validated_rev.parent_revision_id == outcome.artifact_revision_id
    assert validated_artifact is not None and validated_artifact.status == "candidate"

    # ── 显式服务端 review approve（域权威）→ proposal_ready ──
    await _approve_domain_asset(
        runtime_factory, ids=ctx, asset_id=ctx["asset_id"]
    )
    async with runtime_factory() as session:
        asset = await session.get(AssetRevision, ctx["asset_id"])
    assert asset is not None and asset.approval_state == "proposal_ready"
    assert await _count_domain_review_events(runtime_factory, asset_id=ctx["asset_id"]) == 1
    assert await _count_approvals(runtime_factory, run_id=run_id) == 0  # 非 ApprovalRequest

    # validator 推进 validated → proposal_ready（域 approve 已就位）。
    proposal_rev_id = await _phase33_validate(
        runtime_factory, ctx=ctx, run_id=run_id, to_state="proposal_ready"
    )
    async with runtime_factory() as session:
        proposal_rev = await session.get(ArtifactRevision, proposal_rev_id)
        revision_chain = list(
            (
                await session.scalars(
                    select(ArtifactRevision)
                    .where(ArtifactRevision.artifact_id == outcome.artifact_id)
                    .order_by(ArtifactRevision.revision_no)
                )
            ).all()
        )
    assert proposal_rev is not None
    assert (
        proposal_rev.content["illustration_revision"]["review_state"] == "proposal_ready"
    )
    # 唯一官方状态机：candidate → validated → proposal_ready。
    assert [
        rev.content["illustration_revision"]["review_state"] for rev in revision_chain
    ] == ["candidate", "validated", "proposal_ready"]
    assert await _count_approvals(runtime_factory, run_id=run_id) == 0
    assert await _count_revisions(runtime_factory, run_id=run_id) == 3

    # ── Phase 34 只读 handoff：只接受 proposal_ready + rights cleared ──
    async with runtime_factory() as session:
        asset = await session.get(AssetRevision, ctx["asset_id"])
        frozen = build_proposal_ref(asset)
    assert isinstance(frozen, FrozenAssetRevisionView)
    assert frozen.approval_state == IllustrationApprovalState.PROPOSAL_READY
    assert frozen.rights_status == "cleared"


async def test_phase33_phase34_handoff_rejects_candidate_and_validated(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """Phase 34 handoff fail-closed：candidate/validated 修订一律拒绝（只接受
    proposal_ready + rights cleared）。FrozenAssetRevisionView 的 model_validator
    把 IllustrationGateError（ValueError）包装为 pydantic ValidationError。"""
    from pydantic import ValidationError

    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"h34_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        asset = await session.get(AssetRevision, ctx["asset_id"])
        assert asset is not None
        # candidate（未 review）→ build_proposal_ref 拒绝（ValidationError）。
        assert asset.approval_state == IllustrationApprovalState.CANDIDATE
        with pytest.raises(ValidationError):
            build_proposal_ref(asset)


# ────────────────────────── 对抗路径（fail closed，零官方写入） ──────────────────────────


async def test_phase33_cancellation_no_write(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """取消 → cancelled，0 artifact/revision/ApprovalRequest（cancel-without-write）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
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


async def test_phase33_wrong_owner_lineage_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """envelope owner 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
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


async def test_phase33_wrong_skill_version_lineage_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """envelope skill_version_id 血缘与 run 不符 → blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
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


async def test_phase33_stale_input_hash_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """envelope input_hash 与 run 不符（stale）→ blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
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


async def test_phase33_forged_approval_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """approval bypass：envelope review_state 非 candidate（proposal_ready 伪造）
    → integrity blocked，零写入；Phase 33 无 ApprovalRequest。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"appr_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, review_state="proposal_ready")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "candidate" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase33_schema_drift_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """schema drift：illustration_revision.review_state 非法 / 缺血缘 → blocked，
    零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"drift_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx, review_state="bogus")
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase33_wrong_branch_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """wrong branch/fork：run 绑定 original 主线（branch=None），envelope 声称
    derivative 模式（branch + fork）→ integrity blocked，零写入。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"br_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(
        ctx, branch="deriv-branch", authority_space="derivative", fork="fork-1"
    )
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "failed"
    assert outcome.error_code == ERROR_CODE_FAILED_VALIDATION
    assert outcome.status_reason is not None and "branch" in outcome.status_reason
    await _assert_zero_writes(runtime_factory, run_id=ctx["run_id"])


async def test_phase33_stale_validator_transition_blocks(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """stale base revision：validator 跳过 validated 直接 proposal_ready →
    IllustrationGateError，零新修订（状态机仅前向）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"stale_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "completed"
    with pytest.raises(IllustrationGateError):
        await _phase33_validate(
            runtime_factory, ctx=ctx, run_id=ctx["run_id"], to_state="proposal_ready"
        )
    # 零新修订：仍只有 finalize 的首个 candidate 修订。
    assert await _count_revisions(runtime_factory, run_id=ctx["run_id"]) == 1
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase33_validator_blocks_before_domain_approve(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """validator 推进 proposal_ready 前必须先有域 review approve（服务端权威）——
    未 approve → IllustrationGateError，零新修订。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"gate_{uuid.uuid4().hex[:6]}",
    )
    envelope = _build_envelope(ctx)
    outcome = await _finalize(
        runtime_factory,
        run_id=ctx["run_id"],
        envelope=envelope,
        frozen_manifest={"evidence_refs": ctx["evidence_keys"]},
    )
    assert outcome.status == "completed"
    await _phase33_validate(runtime_factory, ctx=ctx, run_id=ctx["run_id"], to_state="validated")
    # 未调用域 review approve → 域 approval_state 仍是 candidate → proposal_ready 拒绝。
    async with runtime_factory() as session:
        asset = await session.get(AssetRevision, ctx["asset_id"])
        assert asset is not None and asset.approval_state == "candidate"
    with pytest.raises(IllustrationGateError):
        await _phase33_validate(
            runtime_factory, ctx=ctx, run_id=ctx["run_id"], to_state="proposal_ready"
        )
    assert await _count_revisions(runtime_factory, run_id=ctx["run_id"]) == 2


async def test_phase33_original_authority_untouched(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """Original 权威零变更：章节正文、无 ApprovalRequest、无 published、无域表写入
    （Agent 不直接写 illustration 域表——作业/资产由 worker/服务端产出）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"orig_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        chapter = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(Chapter.id == ctx["chapter_ids"][0])
        )
        approvals_total = await session.scalar(
            select(func.count()).select_from(ApprovalRequest)
        )
        vb_count = await session.scalar(
            select(func.count())
            .select_from(VisualBibleVersion)
            .where(VisualBibleVersion.owner_id == ctx["owner_id"])
        )
        spec_count = await session.scalar(
            select(func.count())
            .select_from(SceneSpecVersion)
            .where(SceneSpecVersion.owner_id == ctx["owner_id"])
        )
        prompt_count = await session.scalar(
            select(func.count())
            .select_from(PromptRevision)
            .where(PromptRevision.owner_id == ctx["owner_id"])
        )
    assert chapter is not None and chapter.content == CH1
    assert int(approvals_total or 0) == 0
    # 域只读：Agent 的 run 没有创建 VB/SceneSpec/Prompt（这些是 setup 播种的 1 行）。
    assert int(vb_count or 0) == 1
    assert int(spec_count or 0) == 1
    assert int(prompt_count or 0) == 1


async def test_phase33_forbidden_tool_never_publishes(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """forbidden Tool/action：generate_image_candidate 只创建候选作业（queued →
    succeeded），从不发布；作业信封不携带 published/publish 状态。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"tool_{uuid.uuid4().hex[:6]}",
    )
    async with runtime_factory() as session:
        job = await session.get(IllustrationJob, ctx["job_id"])
        attempts = list(
            (
                await session.scalars(
                    select(IllustrationAttempt)
                    .where(IllustrationAttempt.job_id == ctx["job_id"])
                )
            ).all()
        )
    assert job is not None and job.status == "succeeded"
    assert job.status_reason == "generated"
    assert len(attempts) == 1 and attempts[0].status == "succeeded"
    assert job.response_hash is not None
    # 零发布证据：Phase 33 没有 published 状态列 / publisher 调用。
    assert "published" not in str(job.status)
    assert await _count_approvals(runtime_factory, run_id=ctx["run_id"]) == 0


async def test_phase33_generate_tool_idempotent_replay(
    runtime_factory, migrated_postgres: str, tmp_path
):
    """generate_image_candidate 幂等：重复作业键/血缘 → 重放既有候选作业（一个
    作业、一个候选资产、一个 charge）。"""
    ctx = await _set_up(
        runtime_factory,
        migrated_postgres,
        tmp_path,
        suffix=f"idem_{uuid.uuid4().hex[:6]}",
    )
    job_view = await _run_tool_generate_candidate(
        runtime_factory,
        ids=ctx,
        chain=ctx["chain"],
        params={"job_key": "job-ill-main"},
    )
    assert job_view["id"] == ctx["job_id"]
    assert job_view["candidate_only"] is True
    async with runtime_factory() as session:
        jobs = (
            await session.scalars(
                select(IllustrationJob).where(
                    IllustrationJob.owner_id == ctx["owner_id"],
                    IllustrationJob.novel_id == ctx["novel_id"],
                )
            )
        ).all()
        assets = (
            await session.scalars(
                select(AssetRevision).where(
                    AssetRevision.owner_id == ctx["owner_id"],
                    AssetRevision.novel_id == ctx["novel_id"],
                )
            )
        ).all()
    assert len(jobs) == 1
    assert len(assets) == 1


def json_str(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
