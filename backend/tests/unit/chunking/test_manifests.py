"""Unit tests for chunk manifest contracts and deterministic IDs (07-01)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.chunking.manifests import (
    build_chapter_source,
    build_manifest,
    build_source_snapshot,
    config_hash,
    content_hash,
    make_node_id,
)
from app.services.chunking.schemas import (
    ChunkerConfig,
    RawChunkNode,
)

pytestmark = pytest.mark.unit


def test_content_hash_stable_and_sensitive():
    h1 = content_hash("你好世界")
    h2 = content_hash("你好世界")
    h3 = content_hash("你好世界!")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_source_snapshot_deterministic():
    c1 = build_chapter_source(chapter_id=1, chapter_number=1, content="甲乙丙")
    c2 = build_chapter_source(chapter_id=2, chapter_number=2, content="丁戊")
    s1 = build_source_snapshot(novel_id=9, chapters=[c1, c2])
    s2 = build_source_snapshot(novel_id=9, chapters=[c2, c1])  # order independent
    assert s1.snapshot_hash == s2.snapshot_hash
    s3 = build_source_snapshot(novel_id=9, chapters=[c1])
    assert s3.snapshot_hash != s1.snapshot_hash


def test_node_id_changes_with_offsets_and_text():
    base = dict(
        source_snapshot_hash="a" * 64,
        chapter_id=1,
        chunk_index=0,
        content_hash_value=content_hash("hello"),
        source_start=0,
        source_end=5,
    )
    id1 = make_node_id(**base)
    id2 = make_node_id(**base)
    assert id1 == id2
    id3 = make_node_id(**{**base, "source_end": 6})
    assert id3 != id1
    id4 = make_node_id(**{**base, "content_hash_value": content_hash("hallo")})
    assert id4 != id1


def test_manifest_checksum_stable_and_config_sensitive():
    node = RawChunkNode(
        node_id="rn_test01",
        novel_id=1,
        chapter_id=1,
        chapter_number=1,
        chunk_index=0,
        chunk_type="paragraph",
        content="abc",
        content_hash=content_hash("abc"),
        word_count=3,
        source_start=0,
        source_end=3,
        normalized_start=0,
        normalized_end=3,
        source_snapshot_hash="b" * 64,
        chapter_content_hash="c" * 64,
        offset_map_hash="d" * 64,
        legacy_chunk_index=0,
    )
    # Fix node_id to deterministic
    node = node.model_copy(
        update={
            "node_id": make_node_id(
                source_snapshot_hash="b" * 64,
                chapter_id=1,
                chunk_index=0,
                content_hash_value=content_hash("abc"),
                source_start=0,
                source_end=3,
            )
        }
    )
    m1 = build_manifest(
        novel_id=1,
        source_snapshot_hash="b" * 64,
        nodes=[node],
        offset_map_hashes={"1": "d" * 64},
        config=ChunkerConfig(min_chunk_size=300, max_chunk_size=500),
    )
    m2 = build_manifest(
        novel_id=1,
        source_snapshot_hash="b" * 64,
        nodes=[node],
        offset_map_hashes={"1": "d" * 64},
        config=ChunkerConfig(min_chunk_size=300, max_chunk_size=500),
    )
    assert m1.manifest_checksum == m2.manifest_checksum
    m3 = build_manifest(
        novel_id=1,
        source_snapshot_hash="b" * 64,
        nodes=[node],
        offset_map_hashes={"1": "d" * 64},
        config=ChunkerConfig(min_chunk_size=200, max_chunk_size=500),
    )
    assert m3.manifest_checksum != m1.manifest_checksum
    assert m3.chunker_config_hash != m1.chunker_config_hash


def test_rejects_extra_fields_and_bad_offsets():
    with pytest.raises(ValidationError):
        RawChunkNode(
            node_id="rn_x",
            novel_id=1,
            chapter_id=1,
            chapter_number=1,
            chunk_index=0,
            chunk_type="paragraph",
            content="x",
            content_hash=content_hash("x"),
            word_count=1,
            source_start=5,
            source_end=1,
            normalized_start=0,
            normalized_end=1,
            source_snapshot_hash="a" * 64,
            chapter_content_hash="b" * 64,
            offset_map_hash="c" * 64,
            legacy_chunk_index=0,
            extra_field=True,  # type: ignore[call-arg]
        )


def test_config_hash_canonical():
    a = config_hash(ChunkerConfig(min_chunk_size=300, max_chunk_size=500))
    b = config_hash(
        {"max_chunk_size": 500, "min_chunk_size": 300, "short_paragraph_merge": 50}
    )
    assert a == b
