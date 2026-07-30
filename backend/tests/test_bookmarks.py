"""阅读器选区书签 API 测试。"""

import hashlib
import io

import pytest
from httpx import AsyncClient

from app.models.bookmark import Bookmark

pytestmark = pytest.mark.unit


def test_bookmark_model_matches_persisted_schema() -> None:
    """ORM 必须映射现有 bookmarks 表，而不是不存在的 reader_bookmarks。"""
    assert Bookmark.__tablename__ == "bookmarks"
    assert set(Bookmark.__table__.columns.keys()) >= {
        "id",
        "owner_id",
        "novel_id",
        "chapter_id",
        "source_start",
        "source_end",
        "selected_text",
        "selection_text_hash",
        "chapter_content_hash",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_bookmark_persists_exact_selected_range(auth_client: AsyncClient):
    content = "第一章 测试\n\n阿宁走进旧书店，翻开了泛黄的书页。\n"
    upload = await auth_client.post(
        "/api/novels/upload",
        files={"file": ("bookmark-test.txt", io.BytesIO(content.encode()), "text/plain")},
    )
    assert upload.status_code == 200
    novel_id = upload.json()["novel_id"]
    assert novel_id is not None

    chapters = (await auth_client.get(f"/api/novels/{novel_id}/chapters")).json()
    chapter_id = chapters[0]["id"]
    chapter = (
        await auth_client.get(f"/api/novels/{novel_id}/chapters/{chapter_id}")
    ).json()
    selected = "阿宁走进旧书店"
    start = chapter["content"].index(selected)
    end = start + len(selected)
    payload = {
        "chapter_id": chapter_id,
        "source_start": start,
        "source_end": end,
        "selection_text": selected,
        "selection_text_hash": hashlib.sha256(selected.encode()).hexdigest(),
        "chapter_content_hash": hashlib.sha256(
            chapter["content"].encode()
        ).hexdigest(),
    }

    created = await auth_client.post(f"/api/novels/{novel_id}/bookmarks", json=payload)
    assert created.status_code == 201
    bookmark = created.json()
    assert bookmark["chapter_id"] == chapter_id
    assert bookmark["source_start"] == start
    assert bookmark["source_end"] == end
    assert bookmark["selected_text"] == selected

    listed = await auth_client.get(f"/api/novels/{novel_id}/bookmarks")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == bookmark["id"]

    deleted = await auth_client.delete(
        f"/api/novels/{novel_id}/bookmarks/{bookmark['id']}"
    )
    assert deleted.status_code == 204
    assert (await auth_client.get(f"/api/novels/{novel_id}/bookmarks")).json() == []
