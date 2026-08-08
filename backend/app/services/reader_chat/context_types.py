"""Data-contract layer for reader-chat context manifests.

Frozen dataclasses (validated selection / progress snapshot / chapter range /
evidence entries / manifest), the stable ``SelectionValidationError`` rejection
type, and the canonical hash / code-point slicing utilities. This is the leaf
module of the ``reader_chat.context`` split — it imports no DB or service
modules, so every other split module may depend on it without cycles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

MAX_SELECTION_CODE_POINTS = 8000
SELECTION_EVIDENCE_KEY = "selection:primary"
CHAPTER_EVIDENCE_KEY = "chapter:primary"

# Multi-chapter range context: total excerpt budget is a bounded multiple (2x)
# of the single-chapter budget, split evenly across chapters in the range.
MAX_RANGE_CONTEXT_CODE_POINTS = 2 * MAX_SELECTION_CODE_POINTS
CHAPTER_RANGE_ANCHOR_KIND = "chapter_range"


class SelectionValidationError(ValueError):
    """Stable selection rejection with machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedSelection:
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    selection_text: str
    selection_text_hash: str
    chapter_content_hash: str
    hierarchy_build_id: str
    hierarchy_checksum: str


@dataclass(frozen=True)
class ProgressSnapshot:
    chapter_id: int | None
    cutoff_chapter_number: int
    timeline_full_book: bool
    full_book: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "cutoff_chapter_number": self.cutoff_chapter_number,
            "timeline_full_book": self.timeline_full_book,
            "full_book": self.full_book,
        }


@dataclass(frozen=True)
class ValidatedChapterSegment:
    """One visible chapter's budgeted excerpt inside a validated range."""

    chapter_id: int
    chapter_number: int
    excerpt: str
    excerpt_hash: str
    chapter_content_hash: str


@dataclass(frozen=True)
class ValidatedChapterRange:
    """Cutoff-narrowed chapter interval with per-chapter budgeted excerpts."""

    chapter_start: int
    chapter_end: int  # effective end after cutoff narrowing
    requested_chapter_end: int
    segments: tuple[ValidatedChapterSegment, ...]
    hierarchy_build_id: str
    hierarchy_checksum: str
    progress: ProgressSnapshot

    def anchor_dict(self) -> dict[str, Any]:
        return {
            "kind": CHAPTER_RANGE_ANCHOR_KIND,
            "chapter_start": self.chapter_start,
            "chapter_end": self.chapter_end,
        }


@dataclass(frozen=True)
class ContextEvidenceEntry:
    evidence_key: str
    source_type: str
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    sort_order: int
    version_lineage: dict[str, Any] = field(default_factory=dict)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_key": self.evidence_key,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "chapter_id": self.chapter_id,
            "chapter_number": self.chapter_number,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "content_hash": self.content_hash,
            "excerpt": self.excerpt,
            "sort_order": self.sort_order,
            "version_lineage": self.version_lineage,
        }


@dataclass(frozen=True)
class ContextManifest:
    reading_progress_snapshot: dict[str, Any]
    full_book: bool
    cutoff_chapter_number: int
    analysis_version_id: int | None
    hierarchy_build_id: str
    hierarchy_checksum: str
    evidence: tuple[ContextEvidenceEntry, ...]
    omitted_evidence_counts: dict[str, int]
    prompt_inputs: dict[str, Any]
    source_status: dict[str, str]
    manifest_checksum: str

    def allowed_evidence_ids(self) -> set[str]:
        return {entry.evidence_key for entry in self.evidence}

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "reading_progress_snapshot": self.reading_progress_snapshot,
            "full_book": self.full_book,
            "cutoff_chapter_number": self.cutoff_chapter_number,
            "analysis_version_id": self.analysis_version_id,
            "hierarchy_build_id": self.hierarchy_build_id,
            "hierarchy_checksum": self.hierarchy_checksum,
            "evidence": [e.canonical_dict() for e in self.evidence],
            "omitted_evidence_counts": self.omitted_evidence_counts,
            "prompt_inputs": self.prompt_inputs,
            "source_status": self.source_status,
        }


def code_point_len(text: str) -> int:
    """Python 3 str indices are Unicode code points."""

    return len(text)


def code_point_slice(text: str, start: int, end: int) -> str:
    """Half-open code-point slice matching persisted Chapter.content coordinates."""

    if start < 0 or end < start:
        raise SelectionValidationError(
            "invalid_bounds", "source offsets must form a non-empty half-open range"
        )
    length = code_point_len(text)
    if end > length:
        raise SelectionValidationError(
            "invalid_bounds", "source_end exceeds chapter content length"
        )
    return text[start:end]


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_manifest_checksum(payload: dict[str, Any]) -> str:
    """Checksum of the frozen canonical graph excluding the checksum field itself."""

    body = {k: v for k, v in payload.items() if k != "manifest_checksum"}
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()
