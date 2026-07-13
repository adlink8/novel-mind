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


# ── Phase 07-02: atomic spans, boundary proposals, candidate segments ──

ReasonCode = Literal[
    "CHAPTER_EDGE",
    "STRUCTURAL_BREAK",
    "TIME_SHIFT",
    "LOCATION_SHIFT",
    "SPEAKER_SHIFT",
    "POV_SHIFT",
    "OPEN_QUOTE",
    "COREFERENCE_RISK",
    "TARGET_SIZE",
    "HARD_MAX_SIZE",
    "UNDER_MIN_SIZE",
]

RuleDecision = Literal["split", "merge", "abstain"]

RULE_CONFIDENCE_VERSION = "rule-confidence.v1"
AUTO_ACCEPT_THRESHOLD = 0.75
ADJUDICATE_THRESHOLD = 0.40


class AtomicSpan(StrictModel):
    """Offset-preserving atomic text unit (sentence/paragraph fragment)."""

    span_id: str = Field(..., min_length=8)
    chapter_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    index: int = Field(..., ge=0)
    content: str
    content_hash: str = Field(..., min_length=64, max_length=64)
    source_start: int = Field(..., ge=0)
    source_end: int = Field(..., ge=0)
    normalized_start: int = Field(..., ge=0)
    normalized_end: int = Field(..., ge=0)
    char_count: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _ok(self) -> AtomicSpan:
        if self.source_end < self.source_start:
            raise ValueError("source_end < source_start")
        if self.normalized_end < self.normalized_start:
            raise ValueError("normalized_end < normalized_start")
        if self.char_count != len(self.content):
            raise ValueError("char_count must equal len(content)")
        return self


class BoundaryProposal(StrictModel):
    """Rule proposal for the boundary between two adjacent atomic spans.

    ``confidence`` is a versioned heuristic score in [0, 1], not a probability.
    """

    proposal_id: str = Field(..., min_length=8)
    chapter_id: int = Field(..., ge=1)
    left_span_id: str
    right_span_id: str
    left_content_hash: str = Field(..., min_length=64, max_length=64)
    right_content_hash: str = Field(..., min_length=64, max_length=64)
    rule_decision: RuleDecision
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_version: str = RULE_CONFIDENCE_VERSION
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    hard_constraint: bool = False
    llm_eligible: bool = False
    fallback_decision: RuleDecision
    input_hash: str = Field(..., min_length=64, max_length=64)
    rule_config_hash: str = Field(..., min_length=64, max_length=64)
    source_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)

    @model_validator(mode="after")
    def _hard_not_eligible(self) -> BoundaryProposal:
        if self.hard_constraint and self.llm_eligible:
            raise ValueError("hard_constraint boundaries must not be llm_eligible")
        if not self.reason_codes:
            raise ValueError("reason_codes must be non-empty")
        return self


class CandidateSegment(StrictModel):
    """One candidate chunk assembled from contiguous atomic spans."""

    segment_id: str = Field(..., min_length=8)
    chapter_id: int = Field(..., ge=1)
    chapter_number: int = Field(..., ge=1)
    index: int = Field(..., ge=0)
    span_ids: list[str] = Field(..., min_length=1)
    content: str
    content_hash: str = Field(..., min_length=64, max_length=64)
    source_start: int = Field(..., ge=0)
    source_end: int = Field(..., ge=0)
    char_count: int = Field(..., ge=0)
    decision_sources: list[str] = Field(default_factory=list)


class CandidateSegmentation(StrictModel):
    """Deterministic chapter segmentation from rule proposals (no model/DB)."""

    schema_version: Literal["candidate-segmentation.v1"] = "candidate-segmentation.v1"
    chapter_id: int
    chapter_number: int
    source_snapshot_hash: str | None = None
    spans: list[AtomicSpan]
    proposals: list[BoundaryProposal]
    segments: list[CandidateSegment]
    pending_adjudication: list[str] = Field(
        default_factory=list,
        description="proposal_ids awaiting LLM (non-hard, below auto-accept)",
    )
    rule_config_hash: str = Field(..., min_length=64, max_length=64)
    segmentation_checksum: str = Field(..., min_length=64, max_length=64)
