"""
Phase 10 reader-chat strict API and answer contracts.

Suggestions are chat data only (D-06): requires_explicit_confirmation is always
true and no confirmation/apply/write model exists in this phase.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictReaderChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class GenerationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_DEPENDENCY = "paused_dependency"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    FAILED_VALIDATION = "failed_validation"


class EvidenceSourceType(StrEnum):
    SELECTION = "selection"
    HIERARCHY = "hierarchy"
    TIMELINE = "timeline"
    KNOWLEDGE = "knowledge"
    RELATIONSHIP_OBSERVATION = "relationship_observation"


class SuggestionCandidateType(StrEnum):
    TIMELINE = "timeline"
    RELATIONSHIP = "relationship"
    CLUE = "clue"


# ---------------------------------------------------------------------------
# Selection and message request contracts
# ---------------------------------------------------------------------------


class SelectionCoordinate(StrictReaderChatModel):
    """Client-supplied selection; server re-slices Chapter.content as authority."""

    chapter_id: int = Field(gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    selection_text: str = Field(min_length=1, max_length=8000)
    selection_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_half_open_range(self) -> SelectionCoordinate:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ChapterRange(StrictReaderChatModel):
    """Structure-anchored chapter interval (chapter-number semantics, inclusive).

    Matches timeline API chapter_start/chapter_end semantics. Server narrows
    chapter_end to the reading cutoff before any context assembly.
    """

    chapter_start: int = Field(ge=1)
    chapter_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_inclusive_order(self) -> ChapterRange:
        if self.chapter_end < self.chapter_start:
            raise ValueError("chapter_end must be >= chapter_start")
        return self


class ChapterRangeAnchor(StrictReaderChatModel):
    """Echo of the effective (cutoff-narrowed) structure anchor on a message."""

    kind: Literal["chapter_range"] = "chapter_range"
    chapter_start: int = Field(ge=1)
    chapter_end: int = Field(ge=1)


class ConversationCreate(StrictReaderChatModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)


class ConversationPatch(StrictReaderChatModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: ConversationStatus | None = None

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> ConversationPatch:
        if self.title is None and self.status is None:
            raise ValueError("at least one of title or status is required")
        return self


class ConversationListItem(StrictReaderChatModel):
    """Metadata-only list row — no message bodies or evidence excerpts."""

    id: int
    novel_id: int
    title: str
    status: ConversationStatus
    next_sequence: int
    last_opened_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_message_sequence: int | None = None
    last_message_role: MessageRole | None = None
    last_message_at: datetime | None = None


class ConversationDetail(ConversationListItem):
    pass


class MessageCreate(StrictReaderChatModel):
    client_message_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=8000)
    chapter_id: int | None = Field(default=None, gt=0)
    selection: SelectionCoordinate | None = None
    chapter_range: ChapterRange | None = None

    @model_validator(mode="after")
    def require_chapter_context(self) -> MessageCreate:
        if self.chapter_range is not None:
            # Structure anchor is exclusive with selection/single-chapter anchors.
            if self.selection is not None or self.chapter_id is not None:
                raise ValueError(
                    "chapter_range is mutually exclusive with selection and chapter_id"
                )
            return self
        if self.selection is None and self.chapter_id is None:
            raise ValueError(
                "chapter_id is required when selection and chapter_range are absent"
            )
        if (
            self.selection is not None
            and self.chapter_id is not None
            and self.selection.chapter_id != self.chapter_id
        ):
            raise ValueError("chapter_id must match selection.chapter_id")
        return self


class SelectionSummary(StrictReaderChatModel):
    chapter_id: int
    source_start: int
    source_end: int
    selection_text_hash: str
    chapter_content_hash: str


class CitationView(StrictReaderChatModel):
    block_id: str
    evidence_key: str
    context_evidence_ref_id: int
    chapter_id: int
    source_start: int
    source_end: int


class GenerationJobView(StrictReaderChatModel):
    id: int
    user_message_id: int
    status: GenerationJobStatus
    status_reason: str | None = None
    cancel_requested: bool
    retry_count: int
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class MessageView(StrictReaderChatModel):
    id: int
    conversation_id: int
    sequence: int
    role: MessageRole
    body: str
    client_message_id: str | None = None
    reply_to_message_id: int | None = None
    selection: SelectionSummary | None = None
    anchor: ChapterRangeAnchor | None = None
    citations: list[CitationView] = Field(default_factory=list)
    generation_job: GenerationJobView | None = None
    queryplan: QueryPlanTraceView | None = None
    created_at: datetime


class QueryPlanTraceView(StrictReaderChatModel):
    """Trace/citation-level exposure shared by Reader and Analysis Chat (26-04).

    Mirrors the QueryPlan consumer view (trace id, availability, fallback and
    leaf citation-jump targets). Only leaf/raw evidence appears in
    ``citation_jump``; summaries, scores, routing metadata and chat text never
    do (D-08).
    """

    trace_id: str = Field(min_length=32, max_length=64)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent: Literal["reader", "analysis"]
    anchor_kind: Literal["selection", "chapter_range"] | None = None
    cutoff_mode: str = Field(min_length=1, max_length=32)
    through_chapter: int = Field(gt=0)
    full_book_authorized: bool = False
    availability: list[dict[str, str]] = Field(default_factory=list)
    fallback: dict[str, Any] = Field(default_factory=dict)
    manifest_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_evidence_ids: list[str] = Field(default_factory=list)
    citation_jump: list[dict[str, Any]] = Field(default_factory=list)
    abstained: bool = False


class MessageAccepted(StrictReaderChatModel):
    """202 response after durable user-message + job commit."""

    message: MessageView
    job: GenerationJobView


# ---------------------------------------------------------------------------
# Strict model answer envelope (AI-SPEC §5)
# ---------------------------------------------------------------------------


class AnswerBlock(StrictReaderChatModel):
    block_id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)

    @field_validator("evidence_refs")
    @classmethod
    def non_empty_refs(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("answer blocks require at least one evidence ref")
        for ref in value:
            if not ref or not str(ref).strip():
                raise ValueError("evidence ref must be non-empty")
        return value


class UncertaintyPayload(StrictReaderChatModel):
    reason_code: str = Field(min_length=1, max_length=80)
    explanation: str = Field(min_length=1, max_length=2000)
    missing_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_known_reason_alias(cls, value: Any) -> Any:
        """Accept the provider's known ``reason`` alias without widening the schema."""

        if isinstance(value, dict) and "reason_code" not in value and "reason" in value:
            normalized = dict(value)
            normalized["reason_code"] = normalized.pop("reason")
            return normalized
        return value


