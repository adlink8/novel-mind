"""Strict Pydantic contracts for narrative-unit source-of-truth records."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DomainProfile = Literal["fiction", "history"]
SourceSnapshotStatus = Literal["frozen"]
NarrativeUnitStage = Literal["draft", "canonical"]
NarrativeUnitStatus = Literal[
    "draft", "candidate", "active", "failed", "deprecated", "rolled_back"
]
NarrativeLifecycleStatus = Literal["current", "disputed", "deprecated", "deleted"]
NarrativeBuildStatus = Literal[
    "draft", "candidate", "active", "failed", "deprecated", "rolled_back"
]
PromotionJournalStatus = Literal["prepared", "committed", "failed", "rolled_back"]


class StrictContract(BaseModel):
    """Reject unknown fields at every narrative-unit trust boundary."""

    model_config = ConfigDict(extra="forbid")


class NarrativeEvidenceLineage(StrictContract):
    source_evidence_id: int = Field(..., ge=1)
    ref_key: str = Field(..., min_length=1, max_length=120)
    content_hash: str = Field(..., min_length=64, max_length=64)


class NarrativeSourceSnapshotCreate(StrictContract):
    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    domain_profile: DomainProfile


class NarrativeSourceSnapshotResponse(StrictContract):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    domain_profile: str
    ontology_profile: str
    status: str
    source_watermark: str
    manifest_checksum: str
    item_count: int
    created_at: datetime
    updated_at: datetime


class NarrativeUnitCreate(StrictContract):
    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    source_snapshot_id: int = Field(..., ge=1)
    source_judgment_id: int = Field(..., ge=1)
    source_candidate_id: int = Field(..., ge=1)
    domain_profile: DomainProfile
    ontology_profile: str = Field(..., min_length=1, max_length=100)
    unit_stage: NarrativeUnitStage = "draft"
    status: NarrativeUnitStatus = "draft"
    lifecycle_status: NarrativeLifecycleStatus = "current"
    canonical_id: str | None = Field(default=None, min_length=1, max_length=96)
    version: int = Field(default=1, ge=1)
    subject_key: str = Field(..., min_length=1, max_length=240)
    relation_type: str = Field(..., min_length=1, max_length=80)
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    content_hash: str = Field(..., min_length=64, max_length=64)
    prompt_hash: str | None = Field(default=None, min_length=64, max_length=64)
    schema_hash: str | None = Field(default=None, min_length=64, max_length=64)
    model_hash: str | None = Field(default=None, min_length=64, max_length=64)
    evidence: list[NarrativeEvidenceLineage] = Field(..., min_length=1)

    @model_validator(mode="after")
    def canonical_units_have_an_id(self) -> "NarrativeUnitCreate":
        if self.unit_stage == "canonical" and not self.canonical_id:
            raise ValueError("canonical units require canonical_id")
        return self


class NarrativeUnitResponse(StrictContract):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    source_snapshot_id: int
    source_judgment_id: int
    source_candidate_id: int
    primary_evidence_id: int
    domain_profile: str
    ontology_profile: str
    unit_stage: str
    status: str
    lifecycle_status: str
    canonical_id: str | None
    version: int
    subject_key: str
    relation_type: str
    question: str
    answer: str
    confidence: float
    evidence_count: int
    content_hash: str
    evidence_manifest_checksum: str
    prompt_hash: str | None
    schema_hash: str | None
    model_hash: str | None
    created_at: datetime
    updated_at: datetime


class NarrativeIndexBuildCreate(StrictContract):
    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    source_snapshot_id: int = Field(..., ge=1)
    domain_profile: DomainProfile
    build_key: str = Field(..., min_length=1, max_length=120)
    status: NarrativeBuildStatus = "draft"
    manifest_checksum: str = Field(..., min_length=64, max_length=64)
    config_checksum: str = Field(..., min_length=64, max_length=64)
    unit_count: int = Field(default=0, ge=0)


class NarrativeActivePointerCreate(StrictContract):
    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    domain_profile: DomainProfile
    build_id: int = Field(..., ge=1)
    pointer_version: int = Field(..., ge=1)
    active_manifest_checksum: str = Field(..., min_length=64, max_length=64)
    activated_at: datetime


class NarrativePromotionJournalCreate(StrictContract):
    owner_id: int = Field(..., ge=1)
    novel_id: int = Field(..., ge=1)
    domain_profile: DomainProfile
    transaction_key: str = Field(..., min_length=1, max_length=120)
    candidate_build_id: int = Field(..., ge=1)
    previous_build_id: int | None = Field(default=None, ge=1)
    status: PromotionJournalStatus = "prepared"
    candidate_checksum: str = Field(..., min_length=64, max_length=64)
    previous_checksum: str | None = Field(default=None, min_length=64, max_length=64)
    details: dict = Field(default_factory=dict)
