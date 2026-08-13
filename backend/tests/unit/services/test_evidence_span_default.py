"""get_evidence_span 默认服务：content_hash 可选化（Slice A1）。

契约：
  - 省略 content_hash → 服务端从原文切片确定性计算并返回（模型无需自行算 hash）；
  - 提供且匹配 → 正常返回；
  - 提供但不匹配 → InvalidInputError（fail closed，绝不返回错误切片）；
  - 非法 offsets / 章节缺失 → 维持原有 fail-closed 行为。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.agent_tools import _defaults_world
from app.services.agent_tools.errors import InvalidInputError
from app.services.queryplan.adapters import chapter_content_hash
from app.services.queryplan.contracts import leaf_evidence_key

pytestmark = pytest.mark.unit

CHAPTER_CONTENT = "夜色笼罩着庭院，林安握紧了剑。" * 20


@dataclass(frozen=True)
class _FakeChapter:
    id: int
    novel_id: int
    chapter_number: int
    content: str


def _patch_chapter(monkeypatch: pytest.MonkeyPatch, chapter: _FakeChapter | None):
    async def fake_get_chapter(db, chapter_id):
        return chapter

    monkeypatch.setattr(
        _defaults_world.novel_service, "get_chapter", fake_get_chapter
    )


async def test_content_hash_omitted_is_computed_server_side(
    monkeypatch: pytest.MonkeyPatch,
):
    """省略 content_hash：服务端计算并返回与切片匹配的真实 hash。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    span = await _defaults_world._default_get_evidence_span(
        object(),
        chapter_id=7,
        source_start=0,
        source_end=10,
        content_hash=None,
    )

    assert span is not None
    expected_hash = chapter_content_hash(CHAPTER_CONTENT[0:10])
    assert span["content_hash"] == expected_hash
    assert span["evidence_key"] == leaf_evidence_key(
        chapter_id=7,
        source_start=0,
        source_end=10,
        content_hash=expected_hash,
    )
    assert span["excerpt"] == CHAPTER_CONTENT[0:10]


async def test_content_hash_provided_and_matching_still_accepted(
    monkeypatch: pytest.MonkeyPatch,
):
    """提供且匹配：行为不变。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    span = await _defaults_world._default_get_evidence_span(
        object(),
        chapter_id=7,
        source_start=0,
        source_end=10,
        content_hash=chapter_content_hash(CHAPTER_CONTENT[0:10]),
    )

    assert span is not None
    assert span["chapter_number"] == 2


async def test_content_hash_provided_and_mismatched_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    """提供但不匹配：fail closed（防漂移语义不变）。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    with pytest.raises(InvalidInputError):
        await _defaults_world._default_get_evidence_span(
            object(),
            chapter_id=7,
            source_start=0,
            source_end=10,
            content_hash="0" * 64,
        )


async def test_invalid_offsets_still_fail_closed(monkeypatch: pytest.MonkeyPatch):
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    with pytest.raises(InvalidInputError):
        await _defaults_world._default_get_evidence_span(
            object(),
            chapter_id=7,
            source_start=10,
            source_end=10,
            content_hash=None,
        )


async def test_missing_chapter_returns_none(monkeypatch: pytest.MonkeyPatch):
    _patch_chapter(monkeypatch, None)

    span = await _defaults_world._default_get_evidence_span(
        object(),
        chapter_id=999,
        source_start=0,
        source_end=10,
        content_hash=None,
    )

    assert span is None
