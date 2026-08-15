"""阅读器书签 API — 基于 ReaderBookmark 模型（owner-scoped）。

- GET    /api/novels/{novel_id}/bookmarks           列表
- POST   /api/novels/{novel_id}/bookmarks           创建
- DELETE /api/novels/{novel_id}/bookmarks/{bid}     删除
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.models import Chapter, Novel, ReaderBookmark
from app.schemas.bookmark import BookmarkCreate, BookmarkResponse

router = APIRouter()


@router.get("/{novel_id}/bookmarks", response_model=list[BookmarkResponse])
async def list_bookmarks(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户在本书保存的章节书签（按创建时间倒序）。"""
    result = await db.execute(
        select(ReaderBookmark)
        .where(
            ReaderBookmark.owner_id == novel.owner_id,
            ReaderBookmark.novel_id == novel.id,
        )
        .order_by(ReaderBookmark.created_at.desc(), ReaderBookmark.id.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/{novel_id}/bookmarks",
    response_model=BookmarkResponse,
    status_code=201,
)
async def create_bookmark(
    data: BookmarkCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """保存当前阅读位置为书签。

    - chapter_id 必须属于本书（防止跨书引用）
    - position_percent 为章内阅读百分比 0-100
    - owner 沿用小说所有者（require_owned_novel 已校验）
    """
    chapter = await db.scalar(
        select(Chapter).where(
            Chapter.id == data.chapter_id,
            Chapter.novel_id == novel.id,
        )
    )
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    bookmark = ReaderBookmark(
        owner_id=novel.owner_id,
        novel_id=novel.id,
        chapter_id=chapter.id,
        position_percent=data.position_percent,
        label=data.label,
        note=data.note,
    )
    db.add(bookmark)
    await db.commit()
    await db.refresh(bookmark)
    return bookmark


@router.delete("/{novel_id}/bookmarks/{bookmark_id}", status_code=204)
async def delete_bookmark(
    bookmark_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """删除当前用户在本书保存的书签（owner + novel 双重作用域）。"""
    bookmark = await db.scalar(
        select(ReaderBookmark).where(
            ReaderBookmark.id == bookmark_id,
            ReaderBookmark.owner_id == novel.owner_id,
            ReaderBookmark.novel_id == novel.id,
        )
    )
    if not bookmark:
        raise HTTPException(status_code=404, detail="书签不存在")
    await db.delete(bookmark)
    await db.commit()
