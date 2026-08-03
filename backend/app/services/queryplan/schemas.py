"""Strict typed contracts for the Question-Driven Retrieval QueryPlan boundary.

Phase 26-01 / REQ-QP-01. The parser is deterministic and fail-closed (D-02): it
never guesses intent, scope, cutoff, dimensions or constraints. Whole-book scope
requires the explicit per-novel switch (D-12). Unknown / ambiguous intent, scope
escape, future probing and contradictory constraints are rejected with stable
``BlockedResult`` reasons and never create a trace or touch the database.

Phase 25.2's ``answer-reading-question`` Skill is an agent-orchestration upstream
boundary (D-11): it is NOT assumed implemented here. This module only defines the
typed contract a downstream consumer must satisfy. No NM promotion, active-pointer
or consumer cutover path exists in this boundary (D-14).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

Hash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]
QuestionText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
LineageText = Annotated[str, StringConstraints(min_length=1, max_length=128)]

QUERYPLAN_SCHEMA_VERSION = "queryplan.v1"
QUERYPLAN_PARSER_VERSION = "queryplan-parser.v1"
QUERYPLAN_DATASET_VERSION = "queryplan-questions-v1"
QUERYPLAN_SOURCES = ("reader_chat", "analysis_chat", "fixture")
QUERYPLAN_HASH_PLAN = f"{QUERYPLAN_SCHEMA_VERSION}:plan"
QUERYPLAN_HASH_IDEM = f"{QUERYPLAN_SCHEMA_VERSION}:idem"
QUERYPLAN_HASH_QUESTION = f"{QUERYPLAN_SCHEMA_VERSION}:question"
QUERYPLAN_HASH_AVAILABILITY = f"{QUERYPLAN_SCHEMA_VERSION}:availability"


class StrictQueryPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryPlanIntent(StrEnum):
    READER = "reader"
    ANALYSIS = "analysis"


class AnchorKind(StrEnum):
    SELECTION = "selection"
    CHAPTER_RANGE = "chapter_range"


class QueryDimension(StrEnum):
    """D-04 dimension vocabulary."""

    RAW_TEXT = "raw_text"
    EVENTS_CAUSALITY = "events_causality"
    CHARACTER_STATE = "character_state"
    RELATIONS = "relations"
    TIMELINE = "timeline"
    CLUES_FORESHAOWING = "clues_foreshadowing"
    WORLD_RULES = "world_rules"
    NARRATIVE_UNITS = "narrative_units"
    WORLD_PROJECTION = "world_projection"


class CutoffMode(StrEnum):
    READING_PROGRESS = "reading_progress"
    WHOLE_BOOK = "whole_book"


class FallbackStage(StrEnum):
    EXACT_READER = "exact_reader"
    DETERMINISTIC_HEURISTIC = "deterministic_heuristic"
    STABLE_UNAVAILABLE = "stable_unavailable"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class BlockedReasonCode(StrEnum):
    UNKNOWN_INTENT = "unknown_intent"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    SCOPE_ESCAPE = "scope_escape"
    FUTURE_PROBING = "future_probing"
    CONTRADICTORY = "contradictory_constraints"
    WHOLE_BOOK_UNAUTHORIZED = "whole_book_unauthorized"
    INVALID_INPUT = "invalid_input"


# ---------------------------------------------------------------------------
# Scope / anchor contracts
# ---------------------------------------------------------------------------


class ReadingProgress(StrictQueryPlanModel):
    """Persisted reading-progress cutoff (D-12); never a transient client guess."""

    through_chapter: PositiveInt
    snapshot_hash: Hash64
    full_book_authorized: bool = False


class SelectionAnchor(StrictQueryPlanModel):
    """Reader anchor (D-10); raw selection text is never stored in a trace."""

    kind: Literal["selection"] = "selection"
    chapter_id: PositiveInt
    source_start: NonNegInt
    source_end: PositiveInt
    chapter_content_hash: Hash64

    @model_validator(mode="after")
    def _half_open_range(self) -> SelectionAnchor:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ChapterRangeAnchor(StrictQueryPlanModel):
    """Analysis structure anchor (D-10); inclusive chapter-number semantics."""

    kind: Literal["chapter_range"] = "chapter_range"
    chapter_start: PositiveInt
    chapter_end: PositiveInt

    @model_validator(mode="after")
    def _inclusive_order(self) -> ChapterRangeAnchor:
        if self.chapter_end < self.chapter_start:
            raise ValueError("chapter_end must be >= chapter_start")
        return self


Anchor = Annotated[SelectionAnchor | ChapterRangeAnchor, Field(discriminator="kind")]


# ---------------------------------------------------------------------------
# Constraints / fallback / availability / evidence contracts
# ---------------------------------------------------------------------------


class AnswerConstraints(StrictQueryPlanModel):
    """D-08/D-09: no uncited factual assertion; abstain when evidence is absent."""

    must_cite_every_fact: bool = True
    abstain_without_evidence: bool = True
    allow_summary_citation: bool = False
    max_evidence_refs: PositiveInt = 8

    @model_validator(mode="after")
    def _no_uncited_fact_hole(self) -> AnswerConstraints:
        if not self.must_cite_every_fact and not self.abstain_without_evidence:
            raise ValueError(
                "relaxing both must_cite_every_fact and abstain_without_evidence "
                "creates an uncited-fact hole (D-09)"
            )
        return self


class FallbackPolicy(StrictQueryPlanModel):
    """D-15 single fallback chain; heuristic output is candidate-recall only."""

    chain: tuple[FallbackStage, ...] = (
        FallbackStage.EXACT_READER,
        FallbackStage.DETERMINISTIC_HEURISTIC,
        FallbackStage.STABLE_UNAVAILABLE,
    )
    heuristic_candidate_recall_only: bool = True

    @field_validator("chain")
    @classmethod
    def _fixed_chain(cls, value: tuple[FallbackStage, ...]) -> tuple[FallbackStage, ...]:
        expected = (
            FallbackStage.EXACT_READER,
            FallbackStage.DETERMINISTIC_HEURISTIC,
            FallbackStage.STABLE_UNAVAILABLE,
        )
        if tuple(value) != expected:
            raise ValueError(
                "fallback chain must be exact_reader -> deterministic_heuristic "
                "-> stable_unavailable (D-15)"
            )
        return value


class DimensionAvailability(StrictQueryPlanModel):
    """D-05: missing/partial is never empty-success; provenance recorded."""

    dimension: QueryDimension
    status: AvailabilityStatus
    reason: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    provenance: Annotated[str, StringConstraints(min_length=1, max_length=160)]


class SpoilerCutoff(StrictQueryPlanModel):
    """Effective scope cutoff after the D-12 whole-book gate."""

    mode: CutoffMode = CutoffMode.READING_PROGRESS
    through_chapter: PositiveInt
    full_book_authorized: bool = False

    @model_validator(mode="after")
    def _mode_consistent(self) -> SpoilerCutoff:
        if self.mode == CutoffMode.READING_PROGRESS and self.full_book_authorized:
            raise ValueError(
                "reading-progress cutoff cannot also declare full-book authorization"
            )
        if self.mode == CutoffMode.WHOLE_BOOK and not self.full_book_authorized:
            raise ValueError("whole-book cutoff requires full_book_authorized")
        return self


class EvidenceRef(StrictQueryPlanModel):
    """Leaf/raw evidence contract (D-07/D-08); never a summary, score or chat text."""

    chapter_id: PositiveInt
    chapter_number: PositiveInt
    source_start: NonNegInt
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64

    @model_validator(mode="after")
    def _half_open_range(self) -> EvidenceRef:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


# ---------------------------------------------------------------------------
# Trace and plan contracts
# ---------------------------------------------------------------------------


class QueryPlanTrace(StrictQueryPlanModel):
    """Serializable audit-safe trace of one validated QueryPlan (D-01).

    Raw question text is never stored; only ``idempotency_key`` and
    ``canonical_payload_hash`` derivations plus lineage are kept.
    """

    trace_id: Annotated[str, StringConstraints(min_length=32, max_length=64)]
    idempotency_key: Hash64
    schema_version: str = QUERYPLAN_SCHEMA_VERSION
    parser_version: str = QUERYPLAN_PARSER_VERSION
    created_at: datetime
    source: Literal["reader_chat", "analysis_chat", "fixture"]
    dataset_lineage: LineageText
    canonical_payload_hash: Hash64
    availability_checksum: Hash64


class QueryPlan(StrictQueryPlanModel):
    """Typed retrieval plan (D-01): intent, scope, cutoff, dimensions, fallback,
    answer constraints, anchor, and a serializable trace."""

    schema_version: str = QUERYPLAN_SCHEMA_VERSION
    parser_version: str = QUERYPLAN_PARSER_VERSION
    intent: QueryPlanIntent
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    spoiler_cutoff: SpoilerCutoff
    dimensions: tuple[QueryDimension, ...]
    availability: tuple[DimensionAvailability, ...]
    fallback: FallbackPolicy
    answer_constraints: AnswerConstraints
    anchor: Anchor | None = None
    question_hash: Hash64
    trace: QueryPlanTrace

    @field_validator("dimensions")
    @classmethod
    def _non_empty_dedup(cls, value: tuple[QueryDimension, ...]) -> tuple[QueryDimension, ...]:
        if not value:
            raise ValueError("dimensions must be non-empty")
        return tuple(dict.fromkeys(value))

    @field_validator("availability")
    @classmethod
    def _availability_unique_per_dimension(
        cls, value: tuple[DimensionAvailability, ...]
    ) -> tuple[DimensionAvailability, ...]:
        if len({entry.dimension for entry in value}) != len(value):
            raise ValueError("duplicate availability entry for a dimension")
        return value


class BlockedResult(StrictQueryPlanModel):
    """Stable fail-closed clarification result; never persisted as a trace."""

    reason_code: BlockedReasonCode
    message: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    clarification: Annotated[str, StringConstraints(min_length=1, max_length=400)]


class QueryPlanRequest(StrictQueryPlanModel):
    """Parser boundary input. Parsing is pure and deterministic; no DB access."""

    intent: QueryPlanIntent
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    question_text: QuestionText
    reading_progress: ReadingProgress
    whole_book: bool = False
    selection: SelectionAnchor | None = None
    chapter_range: ChapterRangeAnchor | None = None
    dimensions: tuple[QueryDimension, ...] | None = None
    answer_constraints: AnswerConstraints | None = None
    source: Literal["reader_chat", "analysis_chat", "fixture"]
    dataset_lineage: LineageText = QUERYPLAN_DATASET_VERSION


# ---------------------------------------------------------------------------
# Deterministic hashing helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_query_text(raw: str) -> str:
    """NFKC normalize and collapse whitespace; preserves Chinese Unicode semantics."""

    if not isinstance(raw, str):
        raise TypeError("question_text must be str")
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _WS_RE.sub(" ", text)
    if not text:
        raise ValueError("question_text must be non-empty after normalization")
    return text


def canonical_json(value: Any) -> str:
    """Deterministic compact JSON used for all queryplan hashes."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def question_hash(normalized: str) -> str:
    return sha256_hex(QUERYPLAN_HASH_QUESTION, normalized)


def plan_payload_hash(payload: dict[str, Any]) -> str:
    """Hash of a canonical plan payload dict (trace excluded)."""

    return sha256_hex(QUERYPLAN_HASH_PLAN, canonical_json(payload))


def availability_checksum(availability: tuple[DimensionAvailability, ...]) -> str:
    body = canonical_json(
        [
            {
                "dimension": entry.dimension.value,
                "status": entry.status.value,
                "reason": entry.reason,
                "provenance": entry.provenance,
            }
            for entry in availability
        ]
    )
    return sha256_hex(QUERYPLAN_HASH_AVAILABILITY, body)


def idempotency_key(request: QueryPlanRequest) -> str:
    """Deterministic replay key over all plan-determining request inputs.

    ``source`` and ``dataset_lineage`` are lineage metadata, not plan semantics,
    so they are excluded. The question is normalized before hashing.
    """

    payload = request.model_dump(mode="json", exclude={"source", "dataset_lineage"})
    payload["question_text"] = normalize_query_text(payload["question_text"])
    return sha256_hex(QUERYPLAN_HASH_IDEM, canonical_json(payload))
