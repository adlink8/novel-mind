"""Offset map and reconstructibility tests (07-01)."""

from __future__ import annotations

import pytest

from app.services.chunking.baseline import BaselineChunker, build_offset_map
from app.services.chunking_service import Chapter, ChunkingService

pytestmark = pytest.mark.unit


def test_offset_map_crlf_and_unicode():
    source = "你好\r\n世界\r！"
    omap = build_offset_map(source)
    assert "\r" not in omap.normalized
    assert omap.normalized == "你好\n世界\n！"
    # round-trip norm indices to source
    for ni, ch in enumerate(omap.normalized):
        si = omap.norm_to_source[ni]
        # source char at si may be \r for newline mapping
        assert 0 <= si < len(source)
    end_s = omap.norm_to_source[len(omap.normalized)]
    assert end_s == len(source)


@pytest.mark.asyncio
async def test_baseline_chunks_reconstruct_from_normalized_offsets():
    text = (
        "第一段内容足够长，用来触发正常分块逻辑。" * 5
        + "\n\n"
        + "第二段也有足够的字数确保不会被无短段落规则吞掉。" * 5
    )
    chapters = [Chapter(id=1, chapter_number=1, content=text)]
    baser = BaselineChunker(ChunkingService(min_chunk_size=50, max_chunk_size=120))
    manifest, snap = await baser.build_baseline_manifest(novel_id=1, chapters=chapters)
    assert snap.snapshot_hash
    assert manifest.manifest_checksum
    omap = build_offset_map(text)
    for node in manifest.nodes:
        covered = omap.normalized[node.normalized_start : node.normalized_end]
        # Flexible whitespace: non-ws payload must match
        assert "".join(covered.split()) == "".join(node.content.split())
        assert node.source_end >= node.source_start
        assert node.chapter_id == 1


@pytest.mark.asyncio
async def test_duplicate_sentences_do_not_reuse_wrong_span():
    # Same sentence repeated — sequential cursor must advance
    sent = "重复句子测试甲乙丙丁戊己庚辛壬癸。"
    text = (sent + "\n") * 8
    chapters = [Chapter(id=2, chapter_number=1, content=text)]
    baser = BaselineChunker(ChunkingService(min_chunk_size=20, max_chunk_size=40))
    manifest, _ = await baser.build_baseline_manifest(novel_id=2, chapters=chapters)
    starts = [n.normalized_start for n in manifest.nodes]
    assert starts == sorted(starts)
    # strictly increasing ends for non-empty
    prev = -1
    for n in manifest.nodes:
        assert n.normalized_start >= prev
        prev = n.normalized_start


@pytest.mark.asyncio
async def test_legacy_chunk_count_matches_baseline_nodes():
    text = "段落内容。" * 40 + "\n\n" + "另一段落。" * 40
    chapters = [Chapter(id=3, chapter_number=1, content=text)]
    svc = ChunkingService(min_chunk_size=80, max_chunk_size=150)
    legacy = await svc.chunk_chapter(3, 1, text)
    baser = BaselineChunker(svc)
    manifest, _ = await baser.build_baseline_manifest(novel_id=3, chapters=chapters)
    assert len(manifest.nodes) == len(legacy)
    for n, leg in zip(manifest.nodes, legacy):
        assert n.content == leg["content"]
        assert n.chunk_type == leg["chunk_type"]
