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
    GetChapterRequest,
    GetCluesRequest,
    GetNarrativeMemoryRequest,
    GetNovelRequest,
    GetRelationshipsRequest,
    GetTimelineRequest,
    SearchNovelTextRequest,
)
from app.services.agent_tools.errors import (
    AgentToolError,
    UpstreamError,
)
from app.services.agent_tools.facade import tool_facade

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


async def _run_tool(tool_name: str, *, db: AsyncSession, novel: Novel, owner_id: int, params: dict):
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
        "get_chapter", db=db, novel=novel, owner_id=current_user.id, params=_params(body)
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
        "get_timeline", db=db, novel=novel, owner_id=current_user.id, params=_params(body)
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
