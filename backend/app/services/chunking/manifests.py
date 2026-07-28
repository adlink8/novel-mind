"""Canonical hashes, stable node IDs, and sorted manifest checksums."""

from __future__ import annotations

import json
from typing import Any

from app.services.chunking.schemas import (
    CHUNKER_NAME_BASELINE,
    CHUNKER_VERSION_BASELINE,
    ChapterSource,
    ChunkManifest,
    ChunkerConfig,
    RawChunkNode,
    SourceSnapshot,
)
from app.services.rag_fixture import stable_hash


def content_hash(text: str) -> str:
    """SHA-256 hex of unicode text (code-point stable via UTF-8 encode of str)."""
    return stable_hash({"text": text})


def config_hash(config: ChunkerConfig | dict[str, Any]) -> str:
    if isinstance(config, ChunkerConfig):
        payload = config.model_dump(mode="json")
    else:
        payload = dict(config)
    return stable_hash(payload)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_chapter_source(
    *, chapter_id: int, chapter_number: int, content: str
) -> ChapterSource:
    return ChapterSource(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        content_hash=content_hash(content),
    )


def build_source_snapshot(
    *,
    novel_id: int,
    chapters: list[ChapterSource],
    owner_id: int | None = None,
) -> SourceSnapshot:
    ordered = sorted(chapters, key=lambda c: (c.chapter_number, c.chapter_id))
    snap_body = {
        "novel_id": novel_id,
        "owner_id": owner_id,
        "chapters": [
            {
                "chapter_id": c.chapter_id,
                "chapter_number": c.chapter_number,
                "content_hash": c.content_hash,
            }
            for c in ordered
        ],
    }
    return SourceSnapshot(
        novel_id=novel_id,
        owner_id=owner_id,
        chapters=ordered,
        snapshot_hash=stable_hash(snap_body),
    )


def make_node_id(
    *,
    source_snapshot_hash: str,
    chapter_id: int,
    chunk_index: int,
    content_hash_value: str,
    source_start: int,
    source_end: int,
) -> str:
    """Deterministic node id from real lineage + slice identity (not caller-forged)."""
    digest = stable_hash(
        {
            "source_snapshot_hash": source_snapshot_hash,
            "chapter_id": chapter_id,
            "chunk_index": chunk_index,
            "content_hash": content_hash_value,
            "source_start": source_start,
            "source_end": source_end,
        }
    )
    return f"rn_{digest[:24]}"


def build_manifest(
    *,
    novel_id: int,
    source_snapshot_hash: str,
    nodes: list[RawChunkNode],
    offset_map_hashes: dict[str, str],
    config: ChunkerConfig | None = None,
    chunker_name: str = CHUNKER_NAME_BASELINE,
    chunker_version: str = CHUNKER_VERSION_BASELINE,
) -> ChunkManifest:
    cfg = config or ChunkerConfig()
    cfg_h = config_hash(cfg)
    ordered_nodes = sorted(
        nodes, key=lambda n: (n.chapter_number, n.chunk_index, n.node_id)
    )
    edges = [
        {
            "from": ordered_nodes[i].node_id,
            "to": ordered_nodes[i + 1].node_id,
            "rel": "next_raw",
        }
        for i in range(len(ordered_nodes) - 1)
        if ordered_nodes[i].chapter_id == ordered_nodes[i + 1].chapter_id
    ]
    unsigned = {
        "schema_version": "chunk-manifest.v1",
        "novel_id": novel_id,
        "source_snapshot_hash": source_snapshot_hash,
        "chunker_name": chunker_name,
        "chunker_version": chunker_version,
        "chunker_config_hash": cfg_h,
        "offset_unit": "unicode_codepoint",
        "offset_map_hashes": dict(sorted(offset_map_hashes.items())),
        "nodes": [
            {
                "node_id": n.node_id,
                "chapter_id": n.chapter_id,
                "chunk_index": n.chunk_index,
                "content_hash": n.content_hash,
                "source_start": n.source_start,
                "source_end": n.source_end,
            }
            for n in ordered_nodes
        ],
        "edges": edges,
    }
    checksum = stable_hash(unsigned)
    return ChunkManifest(
        novel_id=novel_id,
        source_snapshot_hash=source_snapshot_hash,
        chunker_name=chunker_name,
        chunker_version=chunker_version,
        chunker_config=cfg,
        chunker_config_hash=cfg_h,
        offset_map_hashes=dict(sorted(offset_map_hashes.items())),
        nodes=ordered_nodes,
        edges=edges,
        manifest_checksum=checksum,
    )
