"""
Phase 09 relationship observation and graph API contracts.

Strict fiction-only enums and response envelopes used by pipeline, API, UI,
and downstream Phase 10/11 readers. Legacy CharacterRelation is not exposed.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictRelationshipModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelationshipEdgeType(StrEnum):
    ALLY = "ally"
    ENEMY = "enemy"
    FAMILY = "family"
    MENTOR = "mentor"
    ROMANTIC = "romantic"


class RelationshipTransition(StrEnum):
    ESTABLISH = "establish"
    CHANGE = "change"
    END = "end"
    UNCERTAIN = "uncertain"


class ObservationPipelineStatus(StrEnum):
    CANDIDATE = "candidate"
    JUDGED = "judged"
    GATED = "gated"
    ACCEPTED = "accepted"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    REJECTED = "rejected"


class OverrideStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    NEEDS_RELINK = "needs_relink"


class RelationshipOverrideField(StrEnum):
    RELATION_TYPE = "relation_type"
    VALID_FROM = "valid_from"
    VALID_TO = "valid_to"
    TRANSITION = "transition"


class GraphDegradationMode(StrEnum):
    NORMAL = "normal"
    LARGE = "large"
    FILTERS_REQUIRED = "filters_required"


class RelationshipVersionSource(StrEnum):
    ACTIVE = "active"
    RUNNING_CANDIDATE = "running_candidate"
    HISTORY = "history"


class ProvenanceKind(StrEnum):
    MACHINE = "machine"
    MANUAL = "manual"


class RelationshipEdgeKind(StrEnum):
    """Truth tier for graph edges — accepted fact vs provisional co-occurrence."""

    ACCEPTED_OBSERVATION = "accepted_observation"
    PROVISIONAL_COOCCURRENCE = "provisional_cooccurrence"


class RelationshipGraphEdgeLabel(StrEnum):
    """
    Labels allowed on graph *projection* edges.

    Fiction enum types remain write-path authority for accepted observations.
    ``cooccur`` is honesty-only for provisional timeline co-occurrence and must
    never be written as an accepted observation relation_type.
    """

    ALLY = "ally"
    ENEMY = "enemy"
    FAMILY = "family"
    MENTOR = "mentor"
    ROMANTIC = "romantic"
    COOCCUR = "cooccur"


_FORBIDDEN_EDGE_TYPES = frozenset(
    {
        "causes",
        "precedes",
        "same_entity",
        "history",
        "friend",
        "lover",
        "allied_with",
        "conflicted_with",
    }
)


def _reject_forbidden_edge(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in _FORBIDDEN_EDGE_TYPES:
        raise ValueError(
            f"edge type {value!r} is not a Phase 09 fiction relationship edge"
        )
    return value


class NarrativeInterval(StrictRelationshipModel):
    valid_from_chapter: int = Field(gt=0)
    valid_from_narrative_index: int = Field(ge=0)
    valid_to_chapter: int | None = Field(default=None, gt=0)
    valid_to_narrative_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_interval_order(self) -> "NarrativeInterval":
        if (self.valid_to_chapter is None) != (self.valid_to_narrative_index is None):
            raise ValueError(
                "valid_to chapter and narrative_index must both be set or both null"
            )
        if (
            self.valid_to_chapter is not None
            and self.valid_to_narrative_index is not None
        ):
            if self.valid_to_chapter < self.valid_from_chapter or (
                self.valid_to_chapter == self.valid_from_chapter
                and self.valid_to_narrative_index < self.valid_from_narrative_index
            ):
                raise ValueError("valid_to must not precede valid_from")
        return self


class RelationshipEvidenceRef(StrictRelationshipModel):
    evidence_id: str = Field(min_length=1, max_length=80)
    chapter_id: int = Field(gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_offsets(self) -> "RelationshipEvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class AcceptedObservationContract(StrictRelationshipModel):
    """Write-path contract for an accepted observation row."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    analysis_version_id: int = Field(gt=0)
    source_judgment_id: int = Field(gt=0)
    candidate_id: int = Field(gt=0)
    judgment_id: int = Field(gt=0)
    source_character_id: int = Field(gt=0)
    target_character_id: int = Field(gt=0)
    relation_type: RelationshipEdgeType
    transition: Literal["establish", "change", "end"]
    interval: NarrativeInterval
    evidence: list[RelationshipEvidenceRef] = Field(min_length=1, max_length=8)
    evidence_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    confidence: float = Field(ge=0, le=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    model_lineage: dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation_type", mode="before")
    @classmethod
    def forbid_non_person_edges(cls, value: Any) -> Any:
        if isinstance(value, str):
            _reject_forbidden_edge(value)
        return value

    @model_validator(mode="after")
    def validate_endpoints_and_lineage(self) -> "AcceptedObservationContract":
        if self.source_character_id == self.target_character_id:
            raise ValueError("self-edges are forbidden")
        return self


class OverrideBaseContract(StrictRelationshipModel):
    author: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=4000)
    evidence_signature: str = Field(min_length=8, max_length=128)
    supersedes_id: int | None = Field(default=None, gt=0)
    status: OverrideStatus = OverrideStatus.ACTIVE
    provenance: dict[str, Any] = Field(default_factory=dict)


