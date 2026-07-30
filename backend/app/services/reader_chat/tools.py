"""Read-only tools exposed to the reader-chat model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.services.hybrid_search import hybrid_search_service

MAX_SEARCH_QUERY_LENGTH = 240
MAX_SEARCH_TOP_K = 8
MAX_SEARCH_TEXT_LENGTH = 1800
MAX_SEARCH_TOTAL_LENGTH = 9000

READER_CHAT_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_novel_text",
            "description": (
                "Search visible original novel passages by meaning and keywords. "
                "Use this when the supplied context does not contain the passage "
                "needed to answer the reader's question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A focused search query in the novel's language.",
                        "minLength": 1,
                        "maxLength": MAX_SEARCH_QUERY_LENGTH,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of passages to return, from 1 to 8.",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_TOP_K,
                        "default": 5,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }
]


def _bounded_query(value: Any) -> str:
    return str(value or "").strip()[:MAX_SEARCH_QUERY_LENGTH]


def _bounded_top_k(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 5
    return max(1, min(MAX_SEARCH_TOP_K, parsed))


async def search_novel_text(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    cutoff_chapter_number: int,
    full_book: bool,
    query: Any,
    top_k: Any = 5,
) -> dict[str, Any]:
    """Search original text inside the frozen owner/novel visibility boundary."""

    normalized_query = _bounded_query(query)
    if not normalized_query:
        return {"results": [], "error": "query_required"}

    owned_novel_id = await session.scalar(
        select(Novel.id).where(
            Novel.id == novel_id,
            Novel.owner_id == owner_id,
        )
    )
    if owned_novel_id is None:
        return {"results": [], "error": "novel_not_found"}

    requested_top_k = _bounded_top_k(top_k)
    raw_results = await hybrid_search_service.search_novel(
        session,
        novel_id=novel_id,
        query=normalized_query,
        top_k=min(MAX_SEARCH_TOP_K * 2, requested_top_k * 2),
    )

    chunk_ids = {
        int(item["chunk_id"])
        for item in raw_results
        if isinstance(item, dict) and item.get("chunk_id") is not None
    }
    chunks_by_id: dict[int, TextChunk] = {}
    if chunk_ids:
        chunks = await session.scalars(
            select(TextChunk).where(
                TextChunk.novel_id == novel_id,
                TextChunk.id.in_(chunk_ids),
            )
        )
        chunks_by_id = {int(chunk.id): chunk for chunk in chunks.all()}

    chapter_ids = {
        int(item["chapter_id"])
        for item in raw_results
        if isinstance(item, dict) and item.get("chapter_id") is not None
    }
    chapter_by_id: dict[int, Chapter] = {}
    if chapter_ids:
        chapters = await session.scalars(
            select(Chapter).where(
                Chapter.novel_id == novel_id,
                Chapter.id.in_(chapter_ids),
            )
        )
        chapter_by_id = {int(chapter.id): chapter for chapter in chapters.all()}

    results: list[dict[str, Any]] = []
    total_length = 0
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        try:
            chunk_id = int(item.get("chunk_id"))
            chapter_id = int(item.get("chapter_id"))
        except (TypeError, ValueError):
            continue

        chunk = chunks_by_id.get(chunk_id)
        chapter = chapter_by_id.get(chapter_id)
        if chunk is None or chapter is None or chunk.chapter_id != chapter.id:
            continue
        chapter_number = int(chapter.chapter_number)
        if not full_book and chapter_number > int(cutoff_chapter_number):
            continue

        text = str(item.get("content") or chunk.content or "").strip()
        if not text:
            continue
        text = text[:MAX_SEARCH_TEXT_LENGTH]
        if total_length + len(text) > MAX_SEARCH_TOTAL_LENGTH:
            break

        source_start = item.get("source_start")
        if source_start is None:
            source_start = chunk.source_start
        try:
            source_start = max(0, int(source_start or 0))
        except (TypeError, ValueError):
            source_start = 0
        source_end = item.get("source_end")
        if source_end is None:
            source_end = chunk.source_end
        try:
            source_end = max(source_start + 1, int(source_end or 0))
        except (TypeError, ValueError):
            source_end = source_start + len(text)

        results.append(
            {
                "chunk_id": chunk_id,
                "chapter_id": chapter_id,
                "chapter_number": chapter_number,
                "chapter_title": chapter.title or "",
                "chunk_index": int(chunk.chunk_index),
                "source_start": source_start,
                "source_end": source_end,
                "text": text,
                "score": float(item.get("score") or 0.0),
            }
        )
        total_length += len(text)
        if len(results) >= requested_top_k:
            break

    return {
        "query": normalized_query,
        "results": results,
        "scope": {
            "novel_id": novel_id,
            "visible_through_chapter": None if full_book else int(cutoff_chapter_number),
        },
    }