class SuggestionCandidate(StrictReaderChatModel):
    """Chat-only proposal; never a domain write contract (D-06)."""

    candidate_type: SuggestionCandidateType
    target_ref: str | None = Field(default=None, max_length=160)
    proposal: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(min_length=1, max_length=8)
    requires_explicit_confirmation: Literal[True] = True


class ReaderAnswerEnvelope(StrictReaderChatModel):
    schema_version: Literal["reader-answer.v1"] = "reader-answer.v1"
    answer_blocks: list[AnswerBlock] = Field(default_factory=list)
    clarifying_question: str | None = Field(default=None, min_length=1, max_length=1000)
    uncertainty: UncertaintyPayload | None = None
    suggestion_candidates: list[SuggestionCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_content_or_uncertainty(self) -> ReaderAnswerEnvelope:
        if self.answer_blocks:
            return self
        if self.clarifying_question or self.uncertainty is not None:
            return self
        raise ValueError(
            "with empty answer_blocks, clarifying_question or uncertainty is required"
        )


def validate_answer_against_manifest(
    envelope: ReaderAnswerEnvelope,
    allowed_evidence_ids: set[str],
) -> None:
    """Business gate: every cited ref must belong to the frozen manifest allowlist."""

    def _check(refs: list[str], context: str) -> None:
        for ref in refs:
            if ref not in allowed_evidence_ids:
                raise ValueError(f"{context}: unknown evidence ref {ref!r}")

    for block in envelope.answer_blocks:
        _check(block.evidence_refs, f"answer block {block.block_id}")
    for suggestion in envelope.suggestion_candidates:
        _check(suggestion.evidence_refs, f"suggestion {suggestion.candidate_type}")


# ---------------------------------------------------------------------------
# Evidence / manifest wire shapes used by later plans
# ---------------------------------------------------------------------------


class ContextEvidenceRefView(StrictReaderChatModel):
    evidence_key: str
    source_type: EvidenceSourceType
    source_id: str
    chapter_id: int
    chapter_number: int
    source_start: int
    source_end: int
    content_hash: str
    excerpt: str
    sort_order: int = 0
    version_lineage: dict[str, Any] = Field(default_factory=dict)


class ContextManifestView(StrictReaderChatModel):
    id: int
    user_message_id: int
    full_book: bool
    cutoff_chapter_number: int
    manifest_checksum: str
    reading_progress_snapshot: dict[str, Any]
    evidence_refs: list[ContextEvidenceRefView] = Field(default_factory=list)