class CharacterIdentityOverrideCreate(OverrideBaseContract):
    novel_id: int = Field(gt=0)
    analysis_version_id: int = Field(gt=0)
    canonical_character_id: int = Field(gt=0)
    merged_character_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_merge_set(self) -> "CharacterIdentityOverrideCreate":
        if self.canonical_character_id in self.merged_character_ids:
            raise ValueError("canonical character cannot be listed among merged ids")
        if len(set(self.merged_character_ids)) != len(self.merged_character_ids):
            raise ValueError("merged_character_ids must be unique")
        return self


class RelationshipOverrideCreate(OverrideBaseContract):
    novel_id: int = Field(gt=0)
    analysis_version_id: int = Field(gt=0)
    observation_id: int | None = Field(default=None, gt=0)
    logical_relationship_key: str = Field(min_length=1, max_length=160)
    field_name: RelationshipOverrideField
    value: dict[str, Any]


class RelationshipSemanticJudgment(StrictRelationshipModel):
    """Strict LLM output — no owner/version/status/write fields."""

    schema_version: Literal["relationship-semantic-judgment.v1"] = (
        "relationship-semantic-judgment.v1"
    )
    candidate_key: str = Field(min_length=1, max_length=160)
    source_ref: str = Field(min_length=1, max_length=80)
    target_ref: str = Field(min_length=1, max_length=80)
    relation_type: RelationshipEdgeType
    transition: RelationshipTransition
    valid_from_evidence_id: str = Field(min_length=1, max_length=80)
    valid_to_evidence_id: str | None = Field(default=None, max_length=80)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(default="", max_length=2000)
    risk_flags: list[str] = Field(default_factory=list)

    @field_validator("relation_type", mode="before")
    @classmethod
    def forbid_timeline_edges(cls, value: Any) -> Any:
        if isinstance(value, str):
            _reject_forbidden_edge(value)
        return value


class RelationshipGraphNode(StrictRelationshipModel):
    character_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)
    first_visible_chapter: int = Field(gt=0)


