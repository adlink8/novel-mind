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
from app.services.knowledge_units.search import (
    NarrativeRetrievalStrategy,
    production_retrieval_strategy,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=SearchResponse)
async def global_search(
    request: SearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    strategy: NarrativeRetrievalStrategy = Depends(production_retrieval_strategy),
):
    """
    全局混合搜索（跨所有小说）。

    需要认证。
    """
    try:
        results = await strategy.search_global(
            db,
            owner_id=current_user.id,
            query=request.query,
            mode=request.mode,
            top_k=request.top_k,
        )
    except Exception as e:
        logger.exception("global hybrid search failed: %s", e)
        return SearchResponse(results=[], total=0, query=request.query)
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
    strategy: NarrativeRetrievalStrategy = Depends(production_retrieval_strategy),
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

    try:
        if request.mode != "chunks" and current_user is None:
            raise HTTPException(status_code=401, detail="知识单元检索需要认证")
        results = await strategy.search_novel(
            db,
            owner_id=current_user.id if current_user else novel.owner_id,
            novel_id=novel_id,
            domain_profile="fiction",
            query=request.query,
            mode=request.mode,
            top_k=request.top_k,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("hybrid search failed for novel_%d: %s", novel_id, e)
        return SearchResponse(results=[], total=0, query=request.query)

    return SearchResponse(
        results=[SearchResultItem(**r) for r in results],
        total=len(results),
        query=request.query,
    )
