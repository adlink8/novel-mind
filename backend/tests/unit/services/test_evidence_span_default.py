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

    monkeypatch.setattr(_defaults_world.novel_service, "get_chapter", fake_get_chapter)


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


@dataclass(frozen=True)
class _FakeChunk:
    id: int
    novel_id: int
    chapter_id: int
    content: str


class _FakeDb:
    def __init__(self, chunk):
        self._chunk = chunk

    async def get(self, model, pk):
        return self._chunk if self._chunk is not None and self._chunk.id == pk else None


async def test_chunk_id_resolves_offsets_deterministically(
    monkeypatch: pytest.MonkeyPatch,
):
    """chunk_id 通道：服务端在章节原文中定位 chunk 内容，推导 half-open offsets。

    text_chunks.source_start/source_end 多为 NULL（E2E 实测 16/19395 有值），
    但 chunk.content 是章节原文的子串——offsets 可以确定性重放，模型无需
    数字符（LLM 不擅长），消除 get_evidence_span 422/404 的主要根因。
    """
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)
    chunk_text = CHAPTER_CONTENT[10:30]
    chunk = _FakeChunk(id=55, novel_id=3, chapter_id=7, content=chunk_text)

    span = await _defaults_world._default_get_evidence_span(
        _FakeDb(chunk),
        chapter_id=7,
        source_start=None,
        source_end=None,
        content_hash=None,
        chunk_id=55,
    )

    assert span is not None
    assert span["source_start"] == 10
    assert span["source_end"] == 30
    assert span["excerpt"] == chunk_text
    assert span["content_hash"] == chapter_content_hash(chunk_text)


async def test_chunk_id_content_drift_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    """chunk 内容不在章节原文中（索引漂移）→ InvalidInputError。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)
    chunk = _FakeChunk(id=55, novel_id=3, chapter_id=7, content="不存在的文本")

    with pytest.raises(InvalidInputError):
        await _defaults_world._default_get_evidence_span(
            _FakeDb(chunk),
            chapter_id=7,
            source_start=None,
            source_end=None,
            content_hash=None,
            chunk_id=55,
        )


async def test_chunk_id_missing_or_wrong_chapter_returns_none(
    monkeypatch: pytest.MonkeyPatch,
):
    """chunk 缺失 / chunk 不属于该章节 → None（404-hide，与章节缺失同一纪律）。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    span = await _defaults_world._default_get_evidence_span(
        _FakeDb(None),
        chapter_id=7,
        source_start=None,
        source_end=None,
        content_hash=None,
        chunk_id=999,
    )
    assert span is None

    wrong = _FakeChunk(id=55, novel_id=3, chapter_id=8, content=CHAPTER_CONTENT[0:5])
    span = await _defaults_world._default_get_evidence_span(
        _FakeDb(wrong),
        chapter_id=7,
        source_start=None,
        source_end=None,
        content_hash=None,
        chunk_id=55,
    )
    assert span is None


async def test_offsets_path_unchanged_when_chunk_id_absent(
    monkeypatch: pytest.MonkeyPatch,
):
    """向后兼容：无 chunk_id 时 offsets 路径行为不变。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)

    span = await _defaults_world._default_get_evidence_span(
        object(),
        chapter_id=7,
        source_start=0,
        source_end=10,
        content_hash=None,
        chunk_id=None,
    )
    assert span is not None
    assert span["source_start"] == 0


async def test_chunk_id_whitespace_normalized_match(
    monkeypatch: pytest.MonkeyPatch,
):
    """chunker 折叠过空白的 chunk：规范化回退命中，offsets 映射回原文切片。"""
    chapter = _FakeChapter(id=7, novel_id=3, chapter_number=2, content=CHAPTER_CONTENT)
    _patch_chapter(monkeypatch, chapter)
    # 取原文 10:30，人为把其中的字符间插入换行模拟 chunker 空白折叠
    raw = CHAPTER_CONTENT[10:30]
    folded = chr(10).join(raw[:10]) + chr(10) * 2 + raw[10:]
    chunk = _FakeChunk(id=56, novel_id=3, chapter_id=7, content=folded)

    span = await _defaults_world._default_get_evidence_span(
        _FakeDb(chunk),
        chapter_id=7,
        source_start=None,
        source_end=None,
        content_hash=None,
        chunk_id=56,
    )

    assert span is not None
    # 映射回原文：起点是原文字符，excerpt 是原文真实切片（含原始空白）
    assert span["excerpt"].replace(chr(10), "") == folded.replace(chr(10), "")
    assert span["source_start"] >= 10
    assert span["source_end"] > span["source_start"]
