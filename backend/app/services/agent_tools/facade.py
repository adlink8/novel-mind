"""
智能体工具门面（25.2-02 Domain Tool Contract / D-06 / D-07）。

本门面把 7 个只读工具暴露给 agent-service：
  get_novel / get_chapter / search_novel_text / get_timeline /
  get_relationships / get_clues / get_narrative_memory

设计原则（对齐 D-07「服务端强制，绝不放提示侧」）：
  1. **不重实现** owner / cutoff / budget 逻辑 —— 只复用现有服务入口
     （novel_service、resolve_chapter_cutoff、build_version_view、
     relationship_graph_query_service、build_clue_envelope、structure_query）。
  2. **门面新增的强制点**：
     - 冻结错误码映射（errors.py，唯一事实源）；
     - per-tool 64 KiB 字节上限（输出超限 → ``output_too_large``）；
     - per-tool ``asyncio.wait_for`` 30s 超时（→ ``timeout``）；
     - budget hook（fail closed：超预算在调用服务**之前**拦截 → ``budget_exceeded``）；
     - ``get_narrative_memory`` 响应带 ``release_status: "candidate"``（ADR-0002）。
  3. **``full_book`` 只从持久化的每本小说开关读取**
     （``novel.reading_progress["timeline_full_book"]``），绝不接受裸请求参数。
  4. 门面对既有领域**只读**（D-22）：不 import 任何领域写入/变异模块。Phase 33
     （33-05）新增唯一 action 工具 ``generate_image_candidate``——它只创建
     **候选**生成作业（服务端 generation gate + 确定性 idempotency key，
     D-33-01..D-33-03），绝不写 Canon / 域表 / ApprovalRequest / published
     状态；候选资产由 durable worker 产出，审批/发布属于 Phase 34。
     ``generate_image_candidate`` 的作业创建复用 illustrations 域确定性服务，
     不越出候选边界。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.models import Novel
from app.schemas.novel import ChapterResponse, NovelResponse
from app.schemas.relationship import RelationshipVersionSource
from app.schemas.timeline import (
    TimelineEnvelope,
    TimelineOrdering,
    TimelineVersionSource,
)
from app.services.agent_tools.errors import (
    AgentToolError,
    BeyondCutoffError,
    InvalidInputError,
    NotFoundError,
    OutputTooLargeError,
    ToolTimeoutError,
    UpstreamError,
)
from app.services.visual_bible.authority import (
    CandidateNotFoundError,
    list_versions as list_visual_bible_versions,
    load_version_view as load_visual_bible_version_view,
)
from app.services.clues.query import build_clue_envelope
from app.services.narrative_memory.structure_query import (
    StructureQueryError,
    list_versions,
    load_structure_tree,
)
from app.services.novel_service import novel_service
from app.services.queryplan.adapters import chapter_content_hash
from app.services.queryplan.contracts import leaf_evidence_key
from app.services.relationships.query import relationship_graph_query_service
from app.services.timeline.query import build_version_view, resolve_chapter_cutoff
from app.services.world_model.entity_queries import WorldEntityQueries
from app.services.world_model.event_queries import WorldModelEventQueries
from app.services.world_model.knowledge import EpistemicAspect, KnowledgeResultStatus
from app.services.world_model.knowledge_queries import KnowledgeQueries

logger = logging.getLogger(__name__)

# 冻结的 16 个域工具名（25.2-03 skill.yaml 的 allowed_tools 白名单镜像此表；
# 27-05 起加入 Phase 27 世界模型工具 get_events / get_character_state /
# get_character_knowledge / get_world_rules / get_evidence_span；31-04 起加入
# Phase 30 Visual Bible 只读工具 get_visual_bible；33-05 起加入 Phase 33
# 候选生成 action 工具 generate_image_candidate——它只创建候选生成作业，
# 绝不写 Canon / 域表 / ApprovalRequest / published 状态（D-33-01..D-33-03）；
# 34-05 起加入 Phase 34 锚点提议 action 工具 publish_illustration /
# attach_illustration_to_text——它们只创建候选 proposal + pending Web
# ApprovalRequest（D-11/D-15），确定性 publisher 拥有 approved publication；
# 35-05 起加入 Phase 35 canon fork 提议 action 工具 create_canon_fork——它只
# 创建候选 fork + pending Web ApprovalRequest（D-11/D-15），确定性 Fork
# materializer 拥有 approved fork 物化。
# 36-05 起加入 Phase 36 derivative 编辑提议 action 工具 apply_derivative_edit——
# 它只创建候选 DerivativeEditProposal + pending Web ApprovalRequest（D-11/D-15），
# 确定性 Revision Service（apply_agent_edit）拥有 approved proposal 应用。
# 37-05 起加入 Phase 37 derivative generation action 工具 allow_divergence /
# publish_derivative_revision——前者只为 blocked/needs_override 候选创建显式
# divergence override + pending Web ApprovalRequest；后者只在 allow_divergence
# approval 批准 + 完整 revalidation 通过后为同一候选创建**独立** publish
# ApprovalRequest（绑定相同 draft_hash + canon_delta_hash）。两者都绝不发布——
# 确定性 revision publisher（consume_publish_approval -> approve_override）拥有
# approved Fanfiction Canon 物化，绝不写 Original Canon。
# 38-05 起加入 Phase 38 branch-aware derivative visual action 工具
# publish_derivative_visual——它只为已存储 candidate asset 创建 pending Web
# ApprovalRequest（payload_hash 绑定候选冻结血缘：asset_id/content_hash/
# scene_spec_hash/divergence_manifest_hash/consistency_verdict/source_snapshot_hash/
# fork_id；blocked candidate / wrong owner/branch/fork → fail closed）。绝不发布——
# 确定性 review seam（review_candidate_asset -> apply_derivative_asset_review）拥有
# approved published asset 物化，绝不写 Original Visual Bible。
# 39-05 起加入 Phase 39 derivative export action 工具 approve_export /
# materialize_export——前者为已 finalize 候选 ExportPreparationArtifact 创建
# pending Web ApprovalRequest（payload_hash 绑定 artifact revision + 确定性
# preparation_hash；wrong owner/branch/fork/stale hash → fail closed）；后者是
# 确定性 materializer：只接受 approved artifact + preparation_hash 匹配的
# approve_export ApprovalRequest，把候选 artifact 推进为 approved 并产出可复现
# bundle（frozen manifest 复算），绝不写 Original Canon / 域表 / Artifact 状态 /
# bundle（download 只读）。
TOOL_NAMES: tuple[str, ...] = (
    "get_novel",
    "get_chapter",
    "search_novel_text",
    "get_timeline",
    "get_relationships",
    "get_clues",
    "get_narrative_memory",
    "get_events",
    "get_character_state",
    "get_character_knowledge",
    "get_world_rules",
    "get_evidence_span",
    "get_visual_bible",
    "generate_image_candidate",
    "publish_illustration",
    "attach_illustration_to_text",
    "create_canon_fork",
    "apply_derivative_edit",
    "allow_divergence",
    "publish_derivative_revision",
    "publish_derivative_visual",
    "approve_export",
    "materialize_export",
)

# per-tool 默认字节上限（agent-service 侧同样硬编码 64 KiB，见 RESEARCH Code Examples）。
DEFAULT_BYTE_CAP = 64 * 1024
# per-tool 默认超时（秒）。
DEFAULT_TOOL_TIMEOUT = 30.0

# 预算钩子类型：在服务执行前被调用；超预算应抛出 BudgetExceededError。
BudgetHook = Callable[[str, dict[str, Any]], Awaitable[None]]


async def default_budget_hook(tool_name: str, params: dict[str, Any]) -> None:
    """默认预算钩子：无策略配置时放行。

    25.2-03 skill runtime 会注入 per-run 的调用/Token 上限钩子
    （skill.yaml ``budget`` 字段，BudgetPolicy 语义），本门面只保证
    fail-closed 的拦截位置在服务调用**之前**。
    """


def _persisted_full_book(novel: Novel) -> bool:
    """从持久化的每本小说开关读取 full_book 授权（绝不接受裸请求参数）。"""
    return bool((novel.reading_progress or {}).get("timeline_full_book", False))


# ────────────────────────── 默认服务入口（按工具） ──────────────────────────


async def _default_get_novel(db, novel_id: int):
    return await novel_service.get_novel(db, novel_id)


async def _default_get_chapter(db, chapter_id: int):
    return await novel_service.get_chapter(db, chapter_id)


async def _default_search_novel_text(
    db,
    *,
    owner_id: int,
    novel_id: int,
    query: str,
    mode: str,
    top_k: int,
) -> Any:
    from app.services.knowledge_units.search import production_retrieval_strategy

    strategy = production_retrieval_strategy()
    outcome = await strategy.resolve_novel(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        domain_profile="fiction",
        query=query,
        mode=mode,
        top_k=top_k,
    )
    return {
        "results": outcome.rows,
        "resolved_mode": outcome.resolved_mode,
        "fallback_reason": outcome.fallback_reason,
    }


async def _default_get_timeline(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: TimelineVersionSource,
    ordering: TimelineOrdering,
    person: str | None,
    include_causal: bool,
    request_full_book: bool,
    chapter_start: int | None,
    chapter_end: int | None,
):
    return await build_version_view(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        ordering=ordering,
        person=person,
        include_causal=include_causal,
        request_full_book=request_full_book,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )


async def _default_get_relationships(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: RelationshipVersionSource,
    version_id: int | None,
    through_chapter: int | None,
    request_full_book: bool,
    character_id: int | None,
    relation_type: str | None,
    include_provisional: bool,
):
    return await relationship_graph_query_service.build_graph(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        version_id=version_id,
        through_chapter=through_chapter,
        request_full_book=request_full_book,
        character_id=character_id,
        relation_type=relation_type,
        include_provisional=include_provisional,
    )


async def _default_get_clues(
    db,
    *,
    novel: Novel,
    owner_id: int,
    request_full_book: bool,
    character_id: int | None,
    status_filter: str | None,
) -> dict[str, Any]:
    return await build_clue_envelope(
        db,
        novel=novel,
        owner_id=owner_id,
        request_full_book=request_full_book,
        character_id=character_id,
        status_filter=status_filter,
    )


async def _default_get_narrative_memory(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    view: str,
    through_chapter: int | None,
) -> Any:
    if view == "versions":
        return await list_versions(db, owner_id=owner_id, novel_id=novel_id)
    if view == "tree":
        if version_id is None:
            raise InvalidInputError("narrative_memory tree 视图需要 version_id")
        return await load_structure_tree(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            through_chapter=through_chapter,
        )
    raise InvalidInputError(f"不支持的 narrative_memory 视图: {view!r}")


# ────────────────────────── Phase 27 世界模型默认服务入口（27-05） ──────────────────────────


def _epistemic_answer_to_json(answer) -> dict[str, Any]:
    """把 EpistemicAnswer 序列化为 JSON 安全 payload（claims/evidence 是 pydantic）。"""
    return {
        "status": answer.status.value,
        "subject": answer.subject,
        "claims": [claim.model_dump(mode="json") for claim in answer.claims],
        "evidence": [ref.model_dump(mode="json") for ref in answer.evidence],
        "has_approval": answer.has_approval,
        "message": answer.message,
    }


def _merge_state_answers(
    *, subject: str, answers: list[Any], message: str
) -> dict[str, Any]:
    """合并 state/goal/motivation 三个 aspect 的查询结果（无编造，abstain 优先）。"""
    claims = tuple(claim for answer in answers for claim in answer.claims)
    evidence = tuple(ref for answer in answers for ref in answer.evidence)
    approved = any(answer.has_approval for answer in answers)
    if not claims:
        status = KnowledgeResultStatus.ABSTAINED
    elif approved:
        status = KnowledgeResultStatus.ANSWERED
    else:
        status = KnowledgeResultStatus.CANDIDATE_ONLY
    return {
        "status": status.value,
        "subject": subject,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "evidence": [ref.model_dump(mode="json") for ref in evidence],
        "has_approval": approved,
        "message": message,
    }


async def _default_get_events(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any] | None:
    """世界模型事件/因果投影（D-05 cutoff 过滤；无投影 → None → 404-hide）。"""
    projection = await WorldModelEventQueries(db).query_cutoff_projection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        cutoff=cutoff,
    )
    return projection.model_dump(mode="json") if projection is not None else None


async def _default_get_character_state(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色状态/目标/动机（aspect ∈ state/goal/motivation 合并，D-05）。"""
    queries = KnowledgeQueries(db)
    answers = [
        await queries.query_character_knowledge(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            subject=subject,
            cutoff=cutoff,
            pov=pov,
            aspect=aspect,
        )
        for aspect in (EpistemicAspect.STATE, EpistemicAspect.GOAL, EpistemicAspect.MOTIVATION)
    ]
    return _merge_state_answers(
        subject=subject,
        answers=answers,
        message="character state merged across state/goal/motivation (D-05)",
    )


