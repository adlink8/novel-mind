"""Phase 37-02 strict wire contracts for derivative generation jobs (D-37-02).

The client selects only an owned sealed ``context_package_id``, a generation
``intent`` and a client ``job_key``; the server derives the fork lineage, the
frozen package hash, the idempotency key and the prompt/schema/config hashes.
Every view echoes the persisted job/candidate lineage so the candidate-only
outcome stays auditable on the wire; nothing here exposes a publish path.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

JobKeyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class StrictDerivativeGenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationIntent(StrEnum):
    CONTINUATION = "continuation"
    REWRITE = "rewrite"


class GenerationJobCreateRequest(StrictDerivativeGenerationModel):
    """Client intent: one owned sealed context package + intent + job key."""

    context_package_id: int = Field(gt=0)
    intent: GenerationIntent
    job_key: JobKeyStr


class GenerationJobView(StrictDerivativeGenerationModel):
    id: int
    owner_id: int
    novel_id: int
    fork_id: int
    context_package_id: int
    package_hash: str
    intent: GenerationIntent
    job_key: str
    idempotency_key: str
    status: str
    status_reason: str | None = None
    error_code: str | None = None
    retry_count: int
    prompt_hash: str
    schema_hash: str
    config_hash: str
    model_lineage: dict = Field(default_factory=dict)
    price_snapshot: dict = Field(default_factory=dict)
    budget_policy: dict = Field(default_factory=dict)
    response_hash: str | None = None
    schema_version: str
    created_at: datetime
    updated_at: datetime


class GenerationJobSummary(StrictDerivativeGenerationModel):
    """Lean list row without full lineage."""

    id: int
    owner_id: int
    novel_id: int
    job_key: str
    intent: GenerationIntent
    status: str
    error_code: str | None = None
    package_hash: str
    created_at: datetime


class CandidateView(StrictDerivativeGenerationModel):
    """Strict-schema candidate row with the deterministic gate verdict."""

    id: int
    job_id: int
    intent: GenerationIntent
    draft_text: str
    summary: str | None = None
    citation_keys: list[str] = Field(default_factory=list)
    divergence: dict | None = None
    branch_suggestions: list[dict] = Field(default_factory=list)
    canon_delta_hash: str | None = None
    gate_verdict: str
    gate_reason: str | None = None
    package_hash: str
    prompt_hash: str
    schema_hash: str
    request_hash: str
    response_hash: str
    usage: dict = Field(default_factory=dict)
    cost_usd: Decimal | None = None
    model_lineage: dict = Field(default_factory=dict)
    approval_state: str
    schema_version: str
    created_at: datetime


class AttemptView(StrictDerivativeGenerationModel):
    """One auditable provider call attempt."""

    id: int
    job_id: int
    attempt_number: int
    status: str
    provider: str
    model_id: str
    provider_request_id: str | None = None
    request_hash: str
    response_hash: str | None = None
    reservation_key: str | None = None
    reserved_input_tokens: int
    reserved_output_tokens: int
    reserved_cost_usd: Decimal | None = None
    usage: dict = Field(default_factory=dict)
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    error_code: str | None = None


class GenerationJobCreateResponse(StrictDerivativeGenerationModel):
    job: GenerationJobView
    replayed: bool = False
    message: str | None = None


class GenerationJobRunResponse(StrictDerivativeGenerationModel):
    job: GenerationJobView
    candidate: CandidateView | None = None
    attempts: list[AttemptView] = Field(default_factory=list)


class GenerationJobCancelResponse(StrictDerivativeGenerationModel):
    job: GenerationJobView
    message: str | None = None


class GenerationJobDetailResponse(StrictDerivativeGenerationModel):
    job: GenerationJobView
    candidate: CandidateView | None = None
    attempts: list[AttemptView] = Field(default_factory=list)


class GenerationJobListResponse(StrictDerivativeGenerationModel):
    novel_id: int
    total: int
    items: list[GenerationJobSummary] = Field(default_factory=list)


__all__ = [
    "AttemptView",
    "CandidateView",
    "GenerationIntent",
    "GenerationJobCancelResponse",
    "GenerationJobCreateRequest",
    "GenerationJobCreateResponse",
    "GenerationJobDetailResponse",
    "GenerationJobListResponse",
    "GenerationJobRunResponse",
    "GenerationJobSummary",
    "GenerationJobView",
]
