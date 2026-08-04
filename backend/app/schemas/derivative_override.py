"""Phase 37-04 strict wire contracts for explicit divergence overrides (D-37-03).

The client can never widen the override surface: create carries only the
owner-stated divergence (reason + affected evidence + optional kind when the
candidate declared no CanonDelta) plus the target project/chapter; approve
carries only the explicit approval note; reject carries only the rejection
note. Every view echoes the server-frozen override (kind/reason/evidence/
canon_delta_hash/evidence_snapshot/actor/status/approval journal) and the
immutable ``PublishedDerivativeRevision`` DTO the Phase 39 consumer reads.
No client-supplied ``owner_id`` / ``novel_id`` / ``fork_id`` / ``approval_state``
is accepted (``extra="forbid"``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Divergence reason / approval note: the raw string reaches the service which
# strips and rejects blanks with the stable ``missing_reason`` /
# ``missing_approval`` codes; an empty string fails the wire schema first.
ReasonStr = Annotated[
    str, StringConstraints(min_length=1, max_length=4000)
]


class StrictDerivativeOverrideModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverrideKind(StrEnum):
    CHARACTER = "character"
    TIMELINE = "timeline"
    WORLD_RULE = "world_rule"
    CLUE = "clue"
    OTHER = "other"


class OverrideStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class OverrideCreateRequest(StrictDerivativeOverrideModel):
    """Owner divergence intent: blocked/override candidate + target chapter."""

    candidate_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    reason: ReasonStr
    # Server-authoritative gate: the override service rejects an empty set with
    # the stable ``missing_evidence`` code (a needs_override candidate may
    # implicitly reuse its declared CanonDelta evidence).
    affected_evidence: list[str] = Field(default_factory=list)
    # Required only when the candidate declared no CanonDelta (D-37-03).
    kind: OverrideKind | None = None


class OverrideApproveRequest(StrictDerivativeOverrideModel):
    """Explicit approval note; an approve without approval is rejected."""

    approval_reason: ReasonStr


class OverrideRejectRequest(StrictDerivativeOverrideModel):
    """Explicit rejection note; no revision is ever materialized."""

    rejection_reason: ReasonStr


class OverrideView(StrictDerivativeOverrideModel):
    """Server-frozen override row with the explicit review action journal."""

    id: int
    owner_id: int
    novel_id: int
    project_id: int
    chapter_id: int
    fork_id: int
    candidate_id: int
    job_id: int
    kind: OverrideKind
    reason: str
    affected_evidence: list[str] = Field(default_factory=list)
    canon_delta_hash: str
    evidence_snapshot: dict = Field(default_factory=dict)
    actor_id: int | None = None
    approval_state: OverrideStatus
    approver_id: int | None = None
    approved_at: datetime | None = None
    rejected_at: datetime | None = None
    approval_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class PublishedDerivativeRevisionView(StrictDerivativeOverrideModel):
    """Immutable Phase 39 consumer contract (mirrors the frozen DTO)."""

    owner_id: int
    project_id: int
    fork_id: int
    revision_id: int
    version_id: int
    status: str
    source_snapshot: str
    manifest_hash: str
    citation_hash: str
    asset_hashes: list[str] = Field(default_factory=list)
    approval: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)


class OverrideCreateResponse(StrictDerivativeOverrideModel):
    override: OverrideView
    message: str | None = None


class OverrideApproveResponse(StrictDerivativeOverrideModel):
    override: OverrideView
    published: PublishedDerivativeRevisionView
    message: str | None = None


class OverrideRejectResponse(StrictDerivativeOverrideModel):
    override: OverrideView
    message: str | None = None


class OverrideDetailResponse(StrictDerivativeOverrideModel):
    override: OverrideView


class OverrideListResponse(StrictDerivativeOverrideModel):
    novel_id: int
    total: int
    items: list[OverrideView] = Field(default_factory=list)


__all__ = [
    "OverrideApproveRequest",
    "OverrideApproveResponse",
    "OverrideCreateRequest",
    "OverrideCreateResponse",
    "OverrideDetailResponse",
    "OverrideKind",
    "OverrideListResponse",
    "OverrideRejectRequest",
    "OverrideRejectResponse",
    "OverrideStatus",
    "OverrideView",
    "PublishedDerivativeRevisionView",
]
