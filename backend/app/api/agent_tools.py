"""
智能体工具门面 API（25.2-02 Domain Tool Contract / D-06 / D-07）。

7 个只读工具通过一个 FastAPI facade 暴露，供 agent-service 调用：
  get_novel / get_chapter / search_novel_text / get_timeline /
  get_relationships / get_clues / get_narrative_memory

安全结构:
  - 每个路由都用 ``Depends(require_owned_novel)``：owner 校验与 404-hide
    在路由签名上结构上不可避免（无 403 oracle）。
  - ``novel_id`` 走查询参数注入 require_owned_novel；请求体只携带各工具
    自己的类型化参数（StrictPydantic，extra="forbid"）。
  - 所有错误统一格式化为 ``{"error": {"code", "message"}}``（冻结错误码表）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.agent_tools import (
    AnchorProposalActionRequest,
    ApplyDerivativeEditRequest,
    CreateCanonForkRequest,
    GenerateImageCandidateRequest,
    GetChapterRequest,
    GetCharacterKnowledgeRequest,
    GetCharacterStateRequest,
    GetCluesRequest,
    GetEventsRequest,
    GetEvidenceSpanRequest,
    GetNarrativeMemoryRequest,
    GetNovelRequest,
    GetRelationshipsRequest,
    GetTimelineRequest,
    GetVisualBibleRequest,
    GetWorldRulesRequest,
    SearchNovelTextRequest,
)
from app.services.agent_tools.errors import (
    AgentToolError,
    UpstreamError,
)
from app.services.agent_tools.facade import tool_facade

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


async def _run_tool(
    tool_name: str, *, db: AsyncSession, novel: Novel, owner_id: int, params: dict
):
    """统一工具执行入口：任何未归类异常都映射为冻结错误码。"""
    try:
        return await tool_facade.execute(
            tool_name, db=db, novel=novel, owner_id=owner_id, params=params
        )
    except AgentToolError:
        raise  # 由全局 exception_handler 格式化为 {error: {code, message}}
    except Exception as exc:  # noqa: BLE001 - 兜底映射
        logger.exception("工具 %s 未归类异常: %s", tool_name, exc)
        raise UpstreamError(f"工具 {tool_name} 上游执行失败") from exc


def _params(body) -> dict:
    """请求体 → 工具参数 dict；get_novel 无参数时允许空请求体。"""
    return body.model_dump() if body is not None else {}


@router.post("/get_novel")
async def tool_get_novel(
    body: GetNovelRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取小说元信息（含章节摘要列表，不含正文）。"""
    return await _run_tool(
        "get_novel", db=db, novel=novel, owner_id=current_user.id, params=_params(body)
    )


