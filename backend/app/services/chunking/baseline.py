"""Offset-preserving deterministic baseline adapter over rule ChunkingService."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.chunking.manifests import (
    build_chapter_source,
    build_manifest,
    build_source_snapshot,
    content_hash,
    make_node_id,
)
from app.services.chunking.schemas import (
    CHUNKER_NAME_BASELINE,
    CHUNKER_VERSION_BASELINE,
    ChunkManifest,
    ChunkerConfig,
    RawChunkNode,
    SourceSnapshot,
)
from app.services.chunking_service import Chapter, ChunkingService
from app.services.rag_fixture import stable_hash


@dataclass(frozen=True)
class OffsetMap:
    """Maps normalized code-point index → source code-point index."""

    normalized: str
    # For each index in normalized (and one past end), corresponding source index
    norm_to_source: list[int]
    map_hash: str

    def source_span(self, norm_start: int, norm_end: int) -> tuple[int, int]:
        if norm_start < 0 or norm_end < norm_start or norm_end > len(self.normalized):
            raise ValueError("normalized span out of range")
        src_start = self.norm_to_source[norm_start]
        src_end = self.norm_to_source[norm_end]
        return src_start, src_end


def build_offset_map(source: str) -> OffsetMap:
    """Single-scan CRLF/CR → LF normalization with explicit source indices.

    Does not strip spaces (chunker strips per-line later); only newline family.
    """
    normalized_chars: list[str] = []
    norm_to_source: list[int] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\r":
            # \r\n or lone \r → single \n in normalized
            normalized_chars.append("\n")
            norm_to_source.append(i)
            if i + 1 < n and source[i + 1] == "\n":
                i += 2
            else:
                i += 1
            continue
        normalized_chars.append(ch)
        norm_to_source.append(i)
        i += 1
    # sentinel for end
    norm_to_source.append(n)
    normalized = "".join(normalized_chars)
    map_hash = stable_hash(
        {
            "unit": "unicode_codepoint",
            "source_len": n,
            "norm_len": len(normalized),
            "norm_to_source": norm_to_source,
        }
    )
    return OffsetMap(
        normalized=normalized, norm_to_source=norm_to_source, map_hash=map_hash
    )


def _flex_match_chunk(haystack: str, needle: str, start: int) -> tuple[int, int] | None:
    """Match needle in haystack from start with flexible whitespace runs.

    Chunker joins paragraphs with single ``\\n`` while source may keep blank
    lines (``\\n\\n``) and line-edge spaces stripped during paragraphization.
    Cursor only advances forward (duplicate-safe; no global str.find).
    """
    n_h = len(haystack)
    n_n = len(needle)
    if n_n == 0:
        return start, start

    def is_ws(ch: str) -> bool:
        return ch in " \t\n\r"

    # Try increasing start positions (code-point steps only)
    pos = start
    while pos < n_h:
        i = pos
        j = 0
        match_start: int | None = None
        while j < n_n and i < n_h:
            if haystack[i] == needle[j]:
                if match_start is None:
                    match_start = i
                i += 1
                j += 1
                continue
            # Align whitespace runs (source may have extra blank lines/spaces)
            if is_ws(haystack[i]) or is_ws(needle[j]):
                if is_ws(haystack[i]) and is_ws(needle[j]):
                    while i < n_h and is_ws(haystack[i]):
                        i += 1
                    while j < n_n and is_ws(needle[j]):
                        j += 1
                    if match_start is None:
                        match_start = pos
                    continue
                if is_ws(haystack[i]) and not is_ws(needle[j]):
                    # Extra source whitespace between chunk tokens
                    if match_start is None:
                        i += 1
                        continue
                    while i < n_h and is_ws(haystack[i]):
                        i += 1
                    continue
            break
        if j == n_n and match_start is not None:
            return match_start, i
        pos += 1
    return None


def _locate_chunk_spans(
    normalized: str, chunks: list[str]
) -> list[tuple[int, int]]:
    """Walk normalized text once; bind each chunk with forward-only flexible match."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for chunk in chunks:
        if not chunk:
            spans.append((cursor, cursor))
            continue
        found = _flex_match_chunk(normalized, chunk, cursor)
        if found is None:
            raise ValueError(
                f"cannot map chunk to normalized source at cursor={cursor}: "
                f"{chunk[:40]!r}..."
            )
        start, end = found
        spans.append((start, end))
        cursor = end
    return spans