class RelationshipGraphEdge(StrictRelationshipModel):
    observation_id: int = Field(gt=0)
    source_character_id: int = Field(gt=0)
    target_character_id: int = Field(gt=0)
    relation_type: RelationshipGraphEdgeLabel
    transition: Literal["establish", "change", "end"]
    confidence: float = Field(ge=0, le=1)
    valid_from_chapter: int = Field(gt=0)
    valid_to_chapter: int | None = Field(default=None, gt=0)
    provenance: ProvenanceKind = ProvenanceKind.MACHINE
    evidence_preview: str | None = Field(default=None, max_length=400)
    evidence_count: int = Field(default=0, ge=0)
    edge_kind: RelationshipEdgeKind = RelationshipEdgeKind.ACCEPTED_OBSERVATION
    # Heuristic fiction label for provisional co-occurrence only; never accepted fact.
    suggested_type: RelationshipEdgeType | None = None

    @field_validator("relation_type", mode="before")
    @classmethod
    def forbid_non_person_edges(cls, value: Any) -> Any:
        if isinstance(value, str):
            _reject_forbidden_edge(value)
        return value

    @model_validator(mode="after")
    def validate_endpoints_and_truth_tier(self) -> "RelationshipGraphEdge":
        if self.source_character_id == self.target_character_id:
            raise ValueError("self-edges are forbidden")
        if (
            self.edge_kind == RelationshipEdgeKind.PROVISIONAL_COOCCURRENCE
            and self.relation_type != RelationshipGraphEdgeLabel.COOCCUR
        ):
            raise ValueError(
                "provisional co-occurrence edges must use relation_type=cooccur"
            )
        if (
            self.edge_kind == RelationshipEdgeKind.ACCEPTED_OBSERVATION
            and self.relation_type == RelationshipGraphEdgeLabel.COOCCUR
        ):
            raise ValueError(
                "accepted observation edges cannot use relation_type=cooccur"
            )
        return self


class RelationshipCounts(StrictRelationshipModel):
    nodes: int = Field(default=0, ge=0)
    edges: int = Field(default=0, ge=0)
    relation_types: dict[str, int] = Field(default_factory=dict)


class RelationshipDegradation(StrictRelationshipModel):
    mode: GraphDegradationMode = GraphDegradationMode.NORMAL
    node_count: int = Field(default=0, ge=0)
    edge_count: int = Field(default=0, ge=0)
    hard_node_cap: int = Field(default=500, ge=1)
    hard_edge_cap: int = Field(default=1500, ge=1)
    message: str | None = None


class RelationshipGraphEnvelope(StrictRelationshipModel):
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    source: RelationshipVersionSource
    through_chapter: int = Field(gt=0)
    full_book: bool = False
    cutoff_chapter: int = Field(gt=0)
    nodes: list[RelationshipGraphNode] = Field(default_factory=list)
    edges: list[RelationshipGraphEdge] = Field(default_factory=list)
    counts: RelationshipCounts = Field(default_factory=RelationshipCounts)
    available_relation_types: list[RelationshipGraphEdgeLabel] = Field(
        default_factory=list
    )
    available_character_ids: list[int] = Field(default_factory=list)
    degradation: RelationshipDegradation = Field(
        default_factory=RelationshipDegradation
    )
    generated_at: datetime | None = None


class RelationshipEvidenceResponse(StrictRelationshipModel):
    observation_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    through_chapter: int = Field(gt=0)
    relation_type: RelationshipEdgeType
    source_character_id: int = Field(gt=0)
    target_character_id: int = Field(gt=0)
    evidence: list[RelationshipEvidenceRef] = Field(default_factory=list)
    provenance: ProvenanceKind = ProvenanceKind.MACHINE


class CharacterIdentityOverrideResponse(StrictRelationshipModel):
    id: int
    novel_id: int
    analysis_version_id: int
    canonical_character_id: int
    merged_character_ids: list[int]
    author: str
    reason: str
    evidence_signature: str
    supersedes_id: int | None = None
    status: OverrideStatus
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class RelationshipOverrideResponse(StrictRelationshipModel):
    id: int
    novel_id: int
    analysis_version_id: int
    observation_id: int | None = None
    logical_relationship_key: str
    field_name: RelationshipOverrideField
    value: dict[str, Any]
    author: str
    reason: str
    evidence_signature: str
    supersedes_id: int | None = None
    status: OverrideStatus
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
