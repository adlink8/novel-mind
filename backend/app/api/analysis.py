"""
剧情分析 API — Phase 07 层级接入

端点:
  POST /api/analysis/{novel_id}/analyze
  GET  /api/analysis/{novel_id}
  POST /api/analysis/{novel_id}/chapters/{chapter_id}/analyze
  GET  /api/analysis/{novel_id}/hierarchy
  POST /api/analysis/{novel_id}/hierarchy/rebuild
  POST /api/analysis/{novel_id}/analyze/stream  (仍为 501 占位)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models.novel import Novel
from app.models.user import User
from app.services.analysis_service import (
    SUPPORTED_TYPES,
    AnalysisError,
    analysis_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_user)])


class AnalyzeBody(BaseModel):
    analysis_type: str = Field(
        default="plot_summary",
        description="plot_summary / character_analysis / theme / style / chapter_summary / hierarchy_map",
    )
    chapter_id: int | None = None
    use_llm: bool = Field(
        default=True,
        description="是否尝试 LLM 精炼；失败时仍返回 Phase07 结构分析",
    )
    rebuild_hierarchy: bool = Field(
        default=False, description="强制重建层级树后再分析"
    )
    model: str | None = Field(default=None, description="可选 LiteLLM 模型 ID")


async def _owned_novel(
    novel_id: int, db: AsyncSession, user: User
) -> Novel:
    novel = (
        await db.execute(select(Novel).where(Novel.id == novel_id))
    ).scalar_one_or_none()
    if not novel or (novel.owner_id != user.id and not user.is_superuser):
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel


def _to_response(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "novel_id": row.novel_id,
        "chapter_id": row.chapter_id,
        "analysis_type": row.analysis_type,
        "result_data": row.result_data or {},
        "model_used": row.model_used,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "status": "ready",
    }


@router.post("/{novel_id}/analyze")
async def analyze_novel(
    novel_id: int,
    body: AnalyzeBody | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """对小说做 AI/结构分析（默认接入 Phase 07 场景层级）。"""
    body = body or AnalyzeBody()
    await _owned_novel(novel_id, db, current_user)
    try:
        row = await analysis_service.analyze(
            db,
            novel_id=novel_id,
            analysis_type=body.analysis_type,
            chapter_id=body.chapter_id,
            model=body.model,
            use_llm=body.use_llm,
            rebuild_hierarchy=body.rebuild_hierarchy,
        )
        return _to_response(row)
    except AnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        logger.exception("analyze failed novel_%s", novel_id)
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get("/{novel_id}")
async def get_analysis(
    novel_id: int,
    analysis_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取已有分析结果列表（新→旧）。"""
    await _owned_novel(novel_id, db, current_user)
    rows = await analysis_service.get_latest(
        db, novel_id=novel_id, analysis_type=analysis_type
    )
    if not rows:
        return {
            "novel_id": novel_id,
            "status": "not_analyzed",
            "supported_types": sorted(SUPPORTED_TYPES),
            "items": [],
        }
    return {
        "novel_id": novel_id,
        "status": "ready",
        "items": [_to_response(r) for r in rows],
        "latest": _to_response(rows[0]),
    }


@router.post("/{novel_id}/chapters/{chapter_id}/analyze")
async def analyze_chapter(
    novel_id: int,
    chapter_id: int,
    body: AnalyzeBody | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """分析单个章节（基于该章下的 Phase07 场景）。"""
    body = body or AnalyzeBody(analysis_type="chapter_summary")
    await _owned_novel(novel_id, db, current_user)
    try:
        row = await analysis_service.analyze(
            db,
            novel_id=novel_id,
            analysis_type=body.analysis_type or "chapter_summary",
            chapter_id=chapter_id,
            model=body.model,
            use_llm=body.use_llm,
            rebuild_hierarchy=body.rebuild_hierarchy,
        )
        return _to_response(row)
    except AnalysisError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        logger.exception("chapter analyze failed")
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get("/{novel_id}/hierarchy")
async def get_hierarchy_status(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """查看 Phase 07 层级是否就绪（场景/证据统计）。"""
    await _owned_novel(novel_id, db, current_user)
    return await analysis_service.hierarchy_status(db, novel_id=novel_id)


@router.post("/{novel_id}/hierarchy/rebuild")
async def rebuild_hierarchy(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """强制重建 Phase 07 层级树并设为 active。"""
    novel = await _owned_novel(novel_id, db, current_user)
    from app.services.analysis_service import ensure_hierarchy

    build_id = await ensure_hierarchy(db, novel, force=True)
    status = await analysis_service.hierarchy_status(db, novel_id=novel_id)
    return {"build_id": build_id, **status}


@router.post("/{novel_id}/analyze/stream")
async def analyze_novel_stream(novel_id: int):
    """流式输出分析过程（SSE）— 后续迭代。"""
    raise HTTPException(status_code=501, detail="流式剧情分析尚未实现，请使用 POST /analyze")
