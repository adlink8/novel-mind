"""
Knowledge graph data-contract schemas.

API schemas describe persisted audit records. LLM output schemas are strict and
separate because raw model responses are never accepted graph facts.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DomainProfile = Literal["fiction", "history"]
RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
CandidateStatus = Literal[
    "candidate",
    "proposed",
    "rejected",
    "needs_human_review",
    "accepted",
]
JudgmentStatus = Literal[
    "pending",
    "schema_failed",
    "evidence_failed",
    "threshold_failed",
    "conflict_failed",
    "needs_human_review",
    "accepted",
    "rejected",
]
GateStatus = Literal[
    "pending",
    "schema_passed",
    "schema_failed",
    "evidence_passed",
    "evidence_failed",
    "threshold_passed",
    "threshold_failed",
    "conflict_passed",
    "conflict_failed",
    "accepted",
    "needs_human_review",
    "rejected",
]
EvidenceSourceType = Literal["text_chunk", "chapter", "accepted_relation"]
ReviewStatus = Literal["open", "in_review", "resolved", "rejected"]
EndpointKind = Literal["entity_candidate", "event_candidate", "accepted_relation"]


FICTION_ENTITY_TYPES = (
    "character",
    "organization",
    "location",
    "artifact",
    "concept",
)
HISTORY_ENTITY_TYPES = (
    "person",
    "polity",
    "institution",
    "location",
    "source_text",
    "concept",
)
FICTION_EVENT_TYPES = (
    "plot",
    "character_action",
    "conflict",
    "reveal",
    "world_event",
)
HISTORY_EVENT_TYPES = (
    "political",
    "military",
    "diplomatic",
    "social",
    "economic",
    "source_claim",
)
FICTION_RELATION_TYPES = (
    "ally",
    "enemy",
    "family",
    "mentor",
    "romantic",
    "causes",
    "precedes",
    "same_entity",
)
HISTORY_RELATION_TYPES = (
    "allied_with",
    "conflicted_with",
    "ruled",
    "served",
    "succeeded",
    "caused",
    "preceded",
    "same_entity",
)

ENTITY_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_ENTITY_TYPES,
    "history": HISTORY_ENTITY_TYPES,
}
EVENT_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_EVENT_TYPES,
    "history": HISTORY_EVENT_TYPES,
}
RELATION_TYPES_BY_DOMAIN_PROFILE = {
    "fiction": FICTION_RELATION_TYPES,
    "history": HISTORY_RELATION_TYPES,
}


class KnowledgeExtractionRunCreate(BaseModel):
    """Create a persisted extraction run."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_name: str = Field(..., min_length=1, max_length=200)
    domain_profile: DomainProfile = "fiction"
    ontology_profile: str = Field(default="fiction.v1", min_length=1, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    config_snapshot: dict = Field(default_factory=dict)


class KnowledgeRunStartRequest(BaseModel):
    """API request for creating a user-owned extraction run."""

    novel_id: int = Field(..., ge=1)
    run_name: str = Field(..., min_length=1, max_length=200)
    domain_profile: DomainProfile = "fiction"
    ontology_profile: str = Field(default="fiction.v1", min_length=1, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    config_snapshot: dict = Field(default_factory=dict)


class KnowledgeExtractionRunResponse(BaseModel):
    """Persisted extraction run response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_name: str
    domain_profile: str
    ontology_profile: str
    status: str
    prompt_version: str | None = None
    candidate_count: int
    judgment_count: int
    accepted_count: int
    rejected_count: int
    review_count: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost_usd: float
    latency_ms_p50: float | None = None
    latency_ms_p95: float | None = None
    config_snapshot: dict
    metrics_summary: dict
    error_detail: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeEvidenceRefCreate(BaseModel):
    """Evidence locator backed by a real source row or future accepted relation."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    ref_key: str = Field(..., min_length=1, max_length=120)
    source_type: EvidenceSourceType
    text_chunk_id: int | None = Field(default=None, ge=1)
    chapter_id: int | None = Field(default=None, ge=1)
    accepted_relation_id: int | None = Field(default=None, ge=1)
    source_locator: dict = Field(default_factory=dict)
    excerpt: str | None = None
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    metadata_json: dict = Field(default_factory=dict)


class KnowledgeEvidenceRefResponse(BaseModel):
    """Evidence locator response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    ref_key: str
    source_type: str
    text_chunk_id: int | None = None
    chapter_id: int | None = None
    accepted_relation_id: int | None = None
    source_locator: dict
    excerpt: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class KnowledgeEntityCandidateCreate(BaseModel):
    """Create an entity candidate from deterministic package construction."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    canonical_name: str = Field(..., min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    domain_profile: DomainProfile = "fiction"
    entity_type: str = Field(..., min_length=1, max_length=60)
    evidence_refs: list[str] = Field(..., min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: CandidateStatus = "candidate"
    notes: str | None = None


class KnowledgeEntityCandidateResponse(BaseModel):
    """Entity candidate response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    canonical_name: str
    aliases: list
    domain_profile: str
    entity_type: str
    evidence_refs: list
    source_refs: list
    confidence: float | None = None
    status: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeEventCandidateCreate(BaseModel):
    """Create an event candidate from deterministic package construction."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=240)
    summary: str | None = None
    domain_profile: DomainProfile = "fiction"
    event_type: str = Field(..., min_length=1, max_length=60)
    time_refs: list[str] = Field(default_factory=list)
    location_refs: list[str] = Field(default_factory=list)
    participant_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(..., min_length=1)
    source_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    status: CandidateStatus = "candidate"


class KnowledgeEventCandidateResponse(BaseModel):
    """Event candidate response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    title: str
    summary: str | None = None
    domain_profile: str
    event_type: str
    time_refs: list
    location_refs: list
    participant_refs: list
    evidence_refs: list
    source_refs: list
    confidence: float | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class KnowledgeRelationCandidateCreate(BaseModel):
    """Create a relation candidate. This is never an accepted graph fact."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    domain_profile: DomainProfile = "fiction"
    relation_type: str = Field(..., min_length=1, max_length=80)
    source_kind: EndpointKind
    source_id: int = Field(..., ge=1)
    target_kind: EndpointKind
    target_id: int = Field(..., ge=1)
    recall_signals: dict = Field(default_factory=dict)
    package_snapshot: dict = Field(default_factory=dict)
    evidence_refs: list[str] = Field(..., min_length=1)
    status: CandidateStatus = "candidate"


class KnowledgeRelationCandidateResponse(BaseModel):
    """Relation candidate response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    domain_profile: str
    relation_type: str
    source_kind: str
    source_id: int
    target_kind: str
    target_id: int
    recall_signals: dict
    package_snapshot: dict
    evidence_refs: list
    status: str
    created_at: datetime
    updated_at: datetime


class KnowledgeRelationJudgmentCreate(BaseModel):
    """Persist a structured LLM judgment plus deterministic gate state."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    relation_candidate_id: int = Field(..., ge=1)
    prompt_version: str = Field(..., min_length=1, max_length=100)
    model_name: str = Field(..., min_length=1, max_length=160)
    relation_type: str = Field(..., min_length=1, max_length=80)
    confidence: float = Field(..., ge=0, le=1)
    evidence_refs: list[str] = Field(..., min_length=1)
    rationale: str | None = None
    risk_flags: list[str] = Field(default_factory=list)
    raw_output: dict = Field(default_factory=dict)
    structured_output: dict = Field(default_factory=dict)
    status: JudgmentStatus = "pending"
    gate_status: GateStatus = "pending"
    gate_failures: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    latency_ms: float | None = Field(default=None, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class KnowledgeRelationJudgmentResponse(BaseModel):
    """Judgment response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    relation_candidate_id: int
    prompt_version: str
    model_name: str
    relation_type: str
    confidence: float
    evidence_refs: list
    rationale: str | None = None
    risk_flags: list
    raw_output: dict
    structured_output: dict
    status: str
    gate_status: str
    gate_failures: list
    needs_human_review: bool
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeReviewQueueCreate(BaseModel):
    """Create a human-review queue item."""

    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    run_id: int = Field(..., ge=1)
    relation_candidate_id: int | None = Field(default=None, ge=1)
    judgment_id: int | None = Field(default=None, ge=1)
    review_type: str = Field(..., min_length=1, max_length=60)
    status: ReviewStatus = "open"
    priority: int = Field(default=5, ge=1, le=10)
    reason: str = Field(..., min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    resolution: str | None = Field(default=None, max_length=60)
    reviewer_notes: str | None = None
    assigned_to: str | None = Field(default=None, max_length=120)


class KnowledgeReviewActionRequest(BaseModel):
    """Manual review action payload."""

    reviewer_notes: str | None = None


class KnowledgeReviewQueueResponse(BaseModel):
    """Human-review queue response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    run_id: int
    relation_candidate_id: int | None = None
    judgment_id: int | None = None
    review_type: str
    status: str
    priority: int
    reason: str
    evidence_refs: list
    resolution: str | None = None
    reviewer_notes: str | None = None
    assigned_to: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeLLMRelationJudgmentOutput(BaseModel):
    """Strict LLM output contract, independent from API response schemas."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: int = Field(..., ge=1)
    relation_type: str = Field(..., min_length=1, max_length=80)
    confidence: float = Field(..., ge=0, le=1)
    evidence_refs: list[str] = Field(..., min_length=1)
    rationale: str = Field(..., min_length=1)
    risk_flags: list[str] = Field(default_factory=list)
    needs_human_review: bool = False


class KnowledgeLLMEntityOutput(BaseModel):
    """Strict entity proposal emitted by an LLM judgment step."""

    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(..., min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    entity_type: str = Field(..., min_length=1, max_length=60)
    evidence_refs: list[str] = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)


class KnowledgeLLMEventOutput(BaseModel):
    """Strict event proposal emitted by an LLM judgment step."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=240)
    summary: str = Field(..., min_length=1)
    event_type: str = Field(..., min_length=1, max_length=60)
    time_refs: list[str] = Field(default_factory=list)
    location_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)
