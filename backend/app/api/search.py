"""
BM25 混合搜索 API

提供全局混合搜索和小说内混合搜索两个端点。
结合 PostgreSQL 全文搜索和 ChromaDB 向量搜索。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, require_user
from app.models import Novel, User
from app.schemas.novel import SearchRequest, SearchResponse, SearchResultItem

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=SearchResponse)
async def global_search(
    request: SearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """
    全局混合搜索（跨所有小说）。

    需要认证。
    """
    from app.services.hybrid_search import hybrid_search_service

    results = await hybrid_search_service.search_global(
        db,
        query=request.query,
        top_k=request.top_k,
        owner_id=current_user.id,
    )
    return SearchResponse(
        results=[SearchResultItem(**r) for r in results],
        total=len(results),
        query=request.query,
    )


@router.post("/novels/{novel_id}", response_model=SearchResponse)
async def novel_search(
    novel_id: int,
    request: SearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user),
):
    """
    小说内混合搜索。

    结合 BM25 全文搜索和向量搜索，加权融合排序。

    可选认证：有 token 则检查所有权，无 token 则允许访问。
    """
    # 验证小说存在
    novel_result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = novel_result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="小说不存在")
    if (
        current_user
        and novel.owner_id != current_user.id
        and not current_user.is_superuser
    ):
        raise HTTPException(status_code=403, detail="无权访问该小说")

    from app.services.hybrid_search import hybrid_search_service

    results = await hybrid_search_service.search_novel(
        db, novel_id=novel_id, query=request.query, top_k=request.top_k
    )
    return SearchResponse(
        results=[SearchResultItem(**r) for r in results],
        total=len(results),
        query=request.query,
    )