class BaselineChunker:
    """Wraps rule ChunkingService with stable source lineage (no active mutation)."""

    def __init__(
        self,
        service: ChunkingService | None = None,
        *,
        min_chunk_size: int = 300,
        max_chunk_size: int = 500,
    ):
        self.service = service or ChunkingService(
            min_chunk_size=min_chunk_size, max_chunk_size=max_chunk_size
        )
        self.config = ChunkerConfig(
            min_chunk_size=self.service.min_chunk_size,
            max_chunk_size=self.service.max_chunk_size,
        )

    async def build_baseline_manifest(
        self,
        *,
        novel_id: int,
        chapters: list[Chapter],
        owner_id: int | None = None,
    ) -> tuple[ChunkManifest, SourceSnapshot]:
        chapter_sources = [
            build_chapter_source(
                chapter_id=ch.id,
                chapter_number=ch.chapter_number,
                content=ch.content or "",
            )
            for ch in chapters
        ]
        snapshot = build_source_snapshot(
            novel_id=novel_id, chapters=chapter_sources, owner_id=owner_id
        )
        nodes: list[RawChunkNode] = []
        offset_map_hashes: dict[str, str] = {}

        for ch, ch_src in zip(chapters, chapter_sources):
            source = ch.content or ""
            omap = build_offset_map(source)
            offset_map_hashes[str(ch.id)] = omap.map_hash

            # Chunk on normalized text so rule behavior matches CRLF-safe input
            raw_chunks = await self.service.chunk_chapter(
                chapter_id=ch.id,
                chapter_number=ch.chapter_number,
                content=omap.normalized,
            )
            texts = [c["content"] for c in raw_chunks]
            spans = _locate_chunk_spans(omap.normalized, texts) if texts else []

            for c, (n_start, n_end) in zip(raw_chunks, spans):
                s_start, s_end = omap.source_span(n_start, n_end)
                # Flexible match may cover extra blank lines; verify non-ws payload
                covered = omap.normalized[n_start:n_end]
                if "".join(covered.split()) != "".join(c["content"].split()):
                    raise ValueError("normalized span does not cover chunk content")
                c_hash = content_hash(c["content"])
                node_id = make_node_id(
                    source_snapshot_hash=snapshot.snapshot_hash,
                    chapter_id=ch.id,
                    chunk_index=c["chunk_index"],
                    content_hash_value=c_hash,
                    source_start=s_start,
                    source_end=s_end,
                )
                nodes.append(
                    RawChunkNode(
                        node_id=node_id,
                        novel_id=novel_id,
                        chapter_id=ch.id,
                        chapter_number=ch.chapter_number,
                        chunk_index=c["chunk_index"],
                        chunk_type=c["chunk_type"],
                        content=c["content"],
                        content_hash=c_hash,
                        word_count=c["word_count"],
                        source_start=s_start,
                        source_end=s_end,
                        normalized_start=n_start,
                        normalized_end=n_end,
                        source_snapshot_hash=snapshot.snapshot_hash,
                        chapter_content_hash=ch_src.content_hash,
                        offset_map_hash=omap.map_hash,
                        legacy_chunk_index=c["chunk_index"],
                    )
                )

        manifest = build_manifest(
            novel_id=novel_id,
            source_snapshot_hash=snapshot.snapshot_hash,
            nodes=nodes,
            offset_map_hashes=offset_map_hashes,
            config=self.config,
            chunker_name=CHUNKER_NAME_BASELINE,
            chunker_version=CHUNKER_VERSION_BASELINE,
        )
        return manifest, snapshot


def attach_lineage_to_legacy_chunks(
    chunks: list[dict[str, Any]],
    *,
    novel_id: int,
    source_snapshot_hash: str,
    chapter_content_hash: str,
    offset_map_hash: str,
    spans: list[tuple[int, int, int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Augment legacy chunk dicts with lineage metadata (non-breaking)."""
    out: list[dict[str, Any]] = []
    for i, ch in enumerate(chunks):
        meta = dict(ch.get("metadata_json") or {})
        if spans and i < len(spans):
            ss, se, ns, ne = spans[i]
            meta.update(
                {
                    "source_start": ss,
                    "source_end": se,
                    "normalized_start": ns,
                    "normalized_end": ne,
                }
            )
        meta.update(
            {
                "novel_id": novel_id,
                "source_snapshot_hash": source_snapshot_hash,
                "chapter_content_hash": chapter_content_hash,
                "offset_map_hash": offset_map_hash,
                "offset_unit": "unicode_codepoint",
                "chunker_name": CHUNKER_NAME_BASELINE,
                "chunker_version": CHUNKER_VERSION_BASELINE,
            }
        )
        item = dict(ch)
        item["metadata_json"] = meta
        out.append(item)
    return out