@router.post("/get_chapter")
async def tool_get_chapter(
    body: GetChapterRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取章节全文（受 spoiler cutoff 与 64 KiB 字节上限约束）。"""
    return await _run_tool(
        "get_chapter",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/search_novel_text")
async def tool_search_novel_text(
    body: SearchNovelTextRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """小说内全文检索（raw chunks + 知识单元融合）。"""
    return await _run_tool(
        "search_novel_text",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_timeline")
async def tool_get_timeline(
    body: GetTimelineRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """时间线事件信封（spoiler cutoff 服务端强制）。"""
    return await _run_tool(
        "get_timeline",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_relationships")
async def tool_get_relationships(
    body: GetRelationshipsRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """人物关系图信封（spoiler cutoff 服务端强制）。"""
    return await _run_tool(
        "get_relationships",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_clues")
async def tool_get_clues(
    body: GetCluesRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """线索与伏笔信封（spoiler cutoff 服务端强制）。"""
    return await _run_tool(
        "get_clues", db=db, novel=novel, owner_id=current_user.id, params=_params(body)
    )


@router.post("/get_narrative_memory")
async def tool_get_narrative_memory(
    body: GetNarrativeMemoryRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """叙事记忆结构（候选-only，ADR-0002，响应带 release_status="candidate"）。"""
    return await _run_tool(
        "get_narrative_memory",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


# ────────────────────────── Phase 27 世界模型只读工具（27-05） ──────────────────────────


@router.post("/get_events")
async def tool_get_events(
    body: GetEventsRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """世界模型事件/因果候选投影（D-05 cutoff 服务端强制）。"""
    return await _run_tool(
        "get_events",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_character_state")
async def tool_get_character_state(
    body: GetCharacterStateRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """角色状态/目标/动机（REQ-WM-02，D-05 cutoff/POV 服务端强制）。"""
    return await _run_tool(
        "get_character_state",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_character_knowledge")
async def tool_get_character_knowledge(
    body: GetCharacterKnowledgeRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """角色知识（REQ-WM-02，D-05 cutoff/POV 服务端强制）。"""
    return await _run_tool(
        "get_character_knowledge",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_world_rules")
async def tool_get_world_rules(
    body: GetWorldRulesRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """世界规则与规则例外（REQ-WM-03，D-04 例外 first-class，D-05 cutoff 强制）。"""
    return await _run_tool(
        "get_world_rules",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_evidence_span")
async def tool_get_evidence_span(
    body: GetEvidenceSpanRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """leaf 证据跨度（D-07/D-08）：按 chapter+offsets+content_hash 物化原文。"""
    return await _run_tool(
        "get_evidence_span",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/get_visual_bible")
async def tool_get_visual_bible(
    body: GetVisualBibleRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Visual Bible 候选版本视图（31-04；owner 范围服务端强制，candidate-only）。"""
    return await _run_tool(
        "get_visual_bible",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/generate_image_candidate")
async def tool_generate_image_candidate(
    body: GenerateImageCandidateRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Phase 33 候选生成 action（33-05）：创建**一个**候选生成作业。

    服务端 generation gate 只接受已批准且非 stale 的 PromptRevision；作业
    idempotency key 从血缘确定性重放。只创建 candidate 作业（D-33-01..D-33-03），
    绝不写 Canon / 域表 / ApprovalRequest / published 状态——审批与发布属于
    Phase 34；候选资产由 durable worker 在作业成功时产出。
    """
    return await _run_tool(
        "generate_image_candidate",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/publish_illustration")
async def tool_publish_illustration(
    body: AnchorProposalActionRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Phase 34 锚点提议 action（34-05）：创建**一个**候选 proposal + pending
    Web ApprovalRequest（action=publish_illustration，D-11/D-15）。

    服务端 proposal gate 只接受 proposal-ready + rights cleared 的 AssetRevision
    （Phase 33 handoff）与精确 source span（D-34-01）。只创建 candidate
    proposal（proposed → pending_approval），绝不发布——确定性 publisher 在用户
    Web 批准后原子校验 approval + payload + scope 才创建 valid anchor；
    Agent/浏览器绝不发布。
    """
    return await _run_tool(
        "publish_illustration",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/attach_illustration_to_text")
async def tool_attach_illustration_to_text(
    body: AnchorProposalActionRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Phase 34 锚点提议 action（34-05）：把锚点绑定到**精确文本跨度**（candidate
    -only）。与 publish_illustration 同 gate，但 ApprovalRequest action 为
    attach_illustration_to_text（也要求 Web Approval）。绝不发布。
    """
    return await _run_tool(
        "attach_illustration_to_text",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/create_canon_fork")
async def tool_create_canon_fork(
    body: CreateCanonForkRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Phase 35 canon fork 提议 action（35-05，REQ-FORK-01 / REQ-AGENT-03/04/07）：
    创建**一个**候选 fork（candidate-only，D-35-03）。

    服务端 proposal gate 只接受冻结 fork manifest（server-derived cutoff + 精确
    source snapshot）+ delta 意图（delta_key + delta_content）；创建候选 CanonFork
    （status=candidate）+ pending Web ApprovalRequest（action=create_canon_fork，
    payload_hash 确定性重放，D-11/D-15）。绝不物化 fork——确定性 Fork materializer
    在用户 Web 批准后原子校验 approval + payload + fork manifest + snapshot 重放 +
    delta 血缘 + owner/novel/branch/fork scope 才把 fork 物化为 approved；Original
    Canon 不可变、active pointer 恒 false。
    """
    return await _run_tool(
        "create_canon_fork",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )


@router.post("/apply_derivative_edit")
async def tool_apply_derivative_edit(
    body: ApplyDerivativeEditRequest | None = None,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Phase 36 derivative 编辑提议 action（36-05，REQ-FORK-02 / REQ-AGENT-03/04/07）：
    创建**一个**候选 DerivativeEditProposal（candidate-only，D-36-02）。

    服务端 proposal gate 只接受冻结 source snapshot 血缘 + 有效 project/chapter
    scope + base_revision CAS 锚；创建候选 proposal（proposal_status=proposed）+
    pending Web ApprovalRequest（action=apply_derivative_edit，payload_hash 确定
    性重放，D-11/D-15）。绝不直接应用——确定性 Revision Service（
    apply_agent_edit）在用户 Web 批准后原子校验 approval + payload + 冻结
    proposal artifact 血缘 + owner/novel/branch/fork scope + 同一 base_revision
    CAS 才把 approved proposal 应用为 append-only agent_proposal 修订；Original
    Canon / user draft（autosave）revisions / published 状态绝不被 Agent 触碰。
    """
    return await _run_tool(
        "apply_derivative_edit",
        db=db,
        novel=novel,
        owner_id=current_user.id,
        params=_params(body),
    )
