"""Reader bookmark API tests — based on master reader_bookmarks model."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Chapter, Novel, User

pytestmark = pytest.mark.unit


async def _seed_novel_with_chapter(db_session, owner_id: int) -> tuple[Novel, Chapter]:
    novel = Novel(
        title="书签测试小说",
        author="作者",
        owner_id=owner_id,
        status="ready",
        chapter_count=1,
        word_count=4,
    )
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        content="正文内容",
        word_count=4,
    )
    db_session.add(chapter)
    await db_session.flush()
    return novel, chapter


async def _current_user(db_session) -> User:
    user = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert user is not None
    return user


@pytest.mark.asyncio
async def test_bookmarks_crud_roundtrip(auth_client: AsyncClient, db_session):
    """创建 → 列表 → 删除全链路，字段按 reader_bookmarks 契约返回。"""
    user = await _current_user(db_session)
    novel, chapter = await _seed_novel_with_chapter(db_session, user.id)
    await db_session.commit()

    # 创建
    resp = await auth_client.post(
        f"/api/novels/{novel.id}/bookmarks",
        json={
            "chapter_id": chapter.id,
            "position_percent": 42.5,
            "label": "第一章 关键转折",
            "note": "此处埋下伏笔",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["owner_id"] == user.id
    assert body["novel_id"] == novel.id
    assert body["chapter_id"] == chapter.id
    assert body["position_percent"] == 42.5
    assert body["label"] == "第一章 关键转折"
    assert body["note"] == "此处埋下伏笔"
    bookmark_id = body["id"]

    # 列表（倒序，新书签在前）
    resp = await auth_client.get(f"/api/novels/{novel.id}/bookmarks")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == bookmark_id

    # 删除
    resp = await auth_client.delete(f"/api/novels/{novel.id}/bookmarks/{bookmark_id}")
    assert resp.status_code == 204
    resp = await auth_client.get(f"/api/novels/{novel.id}/bookmarks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_bookmark_create_rejects_foreign_chapter(
    auth_client: AsyncClient, db_session
):
    """跨书 chapter_id 必须 404，防止引用其他小说的章节。"""
    user = await _current_user(db_session)
    novel, _ = await _seed_novel_with_chapter(db_session, user.id)
    await db_session.commit()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/bookmarks",
        json={"chapter_id": 999_999, "position_percent": 10},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "章节不存在"


@pytest.mark.asyncio
async def test_bookmark_delete_owner_scoped(auth_client: AsyncClient, db_session):
    """删除其他用户/其他小说的书签必须 404（owner + novel 双重作用域）。"""
    user = await _current_user(db_session)
    novel, chapter = await _seed_novel_with_chapter(db_session, user.id)
    await db_session.commit()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/bookmarks",
        json={"chapter_id": chapter.id, "position_percent": 12},
    )
    assert resp.status_code == 201
    bookmark_id = resp.json()["id"]

    # 错误的小说作用域
    resp = await auth_client.delete(
        f"/api/novels/{novel.id + 999}/bookmarks/{bookmark_id}"
    )
    assert resp.status_code == 404

    # 错误的书签 ID
    resp = await auth_client.delete(
        f"/api/novels/{novel.id}/bookmarks/{bookmark_id + 999}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bookmark_position_percent_validation(
    auth_client: AsyncClient, db_session
):
    """position_percent 越界（<0 或 >100）应返回 422。"""
    user = await _current_user(db_session)
    novel, chapter = await _seed_novel_with_chapter(db_session, user.id)
    await db_session.commit()

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/bookmarks",
        json={"chapter_id": chapter.id, "position_percent": 150},
    )
    assert resp.status_code == 422

    resp = await auth_client.post(
        f"/api/novels/{novel.id}/bookmarks",
        json={"chapter_id": chapter.id, "position_percent": -1},
    )
    assert resp.status_code == 422
