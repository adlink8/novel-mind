"""Unit tests for the structure-anchored chapter_range contract (25.1-01)."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.reader_chat import (
    ChapterRange,
    ChapterRangeAnchor,
    MessageCreate,
    MessageView,
)
from app.services.reader_chat import context as context_module
from app.services.reader_chat.context import (
    MAX_RANGE_CONTEXT_CODE_POINTS,
    MAX_SELECTION_CODE_POINTS,
    ProgressSnapshot,
    SelectionValidationError,
    ValidatedChapterRange,
    ValidatedChapterSegment,
    assemble_range_context_manifest,
    chapter_range_budget,
    narrow_chapter_range,
    validate_chapter_range_context,
)
from app.services.reader_chat.conversations import (
    ProductionContextBuilder,
    anchor_view_from_prompt_inputs,
)
from app.services.reader_chat.retrieval import RetrievalResult, RetrievedEvidence

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# MessageCreate / ChapterRange request validation
# ---------------------------------------------------------------------------


def test_message_create_accepts_chapter_range_alone():
    msg = MessageCreate.model_validate(
        {
            "client_message_id": "range-1",
            "body": "第 2 到 5 章讲了什么？",
            "chapter_range": {"chapter_start": 2, "chapter_end": 5},
        }
    )
    assert msg.chapter_range is not None
    assert msg.chapter_range.chapter_start == 2
    assert msg.chapter_range.chapter_end == 5
    assert msg.chapter_id is None
    assert msg.selection is None


def test_chapter_range_rejects_inverted_or_non_positive_bounds():
    with pytest.raises(ValidationError):
        ChapterRange.model_validate({"chapter_start": 5, "chapter_end": 2})
    with pytest.raises(ValidationError):
        ChapterRange.model_validate({"chapter_start": 0, "chapter_end": 3})
    single = ChapterRange.model_validate({"chapter_start": 3, "chapter_end": 3})
    assert single.chapter_start == single.chapter_end == 3


def test_message_create_rejects_chapter_range_with_other_anchors():
    with pytest.raises(ValidationError):
        MessageCreate.model_validate(
            {
                "client_message_id": "mix-1",
                "body": "?",
                "chapter_id": 7,
                "chapter_range": {"chapter_start": 1, "chapter_end": 3},
            }
        )
    with pytest.raises(ValidationError):
        MessageCreate.model_validate(
            {
                "client_message_id": "mix-2",
                "body": "?",
                "chapter_range": {"chapter_start": 1, "chapter_end": 3},
                "selection": {
                    "chapter_id": 1,
                    "source_start": 0,
                    "source_end": 1,
                    "selection_text": "x",
                    "selection_text_hash": HEX64_A,
                    "chapter_content_hash": HEX64_A,
                },
            }
        )


def test_message_create_still_requires_some_anchor():
    with pytest.raises(ValidationError):
        MessageCreate.model_validate({"client_message_id": "none-1", "body": "?"})


# ---------------------------------------------------------------------------
# Cutoff narrowing semantics
# ---------------------------------------------------------------------------


def test_narrow_chapter_range_intersects_with_cutoff():
    assert narrow_chapter_range(2, 9, cutoff_chapter_number=5, full_book=False) == 5


def test_narrow_chapter_range_keeps_range_within_cutoff():
    assert narrow_chapter_range(1, 5, cutoff_chapter_number=5, full_book=False) == 5
    assert narrow_chapter_range(1, 4, cutoff_chapter_number=5, full_book=False) == 4
    # Exact boundary: start == cutoff is still visible.
    assert narrow_chapter_range(5, 9, cutoff_chapter_number=5, full_book=False) == 5


def test_narrow_chapter_range_rejects_start_beyond_cutoff():
    with pytest.raises(SelectionValidationError) as exc:
        narrow_chapter_range(6, 9, cutoff_chapter_number=5, full_book=False)
    assert exc.value.code == "chapter_beyond_cutoff"


def test_narrow_chapter_range_full_book_skips_truncation():
    assert narrow_chapter_range(2, 9, cutoff_chapter_number=5, full_book=True) == 9
    assert narrow_chapter_range(6, 9, cutoff_chapter_number=5, full_book=True) == 9


def test_narrow_chapter_range_rejects_invalid_interval():
    with pytest.raises(SelectionValidationError) as exc:
        narrow_chapter_range(0, 3, cutoff_chapter_number=5, full_book=False)
    assert exc.value.code == "invalid_chapter_range"
    with pytest.raises(SelectionValidationError):
        narrow_chapter_range(4, 3, cutoff_chapter_number=5, full_book=True)


# ---------------------------------------------------------------------------
# Per-chapter budget allocation (bounded total, no unbounded concatenation)
# ---------------------------------------------------------------------------


def test_chapter_range_budget_single_chapter_matches_single_chapter_cap():
    assert chapter_range_budget(1) == MAX_SELECTION_CODE_POINTS


def test_chapter_range_budget_splits_total_evenly_and_stays_bounded():
    assert chapter_range_budget(2) == MAX_SELECTION_CODE_POINTS
    assert chapter_range_budget(4) == MAX_RANGE_CONTEXT_CODE_POINTS // 4
    for n in (2, 3, 4, 10, 100, 515):
        per = chapter_range_budget(n)
        assert per >= 1
        assert per <= MAX_SELECTION_CODE_POINTS
        assert per * n <= MAX_RANGE_CONTEXT_CODE_POINTS

    with pytest.raises(SelectionValidationError):
        chapter_range_budget(0)


# ---------------------------------------------------------------------------
# validate_chapter_range_context
# ---------------------------------------------------------------------------


def _chapters(novel_id: int, count: int, content_len: int = 100):
    return [
        SimpleNamespace(
            id=100 + n,
            novel_id=novel_id,
            chapter_number=n,
            content=f"第{n}章" + "字" * content_len,
        )
        for n in range(1, count + 1)
    ]


def _session_with_chapters(chapters):
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = chapters
    session.scalars = AsyncMock(return_value=result)
    session.scalar = AsyncMock(return_value=None)  # no active hierarchy pointer
    return session


def _progress(cutoff: int, full_book: bool = False) -> ProgressSnapshot:
    return ProgressSnapshot(
        chapter_id=None,
        cutoff_chapter_number=cutoff,
        timeline_full_book=full_book,
        full_book=full_book,
    )


@pytest.mark.asyncio
async def test_validate_chapter_range_narrows_end_and_builds_segments():
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    chapters = [c for c in _chapters(2, 9) if 2 <= c.chapter_number <= 5]
    session = _session_with_chapters(chapters)

    validated = await validate_chapter_range_context(
        session,
        novel=novel,
        owner_id=1,
        chapter_start=2,
        chapter_end=9,
        progress=_progress(cutoff=5),
    )
    assert validated.chapter_start == 2
    assert validated.chapter_end == 5  # narrowed to cutoff
    assert validated.requested_chapter_end == 9
    assert [s.chapter_number for s in validated.segments] == [2, 3, 4, 5]
    budget = chapter_range_budget(4)
    total = 0
    for seg in validated.segments:
        assert len(seg.excerpt) <= budget
        assert seg.excerpt_hash == _sha(seg.excerpt)
        total += len(seg.excerpt)
    assert total <= MAX_RANGE_CONTEXT_CODE_POINTS


@pytest.mark.asyncio
async def test_validate_chapter_range_rejects_start_beyond_cutoff():
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    session = _session_with_chapters([])
    with pytest.raises(SelectionValidationError) as exc:
        await validate_chapter_range_context(
            session,
            novel=novel,
            owner_id=1,
            chapter_start=6,
            chapter_end=9,
            progress=_progress(cutoff=5),
        )
    assert exc.value.code == "chapter_beyond_cutoff"


@pytest.mark.asyncio
async def test_validate_chapter_range_full_book_keeps_requested_end():
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    chapters = [c for c in _chapters(2, 9) if 6 <= c.chapter_number <= 9]
    session = _session_with_chapters(chapters)
    validated = await validate_chapter_range_context(
        session,
        novel=novel,
        owner_id=1,
        chapter_start=6,
        chapter_end=9,
        progress=_progress(cutoff=5, full_book=True),
    )
    assert validated.chapter_end == 9
    assert [s.chapter_number for s in validated.segments] == [6, 7, 8, 9]


@pytest.mark.asyncio
async def test_validate_chapter_range_rejects_foreign_owner_and_empty_range():
    novel = SimpleNamespace(id=2, owner_id=42, reading_progress={})
    session = _session_with_chapters([])
    with pytest.raises(SelectionValidationError) as exc:
        await validate_chapter_range_context(
            session,
            novel=novel,
            owner_id=1,
            chapter_start=1,
            chapter_end=2,
            progress=_progress(cutoff=5),
        )
    assert exc.value.code == "not_found"

    owned = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    with pytest.raises(SelectionValidationError) as exc:
        await validate_chapter_range_context(
            session,
            novel=owned,
            owner_id=1,
            chapter_start=1,
            chapter_end=2,
            progress=_progress(cutoff=5),
        )
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_validate_chapter_range_per_chapter_budget_truncates_long_chapters():
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    long_chapters = [
        SimpleNamespace(
            id=100 + n,
            novel_id=2,
            chapter_number=n,
            content="长" * (MAX_SELECTION_CODE_POINTS + 500),
        )
        for n in range(1, 5)
    ]
    session = _session_with_chapters(long_chapters)
    validated = await validate_chapter_range_context(
        session,
        novel=novel,
        owner_id=1,
        chapter_start=1,
        chapter_end=4,
        progress=_progress(cutoff=10),
    )
    budget = chapter_range_budget(4)
    assert all(len(s.excerpt) == budget for s in validated.segments)
    assert sum(len(s.excerpt) for s in validated.segments) <= (
        MAX_RANGE_CONTEXT_CODE_POINTS
    )


# ---------------------------------------------------------------------------
# assemble_range_context_manifest aggregation
# ---------------------------------------------------------------------------


def _validated_range(
    start: int, end: int, cutoff: int, full_book: bool = False
) -> ValidatedChapterRange:
    segments = tuple(
        ValidatedChapterSegment(
            chapter_id=100 + n,
            chapter_number=n,
            excerpt=f"第{n}章可见节选",
            excerpt_hash=_sha(f"第{n}章可见节选"),
            chapter_content_hash=_sha(f"第{n}章全文"),
        )
        for n in range(start, end + 1)
    )
    return ValidatedChapterRange(
        chapter_start=start,
        chapter_end=end,
        requested_chapter_end=end,
        segments=segments,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_A,
        progress=_progress(cutoff=cutoff, full_book=full_book),
    )


def _retrieved(chapter_number: int, key: str) -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_key=key,
        source_type="hierarchy",
        source_id=key,
        chapter_id=200 + chapter_number,
        chapter_number=chapter_number,
        source_start=0,
        source_end=4,
        content_hash=HEX64_A,
        excerpt="证据节选",
        version_lineage={},
    )


def _fake_retrieval(items):
    async def fake(session, **kwargs):
        return RetrievalResult(
            items=list(items),
            omitted_counts={"hierarchy": 0},
            source_status={"hierarchy": "ok"},
            hierarchy_build_id="build-1",
            hierarchy_checksum=HEX64_A,
            analysis_version_id=None,
        )

    return fake


@pytest.mark.asyncio
async def test_assemble_range_manifest_aggregates_chapters_and_filters_range(
    monkeypatch,
):
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    validated = _validated_range(2, 4, cutoff=5)
    items = [
        _retrieved(3, "hierarchy:n-in"),
        _retrieved(1, "hierarchy:n-before"),  # below chapter_start -> dropped
        _retrieved(9, "hierarchy:n-after"),  # beyond effective end -> dropped
    ]
    monkeypatch.setattr(
        context_module, "retrieve_visible_evidence", _fake_retrieval(items)
    )

    manifest = await assemble_range_context_manifest(
        AsyncMock(),
        novel=novel,
        owner_id=1,
        chapter_range=validated,
        question="第 2-4 章的主线？",
    )

    keys = [e.evidence_key for e in manifest.evidence]
    assert keys[:3] == ["chapter:102", "chapter:103", "chapter:104"]
    assert "hierarchy:n-in" in keys
    assert "hierarchy:n-before" not in keys
    assert "hierarchy:n-after" not in keys
    assert len(keys) == len(set(keys))

    assert manifest.prompt_inputs["context_mode"] == "chapter_range"
    assert manifest.prompt_inputs["anchor"] == {
        "kind": "chapter_range",
        "chapter_start": 2,
        "chapter_end": 4,
    }
    assert manifest.prompt_inputs["allowed_evidence_ids"] == keys
    assert manifest.cutoff_chapter_number == 5
    assert len(manifest.manifest_checksum) == 64

    # Deterministic checksum for identical inputs.
    again = await assemble_range_context_manifest(
        AsyncMock(),
        novel=novel,
        owner_id=1,
        chapter_range=validated,
        question="第 2-4 章的主线？",
    )
    assert again.manifest_checksum == manifest.manifest_checksum


@pytest.mark.asyncio
async def test_assemble_range_manifest_rejects_forged_client_refs(monkeypatch):
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    monkeypatch.setattr(
        context_module, "retrieve_visible_evidence", _fake_retrieval([])
    )
    with pytest.raises(SelectionValidationError) as exc:
        await assemble_range_context_manifest(
            AsyncMock(),
            novel=novel,
            owner_id=1,
            chapter_range=_validated_range(2, 4, cutoff=5),
            client_evidence_keys=["hierarchy:forged"],
        )
    assert exc.value.code == "forged_evidence_refs"


# ---------------------------------------------------------------------------
# Production builder error mapping (stable 422 error code)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_builder_maps_beyond_cutoff_to_422_code(monkeypatch):
    async def raise_beyond_cutoff(session, **kwargs):
        raise SelectionValidationError(
            "chapter_beyond_cutoff",
            "chapter range starts beyond the visible reading cutoff",
        )

    monkeypatch.setattr(
        context_module, "validate_chapter_range_context", raise_beyond_cutoff
    )
    builder = ProductionContextBuilder()
    novel = SimpleNamespace(id=2, owner_id=1, reading_progress={})
    with pytest.raises(HTTPException) as exc:
        await builder.build(
            AsyncMock(),
            novel=novel,
            owner_id=1,
            conversation_id=1,
            selection=None,
            body="?",
            chapter_range=ChapterRange(chapter_start=6, chapter_end=9),
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "chapter_beyond_cutoff"


# ---------------------------------------------------------------------------
# Anchor echo
# ---------------------------------------------------------------------------


def test_anchor_view_round_trips_from_prompt_inputs():
    anchor = anchor_view_from_prompt_inputs(
        {"anchor": {"kind": "chapter_range", "chapter_start": 2, "chapter_end": 5}}
    )
    assert isinstance(anchor, ChapterRangeAnchor)
    assert anchor.kind == "chapter_range"
    assert anchor.chapter_start == 2
    assert anchor.chapter_end == 5


def test_anchor_view_ignores_missing_or_malformed_anchor():
    assert anchor_view_from_prompt_inputs(None) is None
    assert anchor_view_from_prompt_inputs({}) is None
    assert anchor_view_from_prompt_inputs({"anchor": "chapter_range"}) is None
    assert anchor_view_from_prompt_inputs({"anchor": {"kind": "selection"}}) is None
    assert (
        anchor_view_from_prompt_inputs(
            {"anchor": {"kind": "chapter_range", "chapter_start": "x"}}
        )
        is None
    )
    assert (
        anchor_view_from_prompt_inputs(
            {"anchor": {"kind": "chapter_range", "chapter_start": 0, "chapter_end": 2}}
        )
        is None
    )


def test_message_view_serializes_optional_anchor():
    view = MessageView.model_validate(
        {
            "id": 1,
            "conversation_id": 2,
            "sequence": 1,
            "role": "user",
            "body": "第 2-5 章?",
            "anchor": {
                "kind": "chapter_range",
                "chapter_start": 2,
                "chapter_end": 5,
            },
            "created_at": "2026-07-26T00:00:00Z",
        }
    )
    assert view.anchor is not None
    assert view.anchor.chapter_end == 5
    dumped = view.model_dump(mode="json")
    assert dumped["anchor"]["kind"] == "chapter_range"

    without = MessageView.model_validate(
        {
            "id": 1,
            "conversation_id": 2,
            "sequence": 1,
            "role": "user",
            "body": "?",
            "created_at": "2026-07-26T00:00:00Z",
        }
    )
    assert without.anchor is None