async def _default_get_character_knowledge(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    subject: str,
    cutoff: int,
    pov: str | None,
) -> dict[str, Any]:
    """角色知识（aspect=knowledge；mistaken/hidden 保持显式标签，D-05）。"""
    answer = await KnowledgeQueries(db).query_character_knowledge(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        subject=subject,
        cutoff=cutoff,
        pov=pov,
        aspect=EpistemicAspect.KNOWLEDGE,
    )
    return _epistemic_answer_to_json(answer)


async def _default_get_world_rules(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    cutoff: int,
) -> dict[str, Any]:
    """世界规则与规则例外（D-05 cutoff 过滤；例外是 first-class，D-04）。"""
    queries = WorldEntityQueries(db)
    rules = [
        rule.model_dump(mode="json")
        for rule in await queries.query_rules(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if rule.disclosure_cutoff <= cutoff
    ]
    exceptions = [
        exc.model_dump(mode="json")
        for exc in await queries.query_rule_exceptions(
            owner_id=owner_id, novel_id=novel_id, version_id=version_id
        )
        if exc.disclosure_cutoff <= cutoff
    ]
    return {"rules": rules, "exceptions": exceptions}


async def _default_get_evidence_span(
    db,
    *,
    chapter_id: int,
    source_start: int,
    source_end: int,
    content_hash: str,
) -> dict[str, Any] | None:
    """按 chapter+offsets+content_hash 物化 leaf 证据跨度（D-07/D-08）。

    chapter 缺失 → None（404-hide）；offsets 非法 / hash 与原文切片不匹配 →
    InvalidInputError（fail closed，绝不返回错误切片）。
    """
    chapter = await novel_service.get_chapter(db, chapter_id)
    if chapter is None:
        return None
    content = chapter.content
    if source_start < 0 or source_end > len(content) or source_end <= source_start:
        raise InvalidInputError(
            f"offsets [{source_start},{source_end}) 不是合法 half-open 区间"
        )
    excerpt = content[source_start:source_end]
    if chapter_content_hash(excerpt) != content_hash:
        raise InvalidInputError("evidence content hash 与原文切片不匹配")
    return {
        "evidence_key": leaf_evidence_key(
            chapter_id=chapter_id,
            source_start=source_start,
            source_end=source_end,
            content_hash=content_hash,
        ),
        "chapter_id": chapter_id,
        "chapter_number": chapter.chapter_number,
        "novel_id": chapter.novel_id,
        "source_start": source_start,
        "source_end": source_end,
        "content_hash": content_hash,
        "excerpt": excerpt,
    }


async def _default_get_visual_bible(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    approved_only: bool,
) -> dict[str, Any] | None:
    """按 owner/novel 范围读取 Visual Bible 候选版本视图（31-04 只读工具）。

    显式 ``version_id`` → 单个候选信封（owner/novel 越界 → None，404-hide）；
    缺省 → 版本列表。``approved_only=True`` 只保留 review_state=approved 的
    版本（D-30-04 approval 权威仍只在 FastAPI review API，本工具只读）。
    """
    if version_id is not None:
        try:
            view = await load_visual_bible_version_view(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                version_id=version_id,
            )
        except CandidateNotFoundError:
            return None
        return view.model_dump(mode="json")
    views = await list_visual_bible_versions(
        db, owner_id=owner_id, novel_id=novel_id
    )
    if approved_only:
        views = [view for view in views if view.review_state == "approved"]
    return {
        "items": [view.model_dump(mode="json") for view in views],
        "total": len(views),
    }


async def _default_generate_image_candidate(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 33 action 工具默认服务：创建**一个**候选生成作业（D-33-01..D-33-03）。

    只创建 durable idempotent job（queued）——服务端 generation gate 只接受
    **已批准且非 stale** 的 PromptRevision（``check_generation_prompt_gate``），
    作业 idempotency key 从 owner/novel/SceneSpec/prompt/model/config 血缘
    确定性重放。绝不写 Canon / 域表 / ApprovalRequest / published 状态；候选
    资产由 durable worker 在作业成功时产出，审批/发布属于 Phase 34。
    """
    from app.models.illustration_job import (
        ILLUSTRATION_JOB_NONTERMINAL_STATUSES,
        IllustrationJob,
    )
    from app.schemas.illustration import (
        IllustrationJobContract,
        PriceSnapshot,
        build_illustration_idempotency_key,
        validate_illustration_job_contract,
    )
    from app.services.illustrations.gateway import (
        GenerationGateError,
        build_illustration_lineage,
        check_generation_prompt_gate,
    )
    from app.services.illustrations.worker import (
        DEFAULT_MAX_INPUT_TOKENS,
        DEFAULT_MAX_OUTPUT_TOKENS,
        MOCK_ILLUSTRATION_MODEL,
        MOCK_ILLUSTRATION_PROVIDER,
    )

    provider = str(params.get("provider") or MOCK_ILLUSTRATION_PROVIDER)
    model = str(params.get("model") or MOCK_ILLUSTRATION_MODEL)
    if provider != MOCK_ILLUSTRATION_PROVIDER:
        raise InvalidInputError(
            f"illustration provider {provider!r} is not configured; supported: {MOCK_ILLUSTRATION_PROVIDER!r}"
        )
    prompt_revision_id = int(params["prompt_revision_id"])
    try:
        prompt_row = await check_generation_prompt_gate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_revision_id=prompt_revision_id,
        )
    except GenerationGateError as exc:
        if exc.reason_code == "prompt_revision_not_found":
            raise NotFoundError(str(exc)) from None
        raise InvalidInputError(str(exc)) from exc

    lineage = build_illustration_lineage(
        prompt_revision=prompt_row,
        provider=provider,
        model=model,
        width=int(params.get("width", 1024)),
        height=int(params.get("height", 1024)),
        max_input_tokens=DEFAULT_MAX_INPUT_TOKENS,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )
    idempotency_key = build_illustration_idempotency_key(
        owner_id, novel_id, lineage
    )
    price_snapshot = PriceSnapshot(
        provider=provider,
        model=model,
        input_price_per_million=Decimal("0.10"),
        output_price_per_million=Decimal("0.10"),
        image_price_per_image=Decimal("0.04"),
    )
    job_contract = IllustrationJobContract(
        schema_version="illustration.v1",
        artifact_kind="illustration_job",
        owner_id=owner_id,
        novel_id=novel_id,
        job_key=str(params.get("job_key") or f"agent-{uuid.uuid4().hex[:8]}"),
        lineage=lineage,
        price_snapshot=price_snapshot.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    validate_illustration_job_contract(job_contract)

    existing = await db.scalar(
        select(IllustrationJob).where(
            IllustrationJob.idempotency_key == idempotency_key
        )
    )
    if existing is not None:
        if (
            existing.status in ILLUSTRATION_JOB_NONTERMINAL_STATUSES
            or existing.status == "succeeded"
        ):
            return _job_view_for_tool(existing)
        raise InvalidInputError(
            "a terminal illustration job with this lineage already exists; retry it explicitly"
        )

    row = IllustrationJob(
        owner_id=owner_id,
        novel_id=novel_id,
        job_key=job_contract.job_key,
        idempotency_key=idempotency_key,
        status="queued",
        status_reason=None,
        error_code=None,
        lease_id=None,
        lease_expires_at=None,
        heartbeat_at=None,
        cancel_requested=False,
        retry_count=0,
        scene_spec_hash=lineage.scene_spec_hash,
        prompt_revision_id=lineage.prompt_revision_id,
        prompt_revision_hash=lineage.prompt_revision_hash,
        visual_bible_revision_id=lineage.visual_bible_revision_id,
        visual_bible_revision_hash=lineage.visual_bible_revision_hash,
        source_snapshot_id=lineage.source_snapshot_id,
        source_snapshot_hash=lineage.source_snapshot_hash,
        cutoff_chapter=lineage.cutoff_chapter,
        model_lineage=dict(lineage.model_lineage),
        config_hash=lineage.config_hash,
        price_snapshot=job_contract.price_snapshot,
        response_hash=None,
        schema_version="illustration.v1",
    )
    db.add(row)
    await db.flush()
    return _job_view_for_tool(row)


async def _default_publish_illustration(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 34 action 工具默认服务：创建**一个**候选锚点 proposal（D-34-01）。

    服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision
    （Phase 33 handoff）与精确 source span（excerpt + anchor_hash +
    chapter_content_hash + source snapshot）；创建候选 IllustrationAnchorProposal
    + pending Web ApprovalRequest（action=publish_illustration，payload_hash
    确定性重放，D-11/D-15）。绝不发布——确定性 publisher 在用户 Web 批准后原子
    校验 approval + payload + scope 才创建 valid anchor；Agent/浏览器绝不发布。
    """
    from app.services.illustration_anchors.publish import (
        AnchorProposalError,
        create_anchor_proposal,
    )

    try:
        result = await create_anchor_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
            action="publish_illustration",
        )
    except AnchorProposalError as exc:
        raise InvalidInputError(str(exc)) from None
    await db.flush()
    return _anchor_proposal_view_for_tool(result)


async def _default_attach_illustration_to_text(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 34 action 工具默认服务：把锚点绑定到精确文本跨度（candidate-only）。

    与 publish_illustration 同 gate（proposal-ready asset + 精确 source span），
    但 ApprovalRequest action 为 attach_illustration_to_text。绝不发布。
    """
    from app.services.illustration_anchors.publish import (
        AnchorProposalError,
        create_anchor_proposal,
    )

    try:
        result = await create_anchor_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
            action="attach_illustration_to_text",
        )
    except AnchorProposalError as exc:
        raise InvalidInputError(str(exc)) from None
    await db.flush()
    return _anchor_proposal_view_for_tool(result)


async def _default_create_canon_fork(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 35 action 工具默认服务：创建**一个**候选 canon fork（D-35-03）。

    服务端 proposal gate 只接受冻结 fork manifest（server-derived cutoff +
    精确 source snapshot）+ delta 意图；创建候选 CanonFork（status=candidate）+
    pending Web ApprovalRequest（action=create_canon_fork，payload_hash 确定性
    重放，D-11/D-15）。绝不物化 fork——确定性 Fork materializer 在用户 Web 批准后
    原子校验 approval + payload + fork manifest + snapshot 重放 + delta 血缘 +
    owner/novel/branch/fork scope 才把 fork 物化为 approved；Original Canon
    不可变、active pointer 恒 false。
    """
    from app.services.canon_fork.materializer import (
        ForkProposalError,
        create_fork_proposal,
    )

    try:
        result = await create_fork_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=params,
        )
    except ForkProposalError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
    await db.flush()
    return _fork_proposal_view_for_tool(result)


async def _default_apply_derivative_edit(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 36 action 工具默认服务：创建**一个**候选 derivative edit（D-36-02）。

    服务端 proposal gate 只接受冻结 source snapshot 血缘 + 有效 project/chapter
    scope + base_revision CAS 锚；创建候选 DerivativeEditProposal
    （proposal_status=proposed）+ pending Web ApprovalRequest
    （action=apply_derivative_edit，payload_hash 确定性重放，D-11/D-15）。
    绝不直接应用——确定性 Revision Service（apply_agent_edit）在用户 Web 批准后
    原子校验 approval + payload + 冻结 proposal artifact 血缘 +
    owner/novel/branch/fork scope + 同一 base_revision CAS 才把 approved proposal
    应用为 append-only agent_proposal 修订；Original Canon / user draft
    （autosave）revisions / published 状态绝不被 Agent 触碰。
    """
    from app.services.derivative_editor.revisions import (
        DerivativeEditApplyError,
        create_agent_edit_proposal,
    )

    try:
        result = await create_agent_edit_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            chapter_id=int(params["chapter_id"]),
            content=str(params["content"]),
            base_revision=int(params["base_revision"]),
            proposal_key=str(params["proposal_key"]),
            branch=params.get("branch"),
            fork=params.get("fork"),
            source_snapshot_hash=params.get("source_snapshot_hash"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except DerivativeEditApplyError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None
    await db.flush()
    return _agent_edit_proposal_view_for_tool(result)


async def _default_allow_divergence(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 37 action 工具默认服务：创建**一个**显式 divergence override（D-37-03）。

    委托 `overrides.request_divergence_override`：只为 blocked / ``needs_override``
    候选创建 pending ``DerivativeOverride`` + pending Web ApprovalRequest
    （action=allow_divergence，payload_hash 绑定 exact draft_hash +
    canon_delta_hash，D-11/D-15）。**绝不发布、绝不写 Original Canon**——只有独立
    ``publish_derivative_revision`` approval 被批准后由确定性 revision
    publisher 物化。
    """
    from app.services.derivative_generation.agent_boundary import (
        OverrideError,
        request_divergence_override,
    )

    try:
        return await request_divergence_override(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            chapter_id=int(params["chapter_id"]),
            candidate_id=int(params["candidate_id"]),
            reason=str(params["reason"]),
            affected_evidence=list(params.get("affected_evidence") or []),
            kind=params.get("kind"),
            draft_hash=str(params["draft_hash"]),
            canon_delta_hash=str(params["canon_delta_hash"]),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except OverrideError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_publish_derivative_revision(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 37 action 工具默认服务：创建**一个**独立 publish ApprovalRequest。

    委托 `overrides.request_publish_derivative_revision`：只在 **allow_divergence
    approval 已批准 + 完整 revalidation 通过** 后才为同一候选创建独立 pending
    Web ApprovalRequest（action=publish_derivative_revision），绑定**与
    allow_divergence approval 完全相同**的 draft_hash / canon_delta_hash。
    **绝不发布、绝不写 Original Canon**。
    """
    from app.services.derivative_generation.agent_boundary import (
        OverrideError,
        request_publish_derivative_revision,
    )

    try:
        return await request_publish_derivative_revision(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            override_id=int(params["override_id"]),
            draft_hash=str(params["draft_hash"]),
            canon_delta_hash=str(params["canon_delta_hash"]),
            approval_note=params.get("approval_note"),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except OverrideError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_publish_derivative_visual(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 38 action 工具默认服务：为已存储 candidate 创建**一个** pending
    ApprovalRequest（D-38-03/D-38-04）。

    委托 `derivative_visual.agent_boundary.request_publish_derivative_visual`：
    只接受 owner/novel/fork scope 内可批准（candidate/needs_review）的候选
    （blocked candidate / wrong owner/branch/fork / scene_spec_hash drift →
    fail closed）；创建 pending Web ApprovalRequest
    （action=publish_derivative_visual，payload_hash 绑定候选冻结血缘，
    D-11/D-15）。**绝不发布、绝不写 Original Visual Bible**——只有独立 approval
    被用户批准后由确定性 review seam（review_candidate_asset）物化为 approved
    published asset。
    """
    from app.services.derivative_visual.agent_boundary import (
        DerivativeVisualBoundaryError,
        request_publish_derivative_visual,
    )

    try:
        return await request_publish_derivative_visual(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_asset_id=int(params["candidate_asset_id"]),
            scene_spec_hash=str(params["scene_spec_hash"]),
            actor_id=owner_id,
            approval_note=params.get("approval_note"),
            branch=params.get("branch"),
            fork=params.get("fork"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
            artifact_id=params.get("artifact_id"),
            artifact_revision_id=params.get("artifact_revision_id"),
        )
    except DerivativeVisualBoundaryError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_approve_export(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 39 action 工具默认服务：创建**一个** pending approve_export
    ApprovalRequest（D-39-01/D-39-02）。

    委托 `derivative_export.materializer.request_approve_export`：只接受
    owner/novel/branch/fork/project scope 内已 finalize 的候选
    ExportPreparationArtifact + 确定性 preparation_hash 重放（stale/伪造 hash /
    wrong owner/branch/fork/project → fail closed）；创建 pending Web
    ApprovalRequest（action=approve_export，payload_hash 绑定 artifact
    revision + preparation_hash，D-11/D-15）。**绝不物化、绝不写 Original
    Canon / 域表 / Artifact 状态 / bundle**。
    """
    from app.services.derivative_export.materializer import (
        ExportMaterializationError,
        request_approve_export,
    )

    try:
        return await request_approve_export(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            artifact_id=int(params["artifact_id"]),
            artifact_revision_id=int(params["artifact_revision_id"]),
            preparation_hash=str(params["preparation_hash"]),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
            approval_note=params.get("approval_note"),
            run_id=params.get("run_id"),
            skill_version_id=params.get("skill_version_id"),
        )
    except ExportMaterializationError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


async def _default_materialize_export(
    db,
    *,
    owner_id: int,
    novel_id: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Phase 39 action 工具默认服务：确定性 materializer（D-39-01/D-39-02）。

    委托 `derivative_export.materializer.materialize_export`：只接受 approved
    artifact + preparation_hash 匹配的 approve_export ApprovalRequest，原子校验
    approval action + 相同 hash 绑定 + artifact revision 血缘 + owner/novel/
    branch/fork/project scope + 冻结 manifest 重放，才把候选 artifact 推进为
    approved 并产出可复现 bundle。**绝不写 Original Canon / 域表 / approval
    lineage**（download 只读）。
    """
    from app.services.derivative_export.materializer import (
        ExportMaterializationError,
        materialize_export,
    )

    try:
        return await materialize_export(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=int(params["project_id"]),
            artifact_id=int(params["artifact_id"]),
            artifact_revision_id=int(params["artifact_revision_id"]),
            approval_id=int(params["approval_id"]),
            preparation_hash=str(params["preparation_hash"]),
            reason=params.get("reason"),
            actor_id=owner_id,
            branch=params.get("branch"),
            fork=params.get("fork"),
        )
    except ExportMaterializationError as exc:
        raise InvalidInputError(f"{exc.code}: {exc.detail}") from None


def _agent_edit_proposal_view_for_tool(result) -> dict[str, Any]:
    """DerivativeEditProposal approval ORM → JSON-safe 工具响应。

    candidate-only：proposal_status 恒为 proposed、绝不 applied；approval_request_id /
    payload_hash 供 Web 审批轮询与确定性 Revision Service 引用。
    """
    return {
        "proposal_key": result.proposal_key,
        "owner_id": result.owner_id,
        "novel_id": result.novel_id,
        "project_id": result.project_id,
        "chapter_id": result.chapter_id,
        "base_revision": result.base_revision,
        "content_hash": result.content_hash,
        "approval_request_id": result.approval_request_id,
        "approval_action": result.approval_action,
        "approval_status": result.approval_status,
        "approval_payload_hash": result.approval_payload_hash,
        "status": "candidate",
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _fork_proposal_view_for_tool(result) -> dict[str, Any]:
    """CanonFork + ApprovalRequest ORM → JSON-safe 工具响应。

    candidate-only：fork status 恒为 candidate、active 恒 false，绝不 approved/
    published；approval_request_id / payload_hash 供 Web 审批轮询与确定性 Fork
    materializer 引用。
    """
    fork = result.fork
    approval = result.approval_request
    return {
        "fork_id": fork.id,
        "owner_id": fork.owner_id,
        "novel_id": fork.novel_id,
        "fork_key": fork.fork_key,
        "space": fork.space,
        "status": fork.status,
        "source_version_key": fork.source_version_key,
        "source_snapshot_id": fork.source_snapshot_id,
        "source_snapshot_hash": fork.source_snapshot_hash,
        "through_chapter": fork.through_chapter,
        "full_book_authorized": fork.full_book_authorized,
        "cutoff_snapshot_hash": fork.cutoff_snapshot_hash,
        "scope_hash": fork.scope_hash,
        "manifest_hash": fork.manifest_hash,
        "delta_content_hash": result.delta_content_hash,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "active": bool(fork.active),
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _anchor_proposal_view_for_tool(result) -> dict[str, Any]:
    """IllustrationAnchorProposal + ApprovalRequest ORM → JSON-safe 工具响应。

    candidate-only：status 恒为 pending_approval/proposed，绝不 published；
    approval_request_id / payload_hash 供 Web 审批轮询与确定性 publisher 引用。
    """
    proposal = result.proposal
    approval = result.approval_request
    return {
        "proposal_id": proposal.id,
        "owner_id": proposal.owner_id,
        "novel_id": proposal.novel_id,
        "chapter_id": proposal.chapter_id,
        "chapter_number": proposal.chapter_number,
        "proposal_key": proposal.proposal_key,
        "source_start": proposal.source_start,
        "source_end": proposal.source_end,
        "anchor_hash": proposal.anchor_hash,
        "proposal_asset_revision_id": proposal.proposal_asset_revision_id,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": proposal.status,
        "candidate_only": True,
        "replayed": bool(result.replayed),
    }


def _job_view_for_tool(job) -> dict[str, Any]:
    """IllustrationJob ORM → JSON-safe 工具响应（候选作业读信封，永不 published）。"""
    return {
        "id": job.id,
        "owner_id": job.owner_id,
        "novel_id": job.novel_id,
        "job_key": job.job_key,
        "idempotency_key": job.idempotency_key,
        "status": job.status,
        "status_reason": job.status_reason,
        "error_code": job.error_code,
        "retry_count": job.retry_count,
        "scene_spec_hash": job.scene_spec_hash,
        "prompt_revision_id": job.prompt_revision_id,
        "prompt_revision_hash": job.prompt_revision_hash,
        "visual_bible_revision_hash": job.visual_bible_revision_hash,
        "source_snapshot_id": job.source_snapshot_id,
        "source_snapshot_hash": job.source_snapshot_hash,
        "cutoff_chapter": job.cutoff_chapter,
        "config_hash": job.config_hash,
        "candidate_only": True,
    }


async def _resolve_world_model_version(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
) -> int:
    """显式 version 直接返回；缺省取该 owner/novel 最新版本（无 → 404-hide）。"""
    if version_id is not None:
        return int(version_id)
    versions = await WorldModelEventQueries(db).list_versions(
        owner_id=owner_id, novel_id=novel_id
    )
    if not versions:
        raise NotFoundError("world-model projection not found in owner scope")
    return versions[-1]


# ────────────────────────── 门面本体 ──────────────────────────


class ToolFacade:
    """12 个只读工具 + 11 个候选 action 工具的统一执行门面。

    所有强制点（字节上限 / 超时 / budget hook / 错误码映射）都在
    ``execute`` 内完成；owner / cutoff 逻辑复用现有服务。Phase 33 的
    ``generate_image_candidate`` 只创建候选作业（candidate-only）；Phase 34
    ``publish_illustration`` / ``attach_illustration_to_text`` 只创建候选
    proposal + pending Web ApprovalRequest；Phase 35 ``create_canon_fork`` 只
    创建候选 fork + pending Web ApprovalRequest；Phase 36
    ``apply_derivative_edit`` 只创建候选 DerivativeEditProposal + pending Web
    ApprovalRequest——确定性 Revision Service 拥有 approved proposal 应用。
    Phase 37 ``allow_divergence`` / ``publish_derivative_revision`` 只创建
    divergence override / 独立 publish ApprovalRequest（相同 hash 绑定）——
    确定性 revision publisher（``consume_publish_approval``）拥有 approved
    Fanfiction Canon 物化。Phase 38 ``publish_derivative_visual`` 只为已存储
    candidate 创建 pending publish ApprovalRequest（绑定候选冻结血缘）——
    确定性 review seam 拥有 approved published asset 物化，绝不写 Original
    Visual Bible。Phase 39 ``approve_export`` 只为已 finalize 候选
    ExportPreparationArtifact 创建 pending approve_export ApprovalRequest
    （绑定 artifact revision + preparation_hash）；``materialize_export`` 是
    确定性 materializer——只接受 approved artifact + preparation_hash 匹配的
    approve_export approval，把候选 artifact 推进为 approved 并产出可复现
    bundle（frozen manifest 复算），绝不写 Original Canon / 域表 / approval
    lineage（download 只读）。
    """

    def __init__(
        self,
        *,
        byte_cap: int = DEFAULT_BYTE_CAP,
        timeout: float = DEFAULT_TOOL_TIMEOUT,
        budget_hook: BudgetHook | None = None,
        cutoff_resolver: Callable | None = None,
        service_overrides: dict[str, Callable] | None = None,
    ) -> None:
        self.byte_cap = byte_cap
        self.timeout = timeout
        self.budget_hook = budget_hook or default_budget_hook
        # cutoff 解析器可注入（测试用 stub）；默认复用现有 resolve_chapter_cutoff。
        self.cutoff_resolver = cutoff_resolver or resolve_chapter_cutoff
        # 服务入口可注入（adversarial/contract 测试用 stub；默认走真实服务）。
        self._overrides = dict(service_overrides or {})
        self._handlers = {
            "get_novel": self._get_novel,
            "get_chapter": self._get_chapter,
            "search_novel_text": self._search_novel_text,
            "get_timeline": self._get_timeline,
            "get_relationships": self._get_relationships,
            "get_clues": self._get_clues,
            "get_narrative_memory": self._get_narrative_memory,
            # Phase 27 世界模型只读工具（27-05）。
            "get_events": self._get_events,
            "get_character_state": self._get_character_state,
            "get_character_knowledge": self._get_character_knowledge,
            "get_world_rules": self._get_world_rules,
            "get_evidence_span": self._get_evidence_span,
            # Phase 30 Visual Bible 只读工具（31-04）。
            "get_visual_bible": self._get_visual_bible,
            # Phase 33 候选生成 action 工具（33-05）：只创建候选作业。
            "generate_image_candidate": self._generate_image_candidate,
            # Phase 34 锚点提议 action 工具（34-05）：只创建候选 proposal +
            # pending Web ApprovalRequest；确定性 publisher 拥有 publication。
            "publish_illustration": self._publish_illustration,
            "attach_illustration_to_text": self._attach_illustration_to_text,
            # Phase 35 canon fork 提议 action 工具（35-05）：只创建候选 fork +
            # pending Web ApprovalRequest；确定性 Fork materializer 拥有 approved
            # fork 物化。
            "create_canon_fork": self._create_canon_fork,
            # Phase 36 derivative 编辑提议 action 工具（36-05）：只创建候选
            # proposal + pending Web ApprovalRequest；确定性 Revision Service
            # 拥有 approved proposal 应用。
            "apply_derivative_edit": self._apply_derivative_edit,
            # Phase 37 derivative generation action 工具（37-05）：只创建
            # divergence override / 独立 publish ApprovalRequest（相同 hash 绑定）；
            # 确定性 revision publisher 拥有 approved Fanfiction Canon 物化。
            "allow_divergence": self._allow_divergence,
            "publish_derivative_revision": self._publish_derivative_revision,
            # Phase 38 branch-aware derivative visual action 工具（38-05）：只创建
            # pending publish ApprovalRequest（绑定候选冻结血缘）；确定性 review
            # seam 拥有 approved published asset 物化，绝不写 Original Visual Bible。
            "publish_derivative_visual": self._publish_derivative_visual,
            # Phase 39 derivative export action 工具（39-05）：approve_export 只
            # 创建 pending approve_export ApprovalRequest（绑定 artifact revision +
            # preparation_hash）；materialize_export 是确定性 materializer——只
            # 接受 approved artifact + preparation_hash 匹配的 approval，把候选
            # artifact 推进为 approved 并产出可复现 bundle，绝不写 Original Canon。
            "approve_export": self._approve_export,
            "materialize_export": self._materialize_export,
        }

    # ── 公共入口 ──

    async def execute(
        self,
        tool_name: str,
        *,
        db,
        novel: Novel,
        owner_id: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """执行一个工具；返回 JSON 安全的 payload（已通过字节上限检查）。

        抛出的异常均为 AgentToolError 子类（冻结错误码）。
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise InvalidInputError(f"未知工具: {tool_name!r}")

        # budget hook：fail closed，在服务调用之前拦截。
        await self.budget_hook(tool_name, params)

        try:
            raw = await asyncio.wait_for(
                handler(db=db, novel=novel, owner_id=owner_id, params=params),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(
                f"工具 {tool_name} 执行超过 {self.timeout:.1f}s 超时"
            ) from exc
        except AgentToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射为上游错误
            logger.exception("工具 %s 执行失败: %s", tool_name, exc)
            raise UpstreamError(f"工具 {tool_name} 上游执行失败") from exc

        payload = self._to_json_safe(raw)
        size = len(
            json.dumps(payload, ensure_ascii=False, default=_json_default).encode(
                "utf-8"
            )
        )
        if size > self.byte_cap:
            raise OutputTooLargeError(
                f"工具 {tool_name} 响应 {size} 字节超过 {self.byte_cap} 字节上限"
            )
        return payload

    @staticmethod
    def _to_json_safe(raw: Any) -> Any:
        """把服务返回值归一化为 JSON 安全结构（pydantic → dict，ORM → schema）。"""
        if raw is None:
            return None
        if hasattr(raw, "model_dump"):
            return raw.model_dump(mode="json")
        if isinstance(raw, dict):
            return {key: ToolFacade._to_json_safe(value) for key, value in raw.items()}
        if isinstance(raw, (list, tuple)):
            return [ToolFacade._to_json_safe(item) for item in raw]
        return raw

    def _svc(self, key: str, default: Callable) -> Callable:
        return self._overrides.get(key, default)

    # ── 各工具处理函数 ──

    async def _get_novel(self, *, db, novel: Novel, owner_id: int, params: dict):
        svc = self._svc("get_novel", _default_get_novel)
        row = await svc(db, novel.id)
        if row is None:
            raise NotFoundError("小说不存在")
        return NovelResponse.model_validate(row)

    async def _get_chapter(self, *, db, novel: Novel, owner_id: int, params: dict):
        chapter_id = int(params["chapter_id"])
        svc = self._svc("get_chapter", _default_get_chapter)
        chapter = await svc(db, chapter_id)
        if chapter is None or chapter.novel_id != novel.id:
            raise NotFoundError("章节不存在")
        cutoff = await self.cutoff_resolver(db, novel)
        if cutoff is not None and int(chapter.chapter_number) > int(cutoff):
            raise BeyondCutoffError(
                f"章节 {chapter.chapter_number} 超出当前阅读进度截止点 {cutoff}"
            )
        return ChapterResponse.model_validate(chapter)

    async def _search_novel_text(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
        svc = self._svc("search_novel_text", _default_search_novel_text)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            query=params["query"],
            mode=params.get("mode", "auto"),
            top_k=int(params.get("top_k", 10)),
        )

    async def _get_timeline(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
        chapter_start = params.get("chapter_start")
        chapter_end = params.get("chapter_end")
        if not persisted_full_book and cutoff is not None:
            if chapter_end is not None and int(chapter_end) > int(cutoff):
                raise BeyondCutoffError(
                    f"章节范围结束点 {chapter_end} 超出服务端截止点 {cutoff}"
                )

        ordering = (
            TimelineOrdering.STORY
            if params.get("ordering") == "story"
            else TimelineOrdering.NARRATIVE
        )
        svc = self._svc("get_timeline", _default_get_timeline)
        common = dict(
            db=db,
            novel=novel,
            owner_id=owner_id,
            ordering=ordering,
            person=params.get("person"),
            include_causal=bool(params.get("causal", False)),
            request_full_book=persisted_full_book,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
        )
        active = await svc(source=TimelineVersionSource.ACTIVE, **common)
        running = await svc(source=TimelineVersionSource.RUNNING_CANDIDATE, **common)
        return TimelineEnvelope(active=active, running_candidate=running)

    async def _get_relationships(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
        through_chapter = params.get("through_chapter")
        if not persisted_full_book and cutoff is not None:
            if through_chapter is not None and int(through_chapter) > int(cutoff):
                raise BeyondCutoffError(
                    f"through_chapter {through_chapter} 超出服务端截止点 {cutoff}"
                )

        source_name = params.get("source", "active")
        source = (
            RelationshipVersionSource.RUNNING_CANDIDATE
            if source_name == "running_candidate"
            else RelationshipVersionSource.ACTIVE
        )
        svc = self._svc("get_relationships", _default_get_relationships)
        return await svc(
            db,
            novel=novel,
            owner_id=owner_id,
            source=source,
            version_id=params.get("version_id"),
            through_chapter=through_chapter,
            request_full_book=persisted_full_book,
            character_id=params.get("character_id"),
            relation_type=params.get("relation_type"),
            include_provisional=bool(params.get("include_provisional", False)),
        )

    async def _get_clues(self, *, db, novel: Novel, owner_id: int, params: dict):
        persisted_full_book = _persisted_full_book(novel)
        svc = self._svc("get_clues", _default_get_clues)
        return await svc(
            db,
            novel=novel,
            owner_id=owner_id,
            request_full_book=persisted_full_book,
            character_id=params.get("character_id"),
            status_filter=params.get("status"),
        )

    async def _get_narrative_memory(
        self, *, db, novel: Novel, owner_id: int, params: dict
    ):
        persisted_full_book = _persisted_full_book(novel)
        cutoff = None if persisted_full_book else await self.cutoff_resolver(db, novel)
        through_chapter = params.get("through_chapter")
        if not persisted_full_book and cutoff is not None:
            if through_chapter is not None and int(through_chapter) > int(cutoff):
                raise BeyondCutoffError(
                    f"through_chapter {through_chapter} 超出服务端截止点 {cutoff}"
                )

        svc = self._svc("get_narrative_memory", _default_get_narrative_memory)
        view = params.get("view", "versions")
        try:
            data = await svc(
                db,
                owner_id=owner_id,
                novel_id=novel.id,
                version_id=params.get("version_id"),
                view=view,
                through_chapter=through_chapter,
            )
        except StructureQueryError as exc:
            # 映射叙事记忆领域错误：404 → not_found；其余 → invalid_input。
            if exc.status_code == 404:
                raise NotFoundError(str(exc.detail)) from exc
            raise InvalidInputError(str(exc.detail)) from exc

        # ADR-0002：叙事记忆仅候选发布，响应必须显式标注 release_status。
        return {
            "release_status": "candidate",
            "publication_status": "candidate_preview",
            "view": view,
            "data": data,
        }

    # ── Phase 27 世界模型只读工具（27-05） ──

    async def _resolve_world_cutoff(self, db, novel, params) -> int:
        """服务端截止点权威（D-05/D-07）：显式 cutoff 超限 → beyond_cutoff。

        full_book 只读持久化开关（_persisted_full_book）；显式 cutoff 提供时
        超过服务端截止点被拒绝，绝不越权到整本书。
        """
        persisted_full_book = _persisted_full_book(novel)
        server_cutoff = (
            None if persisted_full_book else await self.cutoff_resolver(db, novel)
        )
        explicit = params.get("cutoff")
        if explicit is not None:
            if server_cutoff is not None and int(explicit) > int(server_cutoff):
                raise BeyondCutoffError(
                    f"cutoff {explicit} 超出服务端截止点 {server_cutoff}"
                )
            return int(explicit)
        return int(server_cutoff or 0)

    async def _get_events(self, *, db, novel, owner_id, params):
        svc = self._svc("get_events", _default_get_events)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        payload = await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff=cutoff,
        )
        if payload is None:
            raise NotFoundError("world-model events not found in scope")
        return payload

    async def _get_character_state(self, *, db, novel, owner_id, params):
        svc = self._svc("get_character_state", _default_get_character_state)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            subject=str(params["subject"]),
            cutoff=cutoff,
            pov=params.get("pov"),
        )

    async def _get_character_knowledge(self, *, db, novel, owner_id, params):
        svc = self._svc("get_character_knowledge", _default_get_character_knowledge)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            subject=str(params["subject"]),
            cutoff=cutoff,
            pov=params.get("pov"),
        )

    async def _get_world_rules(self, *, db, novel, owner_id, params):
        svc = self._svc("get_world_rules", _default_get_world_rules)
        version_id = await _resolve_world_model_version(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
        )
        cutoff = await self._resolve_world_cutoff(db, novel, params)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=version_id,
            cutoff=cutoff,
        )

    async def _get_evidence_span(self, *, db, novel, owner_id, params):
        svc = self._svc("get_evidence_span", _default_get_evidence_span)
        span = await svc(
            db,
            chapter_id=int(params["chapter_id"]),
            source_start=int(params["source_start"]),
            source_end=int(params["source_end"]),
            content_hash=str(params["content_hash"]),
        )
        if span is None:
            raise NotFoundError("章节不存在")
        if span.get("novel_id") != novel.id:
            raise NotFoundError("章节不存在")
        cutoff = await self.cutoff_resolver(db, novel)
        if cutoff is not None and int(span["chapter_number"]) > int(cutoff):
            raise BeyondCutoffError(
                f"章节 {span['chapter_number']} 超出当前阅读进度截止点 {cutoff}"
            )
        return span

    async def _get_visual_bible(self, *, db, novel, owner_id, params):
        svc = self._svc("get_visual_bible", _default_get_visual_bible)
        payload = await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            version_id=params.get("version_id"),
            approved_only=bool(params.get("approved_only", False)),
        )
        if payload is None:
            raise NotFoundError("visual bible version not found in scope")
        return payload

    async def _generate_image_candidate(self, *, db, novel, owner_id, params):
        """创建候选生成作业（服务端 generation gate，candidate-only，D-33-01）。"""
        svc = self._svc("generate_image_candidate", _default_generate_image_candidate)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_illustration(self, *, db, novel, owner_id, params):
        """创建候选锚点 proposal + pending Web ApprovalRequest（candidate-only，D-34-01）。"""
        svc = self._svc("publish_illustration", _default_publish_illustration)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _attach_illustration_to_text(self, *, db, novel, owner_id, params):
        """把锚点绑定到精确文本跨度（candidate-only；attach action 也要求 Web Approval）。"""
        svc = self._svc(
            "attach_illustration_to_text", _default_attach_illustration_to_text
        )
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _create_canon_fork(self, *, db, novel, owner_id, params):
        """创建候选 canon fork + pending Web ApprovalRequest（candidate-only，D-35-03）。"""
        svc = self._svc("create_canon_fork", _default_create_canon_fork)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _apply_derivative_edit(self, *, db, novel, owner_id, params):
        """创建候选 derivative edit + pending Web ApprovalRequest（candidate-only，D-36-02）。"""
        svc = self._svc("apply_derivative_edit", _default_apply_derivative_edit)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _allow_divergence(self, *, db, novel, owner_id, params):
        """创建显式 divergence override + pending Web ApprovalRequest（candidate-only，D-37-03）。"""
        svc = self._svc("allow_divergence", _default_allow_divergence)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_derivative_revision(self, *, db, novel, owner_id, params):
        """创建独立 publish ApprovalRequest（相同 hash 绑定；candidate-only，37-05）。"""
        svc = self._svc("publish_derivative_revision", _default_publish_derivative_revision)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _publish_derivative_visual(self, *, db, novel, owner_id, params):
        """创建独立 publish ApprovalRequest（绑定候选冻结血缘；candidate-only，38-05）。"""
        svc = self._svc("publish_derivative_visual", _default_publish_derivative_visual)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _approve_export(self, *, db, novel, owner_id, params):
        """创建独立 approve_export ApprovalRequest（绑定 artifact revision +
        preparation_hash；candidate-only，39-05）。"""
        svc = self._svc("approve_export", _default_approve_export)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )

    async def _materialize_export(self, *, db, novel, owner_id, params):
        """确定性 materializer：只接受 approved artifact + preparation_hash 匹配的
        approve_export approval，产出可复现 bundle（approved-only，39-05）。"""
        svc = self._svc("materialize_export", _default_materialize_export)
        return await svc(
            db,
            owner_id=owner_id,
            novel_id=novel.id,
            params=params,
        )


def _json_default(obj: Any) -> str:
    """兜底序列化：datetime / Decimal 等非 JSON 原生类型转字符串。"""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


# 全局单例：API 路由与测试共用；测试可用独立实例注入 stub。
tool_facade = ToolFacade()
