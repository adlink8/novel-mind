"""RAG 语义检索 API — 小说向量搜索与索引管理"""

import logging

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_user
from app.models import Novel, User, TextChunk
from app.schemas.novel import (
    RAGSearchRequest,
    RAGSearchResponse,
    IndexStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_novel_for_search(
    novel_id: int,
    db: AsyncSession,
    current_user: User | None,
) -> Novel:
    """获取小说并检查搜索权限（可选认证）。"""
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    if current_user and novel.owner_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="无权访问该小说")
    return novel


async def _get_owned_novel(
    novel_id: int,
    db: AsyncSession,
    current_user: User,
) -> Novel:
    """获取小说并验证所有权（必须认证）。"""
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel or (novel.owner_id != current_user.id and not current_user.is_superuser):
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


@router.post("/{novel_id}/search", response_model=RAGSearchResponse)
async def search_novel(
    novel_id: int,
    body: RAGSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """
    语义搜索小说内容。

    可选认证：有 token 则检查所有权，无 token 则允许访问。
    """
    await _get_novel_for_search(novel_id, db, current_user)

    from app.services.indexing_service import indexing_service

    results = await indexing_service.search_similar(
        db,
        novel_id=novel_id,
        query=body.query,
        top_k=body.top_k,
        chunk_types=body.chunk_types,
    )
    return RAGSearchResponse(results=results)


@router.post("/{novel_id}/index")
async def trigger_index(
    novel_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """
    触发小说索引（异步后台执行）。

    需要认证且仅小说所有者可操作。
    """
    novel = await _get_owned_novel(novel_id, db, current_user)

    if novel.status in ("chunking", "embedding"):
        raise HTTPException(status_code=409, detail="小说正在索引中")

    async def _run_index(novel_id: int):
        """后台索引任务（需要独立数据库会话）。"""
        from app.core.database import async_session_factory
        from app.services.indexing_service import indexing_service

        async with async_session_factory() as session:
            try:
                await indexing_service.index_novel(session, novel_id)
            except Exception:
                logger.exception("后台索引失败 novel_%d", novel_id)

    background_tasks.add_task(_run_index, novel_id)

    return {
        "message": "索引已启动",
        "novel_id": novel_id,
        "status": "chunking",
    }


@router.get("/{novel_id}/index-status", response_model=IndexStatusResponse)
async def get_index_status(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """
    查询小说索引状态。

    需要认证且仅小说所有者可操作。
    """
    novel = await _get_owned_novel(novel_id, db, current_user)

    # 统计分块数量和已嵌入数量
    chunk_count_result = await db.execute(
        select(func.count()).where(TextChunk.novel_id == novel_id)
    )
    chunk_count = chunk_count_result.scalar() or 0

    embedded_count_result = await db.execute(
        select(func.count()).where(
            TextChunk.novel_id == novel_id,
            TextChunk.embedding_status == "embedded",
        )
    )
    embedded_count = embedded_count_result.scalar() or 0

    return IndexStatusResponse(
        novel_id=novel_id,
        status=novel.status,
        chunk_count=chunk_count,
        embedded_count=embedded_count,
    )
