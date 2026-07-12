"""Strict contracts for Phase 07 baseline source lineage and manifests (REQ-CHUNK-01)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


OFFSET_UNIT = "unicode_codepoint"
CHUNKER_NAME_BASELINE = "rule-baseline"
CHUNKER_VERSION_BASELINE = "1.0.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChapterSource(StrictModel):
    """One chapter frozen into a source snapshot."""

    chapter_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    content: str
    content_hash: str = Field(..., min_length=64, max_length=64)


class SourceSnapshot(StrictModel):
    """Immutable source evidence for a novel at a point in time."""

    novel_id: int = Field(..., ge=1)
    owner_id: int | None = None
    chapters: list[ChapterSource] = Field(..., min_length=1)
    snapshot_hash: str = Field(..., min_length=64, max_length=64)
    offset_unit: Literal["unicode_codepoint"] = OFFSET_UNIT


class OffsetSpan(StrictModel):
    """Half-open [start, end) span in unicode code points."""

    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)
    unit: Literal["unicode_codepoint"] = OFFSET_UNIT

    @model_validator(mode="after")
    def _ordered(self) -> OffsetSpan:
        if self.end < self.start:
            raise ValueError("end must be >= start")
        return self


class RawChunkNode(StrictModel):
    """Baseline raw chunk with source + normalized offsets and lineage."""

    node_id: str = Field(..., min_length=8)
    novel_id: int = Field(..., ge=1)
    chapter_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    chunk_index: int = Field(..., ge=0)
    chunk_type: str
    content: str
    content_hash: str = Field(..., min_length=64, max_length=64)
    word_count: int = Field(..., ge=0)
    source_start: int = Field(..., ge=0)
    source_end: int = Field(..., ge=0)
    normalized_start: int = Field(..., ge=0)
    normalized_end: int = Field(..., ge=0)
    source_snapshot_hash: str = Field(..., min_length=64, max_length=64)
    chapter_content_hash: str = Field(..., min_length=64, max_length=64)
    offset_map_hash: str = Field(..., min_length=64, max_length=64)
    # Legacy identity for D-02 continuity
    legacy_chunk_index: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _spans(self) -> RawChunkNode:
        if self.source_end < self.source_start:
            raise ValueError("source_end < source_start")
        if self.normalized_end < self.normalized_start:
            raise ValueError("normalized_end < normalized_start")
        return self


class ChunkerConfig(StrictModel):
    min_chunk_size: int = Field(300, ge=1)
    max_chunk_size: int = Field(500, ge=1)
    short_paragraph_merge: int = Field(50, ge=0)

    @model_validator(mode="after")
    def _sizes(self) -> ChunkerConfig:
        if self.max_chunk_size < self.min_chunk_size:
            raise ValueError("max_chunk_size must be >= min_chunk_size")
        return self


class ChunkManifest(StrictModel):
    """Versioned baseline manifest: sorted nodes + config/source lineage."""

    schema_version: Literal["chunk-manifest.v1"] = "chunk-manifest.v1"
    novel_id: int = Field(..., ge=1)
    source_snapshot_hash: str = Field(..., min_length=64, max_length=64)
    chunker_name: str = Field(..., min_length=1)
    chunker_version: str = Field(..., min_length=1)
    chunker_config: ChunkerConfig
    chunker_config_hash: str = Field(..., min_length=64, max_length=64)
    offset_unit: Literal["unicode_codepoint"] = OFFSET_UNIT
    offset_map_hashes: dict[str, str] = Field(default_factory=dict)
    nodes: list[RawChunkNode] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    manifest_checksum: str = Field(..., min_length=64, max_length=64)

    @field_validator("nodes")
    @classmethod
    def _sorted_nodes(cls, nodes: list[RawChunkNode]) -> list[RawChunkNode]:
        return sorted(
            nodes,
            key=lambda n: (n.chapter_number, n.chunk_index, n.node_id),
        )
