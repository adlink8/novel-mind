"""Selection / chapter / chapter-range validation against server authority.

Re-slices ``Chapter.content`` as the source of truth and rejects stale / forged
client claims with stable ``SelectionValidationError`` codes. Depends on the
data-contract layer (``context_types``) and the progress-snapshot resolver
(``context_queryplan``); it never imports the manifest assemblers.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.novel import Chapter, Novel
from app.schemas.reader_chat import SelectionCoordinate
from app.services.reader_chat.context_queryplan import resolve_progress_snapshot
from app.services.reader_chat.context_types import (
    MAX_RANGE_CONTEXT_CODE_POINTS,
    MAX_SELECTION_CODE_POINTS,
    ProgressSnapshot,
    SelectionValidationError,
    ValidatedChapterRange,
    ValidatedChapterSegment,
    ValidatedSelection,
    code_point_len,
    code_point_slice,
    content_sha256,
)


def narrow_chapter_range(
    chapter_start: int,
    chapter_end: int,
    *,
    cutoff_chapter_number: int,
    full_book: bool,
) -> int:
    """Intersect the requested range with the spoiler cutoff.

    Returns the effective chapter_end. full_book skips truncation entirely;
    a range starting beyond the cutoff is a stable 422 (chapter_beyond_cutoff).
    """

    if chapter_start < 1 or chapter_end < chapter_start:
        raise SelectionValidationError(
            "invalid_chapter_range",
            "chapter_range requires 1 <= chapter_start <= chapter_end",
        )
    if full_book:
        return chapter_end
    if chapter_start > cutoff_chapter_number:
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter range starts beyond the visible reading cutoff",
        )
    return min(chapter_end, cutoff_chapter_number)


def chapter_range_budget(chapter_count: int) -> int:
    """Per-chapter excerpt budget: even split of the bounded total.

    Never exceeds the single-chapter budget per chapter and keeps the sum
    within MAX_RANGE_CONTEXT_CODE_POINTS (no unbounded concatenation).
    """

    if chapter_count < 1:
        raise SelectionValidationError(
            "invalid_chapter_range", "chapter range must contain at least one chapter"
        )
    per_chapter = MAX_RANGE_CONTEXT_CODE_POINTS // chapter_count
    return min(MAX_SELECTION_CODE_POINTS, max(per_chapter, 1))


async def validate_selection(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    selection: SelectionCoordinate,
    hierarchy_build_id: str | None = None,
    hierarchy_checksum: str | None = None,
) -> ValidatedSelection:
    """Re-slice Chapter.content as authority; reject stale/forged client claims."""

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    chapter = await session.scalar(
        select(Chapter)
        .where(
            Chapter.id == selection.chapter_id,
            Chapter.novel_id == novel.id,
        )
        .options(undefer(Chapter.content))
    )
    if chapter is None:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    content = chapter.content or ""
    chapter_hash = content_sha256(content)
    if chapter_hash != selection.chapter_content_hash:
        raise SelectionValidationError(
            "stale_chapter",
            "chapter content hash does not match server authority",
        )

    if selection.source_start < 0 or selection.source_end <= selection.source_start:
        raise SelectionValidationError(
            "invalid_bounds", "source offsets must form a non-empty half-open range"
        )

    length = code_point_len(content)
    if selection.source_end > length:
        raise SelectionValidationError(
            "invalid_bounds", "source_end exceeds chapter content length"
        )

    exact = code_point_slice(content, selection.source_start, selection.source_end)
    if code_point_len(exact) == 0:
        raise SelectionValidationError("empty_selection", "selection is empty")
    if code_point_len(exact) > MAX_SELECTION_CODE_POINTS:
        raise SelectionValidationError(
            "oversized_selection",
            f"selection exceeds {MAX_SELECTION_CODE_POINTS} code points",
        )

    if exact != selection.selection_text:
        raise SelectionValidationError(
            "stale_selection",
            "selection_text does not match exact chapter code-point slice",
        )

    exact_hash = content_sha256(exact)
    if exact_hash != selection.selection_text_hash:
        raise SelectionValidationError(
            "stale_selection",
            "selection_text_hash does not match exact slice hash",
        )

    build_id = hierarchy_build_id or ""
    build_checksum = hierarchy_checksum or ""
    if not build_id or not build_checksum:
        from app.services.reader_chat.retrieval import resolve_active_hierarchy

        meta = await resolve_active_hierarchy(session, novel_id=novel.id)
        if meta is not None:
            build_id, build_checksum = meta
        else:
            build_id = build_id or "none"
            build_checksum = build_checksum or ("0" * 64)

    return ValidatedSelection(
        chapter_id=int(chapter.id),
        chapter_number=int(chapter.chapter_number),
        source_start=int(selection.source_start),
        source_end=int(selection.source_end),
        selection_text=exact,
        selection_text_hash=exact_hash,
        chapter_content_hash=chapter_hash,
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
    )


async def validate_chapter_context(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    chapter_id: int,
) -> ValidatedSelection:
    """Resolve an owned, visible chapter and derive context only from server text."""

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")
    chapter = await session.scalar(
        select(Chapter)
        .where(Chapter.id == chapter_id, Chapter.novel_id == novel.id)
        .options(undefer(Chapter.content))
    )
    if chapter is None:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    progress = await resolve_progress_snapshot(session, novel)
    if (
        not progress.full_book
        and int(chapter.chapter_number) > progress.cutoff_chapter_number
    ):
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter is beyond the visible reading cutoff",
        )

    content = chapter.content or ""
    if not content:
        raise SelectionValidationError("empty_chapter", "chapter content is empty")
    excerpt = content[:MAX_SELECTION_CODE_POINTS]

    from app.services.reader_chat.retrieval import resolve_active_hierarchy

    meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    build_id, build_checksum = meta or ("none", "0" * 64)
    return ValidatedSelection(
        chapter_id=int(chapter.id),
        chapter_number=int(chapter.chapter_number),
        source_start=0,
        source_end=len(excerpt),
        selection_text=excerpt,
        selection_text_hash=content_sha256(excerpt),
        chapter_content_hash=content_sha256(content),
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
    )


async def validate_chapter_range_context(
    session: AsyncSession,
    *,
    novel: Novel,
    owner_id: int,
    chapter_start: int,
    chapter_end: int,
    progress: ProgressSnapshot | None = None,
) -> ValidatedChapterRange:
    """Resolve an owned, cutoff-narrowed chapter interval into budgeted excerpts.

    Chapter-number semantics (inclusive), matching the timeline API. The range
    is intersected with the reading cutoff; full_book skips truncation.
    """

    if novel.owner_id != owner_id:
        raise SelectionValidationError("not_found", "chapter not found for owner scope")

    if progress is None:
        progress = await resolve_progress_snapshot(session, novel)

    effective_end = narrow_chapter_range(
        chapter_start,
        chapter_end,
        cutoff_chapter_number=progress.cutoff_chapter_number,
        full_book=progress.full_book,
    )

    chapters = list(
        (
            await session.scalars(
                select(Chapter)
                .where(
                    Chapter.novel_id == novel.id,
                    Chapter.chapter_number >= chapter_start,
                    Chapter.chapter_number <= effective_end,
                )
                .order_by(Chapter.chapter_number.asc())
                .options(undefer(Chapter.content))
            )
        ).all()
    )
    if not chapters:
        raise SelectionValidationError(
            "not_found", "no chapters found in range for owner scope"
        )

    budget = chapter_range_budget(len(chapters))
    segments: list[ValidatedChapterSegment] = []
    for chapter in chapters:
        content = chapter.content or ""
        if not content:
            continue
        excerpt = content[:budget]
        segments.append(
            ValidatedChapterSegment(
                chapter_id=int(chapter.id),
                chapter_number=int(chapter.chapter_number),
                excerpt=excerpt,
                excerpt_hash=content_sha256(excerpt),
                chapter_content_hash=content_sha256(content),
            )
        )
    if not segments:
        raise SelectionValidationError(
            "empty_chapter", "all chapters in range have empty content"
        )

    from app.services.reader_chat.retrieval import resolve_active_hierarchy

    meta = await resolve_active_hierarchy(session, novel_id=novel.id)
    build_id, build_checksum = meta or ("none", "0" * 64)
    return ValidatedChapterRange(
        chapter_start=int(chapter_start),
        chapter_end=int(effective_end),
        requested_chapter_end=int(chapter_end),
        segments=tuple(segments),
        hierarchy_build_id=build_id,
        hierarchy_checksum=build_checksum,
        progress=progress,
    )
