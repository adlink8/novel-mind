from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.reader_chat.tools import (
    MAX_SEARCH_TOP_K,
    READER_CHAT_TOOLS,
    search_novel_text,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_search_novel_text_enforces_owner_and_visible_chapter_boundary(monkeypatch):
    session = AsyncMock()
    session.scalar.return_value = 7
    visible_chunk = SimpleNamespace(
        id=11,
        novel_id=7,
        chapter_id=101,
        chunk_index=2,
        content="可见章节原文",
        source_start=20,
        source_end=26,
    )
    future_chunk = SimpleNamespace(
        id=12,
        novel_id=7,
        chapter_id=102,
        chunk_index=0,
        content="未来章节原文",
        source_start=0,
        source_end=6,
    )
    visible_chapter = SimpleNamespace(id=101, novel_id=7, chapter_number=3, title="第三章")
    future_chapter = SimpleNamespace(id=102, novel_id=7, chapter_number=4, title="第四章")
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [visible_chunk, future_chunk]),
        SimpleNamespace(all=lambda: [visible_chapter, future_chapter]),
    ]
    monkeypatch.setattr(
        "app.services.reader_chat.tools.hybrid_search_service.search_novel",
        AsyncMock(
            return_value=[
                {"chunk_id": 11, "chapter_id": 101, "content": "可见章节原文", "score": 0.9},
                {"chunk_id": 12, "chapter_id": 102, "content": "未来章节原文", "score": 0.8},
            ]
        ),
    )

    result = await search_novel_text(
        session,
        owner_id=1,
        novel_id=7,
        cutoff_chapter_number=3,
        full_book=False,
        query="伏笔",
        top_k=MAX_SEARCH_TOP_K + 20,
    )

    assert [item["chapter_number"] for item in result["results"]] == [3]
    assert result["results"][0]["text"] == "可见章节原文"


@pytest.mark.asyncio
async def test_search_novel_text_fails_closed_for_unowned_novel():
    session = AsyncMock()
    session.scalar.return_value = None

    result = await search_novel_text(
        session,
        owner_id=99,
        novel_id=7,
        cutoff_chapter_number=1,
        full_book=False,
        query="秘密",
    )

    assert result == {"results": [], "error": "novel_not_found"}
    session.scalars.assert_not_awaited()


def test_search_tool_contract_is_bounded_and_read_only():
    function = READER_CHAT_TOOLS[0]["function"]
    assert function["name"] == "search_novel_text"
    assert function["parameters"]["properties"]["top_k"]["maximum"] == MAX_SEARCH_TOP_K
    assert function["parameters"]["additionalProperties"] is False
