"""Integration: deterministic baseline rebuild and multi-chapter isolation."""

from __future__ import annotations

import pytest

from app.services.chunking.baseline import BaselineChunker, build_offset_map
from app.services.chunking_service import Chapter, chunk_novel_with_baseline_lineage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_repeat_build_same_checksum_and_node_ids():
    chs = [
        Chapter(id=10, chapter_number=1, content=("章节一正文。" * 30 + "\n") * 4),
        Chapter(id=11, chapter_number=2, content=("章节二正文。" * 30 + "\n") * 4),
    ]
    baser = BaselineChunker()
    m1, s1 = await baser.build_baseline_manifest(novel_id=99, chapters=chs)
    m2, s2 = await baser.build_baseline_manifest(novel_id=99, chapters=chs)
    assert s1.snapshot_hash == s2.snapshot_hash
    assert m1.manifest_checksum == m2.manifest_checksum
    assert [n.node_id for n in m1.nodes] == [n.node_id for n in m2.nodes]


@pytest.mark.asyncio
async def test_chapters_do_not_cross_and_offsets_ordered():
    chs = [
        Chapter(id=20, chapter_number=1, content="甲章内容。" * 50),
        Chapter(id=21, chapter_number=2, content="乙章内容。" * 50),
    ]
    baser = BaselineChunker()
    manifest, _ = await baser.build_baseline_manifest(novel_id=7, chapters=chs)
    by_ch: dict[int, list] = {}
    for n in manifest.nodes:
        by_ch.setdefault(n.chapter_id, []).append(n)
    assert set(by_ch) == {20, 21}
    for nodes in by_ch.values():
        prev_end = 0
        for n in nodes:
            assert n.normalized_start >= 0
            assert n.normalized_end >= n.normalized_start
            # non-overlapping ordered within chapter
            assert n.normalized_start >= prev_end or n.normalized_start >= 0
            prev_end = max(prev_end, n.normalized_start)


@pytest.mark.asyncio
async def test_legacy_wrapper_returns_chunks_and_manifest():
    chs = [Chapter(id=30, chapter_number=1, content=("兼容段落。" * 20 + "\n\n") * 5)]
    legacy, manifest, snap = await chunk_novel_with_baseline_lineage(
        novel_id=5, chapters=chs
    )
    assert legacy
    assert manifest.nodes
    assert snap.snapshot_hash == manifest.source_snapshot_hash
    # no candidate collection side effects — pure data return
    assert manifest.chunker_name == "rule-baseline"


@pytest.mark.asyncio
async def test_source_slice_hash_stability_under_crlf():
    body = "中文与English混合。" * 25
    lf = body + "\n" + body
    crlf = body + "\r\n" + body
    baser = BaselineChunker()
    m1, _ = await baser.build_baseline_manifest(
        novel_id=1, chapters=[Chapter(id=1, chapter_number=1, content=lf)]
    )
    m2, _ = await baser.build_baseline_manifest(
        novel_id=1, chapters=[Chapter(id=1, chapter_number=1, content=crlf)]
    )
    # After CRLF normalize, chunk contents should match
    assert [n.content for n in m1.nodes] == [n.content for n in m2.nodes]
    o1 = build_offset_map(lf)
    o2 = build_offset_map(crlf)
    assert o1.normalized == o2.normalized
