"""Owner-scoped chapter analysis batch API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_agent_actor, require_user
from app.models import Novel, User
from app.schemas.agent_runtime import (
    ChapterBatchCreate,
    ChapterBatchView,
)
from app.services.agent_runtime.chapter_batch import (
    ChapterBatchError,
    create_or_resume_chapter_batch,
    get_chapter_batch_status,
    resume_chapter_batch,
)

router = APIRouter(dependencies=[Depends(require_agent_actor)])


def _raise_batch_error(exc: ChapterBatchError) -> None:
    message = str(exc)
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    if "not found" in message:
        status_code = status.HTTP_404_NOT_FOUND
    elif "owner scope" in message:
        status_code = status.HTTP_404_NOT_FOUND
    raise HTTPException(status_code=status_code, detail=message) from exc


@router.post(
    "/novels/{novel_id}/chapter-batches",
    response_model=ChapterBatchView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_chapter_batch(
    novel_id: int,
    data: ChapterBatchCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ChapterBatchView:
    """Create or resume a bounded batch; repeated identical requests are idempotent."""

    try:
        result = await create_or_resume_chapter_batch(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            chapter_start=data.chapter_start,
            chapter_end=data.chapter_end,
            chapter_ids=data.chapter_ids,
            concurrency_window=data.concurrency_window,
        )
        await db.commit()
        return ChapterBatchView.model_validate(result)
    except ChapterBatchError as exc:
        await db.rollback()
        _raise_batch_error(exc)


@router.get(
    "/novels/{novel_id}/chapter-batches/{batch_id}",
    response_model=ChapterBatchView,
)
async def chapter_batch_status(
    novel_id: int,
    batch_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ChapterBatchView:
    try:
        result = await get_chapter_batch_status(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            batch_id=batch_id,
        )
        return ChapterBatchView.model_validate(result)
    except ChapterBatchError as exc:
        _raise_batch_error(exc)


@router.post(
    "/novels/{novel_id}/chapter-batches/{batch_id}/resume",
    response_model=ChapterBatchView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_chapter_batch_endpoint(
    novel_id: int,
    batch_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ChapterBatchView:
    try:
        result = await resume_chapter_batch(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            batch_id=batch_id,
        )
        await db.commit()
        return ChapterBatchView.model_validate(result)
    except ChapterBatchError as exc:
        await db.rollback()
        _raise_batch_error(exc)
