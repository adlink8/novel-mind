"""Illustration job and asset strict contracts (Phase 33-01, REQ-VIS-04).

D-33-01..D-33-04: an illustration request is an idempotent durable job, its
provider output is an immutable candidate asset revision, and consistency is
review evidence, not canon. This module owns:

- strict typed wire contracts with ``extra="forbid"`` and frozen immutable
  lineage payloads (``IllustrationLineage`` / ``IllustrationJobContract`` /
  ``AssetRevisionContract`` / ``ConsistencyReportContract`` / ``PriceSnapshot``);
- the closed job/attempt/reservation/approval/rights/consistency vocabularies
  plus the candidate-only approval state machine;
- canonical hash helpers so the job idempotency key and asset payloads are
  byte-replayable (owner/novel/SceneSpec/prompt/model/config lineage);
- server-side gates that fail closed on a non-replayable idempotency key,
  job/asset lineage drift, empty-success assets and illegal review transitions.

Nothing here writes to the database and nothing promotes a generated asset to
canon; approval is an append-only review event (D-33-03) and Phase 34 owns
publish. ``Novel.cover_url`` and raw upload storage remain unrelated.
"""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ILLUSTRATION_SCHEMA_VERSION = "illustration.v1"
ILLUSTRATION_ASSET_SCHEMA_VERSION = "illustration-asset.v1"
ILLUSTRATION_CONSISTENCY_SCHEMA_VERSION = "illustration-consistency.v1"
ILLUSTRATION_JOB_ARTIFACT_KIND = "illustration_job"
ILLUSTRATION_ASSET_ARTIFACT_KIND = "illustration_asset"
ILLUSTRATION_CONSISTENCY_ARTIFACT_KIND = "illustration_consistency_report"

# Mirrors the ORM vocabulary so schema/model/migration stay byte-identical.
ILLUSTRATION_JOB_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
)
ILLUSTRATION_JOB_NONTERMINAL_STATUSES = (
    "queued",
    "running",
    "paused_budget",
    "paused_dependency",
)
ILLUSTRATION_ATTEMPT_STATUSES = (
    "started",
    "succeeded",
    "failed",
    "cancelled",
    "outcome_unknown",
)
ILLUSTRATION_RESERVATION_STATUSES = ("reserved", "settled", "released", "failed")
ILLUSTRATION_APPROVAL_STATES = (
    "candidate",
    "proposal_ready",
    "rejected",
    "superseded",
)
ILLUSTRATION_REVIEW_ACTIONS = ("approve", "reject", "supersede", "needs_relink")
ILLUSTRATION_ACTOR_SOURCES = ("human", "machine")
ILLUSTRATION_RIGHTS_STATUSES = ("unreviewed", "cleared", "pending", "denied")
ILLUSTRATION_CONSISTENCY_VERDICTS = ("pass", "concern", "fail", "unavailable")


class StrictIllustrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IllustrationJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED_BUDGET = "paused_budget"
    PAUSED_DEPENDENCY = "paused_dependency"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class IllustrationAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class IllustrationReservationStatus(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"
    FAILED = "failed"


class IllustrationApprovalState(StrEnum):
    CANDIDATE = "candidate"
    PROPOSAL_READY = "proposal_ready"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class IllustrationReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    SUPERSEDE = "supersede"
    NEEDS_RELINK = "needs_relink"


class IllustrationActorSource(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


class IllustrationRightsStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    CLEARED = "cleared"
    PENDING = "pending"
    DENIED = "denied"


class IllustrationConsistencyVerdict(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"


class IllustrationGateError(ValueError):
    """Fail-closed gate violation while validating an illustration contract."""


# ---------------------------------------------------------------------------
# Canonical hashing (byte-replayable lineage)
# ---------------------------------------------------------------------------


def canonical_illustration_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Frozen lineage envelope and job idempotency key (D-33-01)
# ---------------------------------------------------------------------------


class IllustrationLineage(StrictIllustrationModel):
    """Deterministic owner/novel/SceneSpec/prompt/model/config lineage.

    ``model_lineage`` is provider-neutral (provider id + model id recorded by
    the gateway); it never carries secrets or provider-specific prompts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_revision_id: int | None = Field(default=None, gt=0)
    prompt_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    visual_bible_revision_id: int | None = Field(default=None, gt=0)
    visual_bible_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def illustration_lineage_payload(
    owner_id: int, novel_id: int, lineage: IllustrationLineage
) -> dict[str, Any]:
    """Canonical idempotency payload: the lineage that must replay everywhere."""
    return {
        "artifact_kind": ILLUSTRATION_JOB_ARTIFACT_KIND,
        "schema_version": ILLUSTRATION_SCHEMA_VERSION,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "scene_spec_hash": lineage.scene_spec_hash,
        "prompt_revision_id": lineage.prompt_revision_id,
        "prompt_revision_hash": lineage.prompt_revision_hash,
        "visual_bible_revision_id": lineage.visual_bible_revision_id,
        "visual_bible_revision_hash": lineage.visual_bible_revision_hash,
        "source_snapshot_id": lineage.source_snapshot_id,
        "source_snapshot_hash": lineage.source_snapshot_hash,
        "cutoff_chapter": lineage.cutoff_chapter,
        "model_lineage": lineage.model_lineage,
        "config_hash": lineage.config_hash,
    }


def build_illustration_idempotency_key(
    owner_id: int, novel_id: int, lineage: IllustrationLineage
) -> str:
    """D-33-01 idempotency key: one nonterminal job per lineage, one charge."""
    return canonical_illustration_hash(
        illustration_lineage_payload(owner_id, novel_id, lineage)
    )


class IllustrationJobContract(StrictIllustrationModel):
    """Frozen durable job creation contract; every lineage field is mandatory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration.v1"] = "illustration.v1"
    artifact_kind: Literal["illustration_job"] = "illustration_job"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    job_key: str = Field(min_length=1, max_length=120)
    lineage: IllustrationLineage
    price_snapshot: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Price snapshot (D-33-02): cost is always settled against a frozen price.
# ---------------------------------------------------------------------------


class PriceSnapshot(StrictIllustrationModel):
    """Frozen deployment price snapshot used for worst-case reservation.

    ``input_price_per_million`` / ``output_price_per_million`` are token prices
    (USD per million tokens); ``image_price_per_image`` is a flat per-image
    price. A None value means the deployment pricing is unknown and cost cannot
    be reserved (fail closed, D-33-02).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    input_price_per_million: Decimal | None = Field(default=None, ge=0)
    output_price_per_million: Decimal | None = Field(default=None, ge=0)
    image_price_per_image: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def at_least_one_price(self) -> "PriceSnapshot":
        if (
            self.input_price_per_million is None
            and self.output_price_per_million is None
            and self.image_price_per_image is None
        ):
            raise ValueError(
                "price snapshot must carry at least one known price; unknown "
                "pricing fails closed before any provider call"
            )
        return self


# ---------------------------------------------------------------------------
# Asset revision (D-33-03): immutable candidate with immutable lineage
# ---------------------------------------------------------------------------


class AssetRevisionContract(StrictIllustrationModel):
    """Frozen provider-output candidate; never empty success (D-33-03)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration-asset.v1"] = "illustration-asset.v1"
    artifact_kind: Literal["illustration_asset"] = "illustration_asset"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    job_id: int = Field(gt=0)
    revision_key: str = Field(min_length=1, max_length=180)
    revision_number: int = Field(ge=1)
    asset_id: str = Field(min_length=1, max_length=200)
    storage_key: str = Field(min_length=1, max_length=320)
    mime_type: str = Field(min_length=1, max_length=100)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    size_bytes: int = Field(gt=0)
    bytes_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage: IllustrationLineage
    provider: str = Field(min_length=1, max_length=64)
    provider_model: str = Field(min_length=1, max_length=120)
    provider_request_id: str | None = Field(default=None, max_length=160)
    provider_response: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    rights_status: IllustrationRightsStatus = IllustrationRightsStatus.UNREVIEWED
    approval_state: IllustrationApprovalState = IllustrationApprovalState.CANDIDATE
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Consistency report (D-33-04): evidence, not canon
# ---------------------------------------------------------------------------


class ConsistencyReportContract(StrictIllustrationModel):
    """Frozen consistency evaluation evidence with fixture/model lineage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["illustration-consistency.v1"] = (
        "illustration-consistency.v1"
    )
    artifact_kind: Literal["illustration_consistency_report"] = (
        "illustration_consistency_report"
    )
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    asset_revision_id: int = Field(gt=0)
    report_key: str = Field(min_length=1, max_length=180)
    evaluator_id: str = Field(min_length=1, max_length=120)
    evaluator_version: str = Field(min_length=1, max_length=64)
    model_lineage: dict[str, Any] = Field(default_factory=dict)
    fixture_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=64)
    scores: dict[str, Any] = Field(default_factory=dict)
    verdict: IllustrationConsistencyVerdict
    details: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Server-side gates
# ---------------------------------------------------------------------------


def validate_illustration_job_contract(job: IllustrationJobContract) -> None:
    """Job idempotency key must replay exactly from its owner/novel/lineage."""
    expected = build_illustration_idempotency_key(
        job.owner_id, job.novel_id, job.lineage
    )
    if job.idempotency_key != expected:
        raise IllustrationGateError(
            "illustration job idempotency_key does not replay from its lineage"
        )
    if any(key in job.price_snapshot for key in ("api_key", "secret", "token")):
        raise IllustrationGateError(
            "illustration job price_snapshot must not carry secrets"
        )


def validate_asset_bytes(asset: AssetRevisionContract, payload: bytes) -> None:
    """Server-side bytes gate: hash, size and MIME must match the contract."""
    actual_hash = sha256(payload).hexdigest()
    if actual_hash != asset.bytes_hash:
        raise IllustrationGateError(
            "asset bytes_hash does not match the provider payload"
        )
    if len(payload) != asset.size_bytes:
        raise IllustrationGateError(
            "asset size_bytes does not match the provider payload"
        )
    if not payload:
        raise IllustrationGateError(
            "provider returned an empty asset; it cannot become a successful "
            "AssetRevision (D-33-01)"
        )


def validate_asset_revision_contract(
    asset: AssetRevisionContract, job: IllustrationJobContract
) -> None:
    """An asset revision must replay exactly from its job's frozen lineage.

    Fails closed on owner/novel scope drift, SceneSpec/prompt/Visual Bible
    revision drift, source snapshot/cutoff drift, model/config drift and any
    attempt to hand an unapproved candidate downstream. The job id linkage is
    enforced by the durable layer (``asset_revisions.job_id`` FK), not here.
    """
    if asset.owner_id != job.owner_id or asset.novel_id != job.novel_id:
        raise IllustrationGateError(
            "asset revision owner/novel scope does not match the job"
        )
    if asset.lineage.scene_spec_hash != job.lineage.scene_spec_hash:
        raise IllustrationGateError(
            "asset scene_spec_hash does not match the job lineage"
        )
    if asset.lineage.prompt_revision_id != job.lineage.prompt_revision_id:
        raise IllustrationGateError(
            "asset prompt_revision_id does not match the job lineage"
        )
    if asset.lineage.prompt_revision_hash != job.lineage.prompt_revision_hash:
        raise IllustrationGateError(
            "asset prompt_revision_hash does not match the job lineage"
        )
    if (
        asset.lineage.visual_bible_revision_hash
        != job.lineage.visual_bible_revision_hash
    ):
        raise IllustrationGateError(
            "asset visual_bible_revision_hash does not match the job lineage"
        )
    if asset.lineage.source_snapshot_id != job.lineage.source_snapshot_id:
        raise IllustrationGateError(
            "asset source_snapshot_id does not match the job lineage"
        )
    if asset.lineage.source_snapshot_hash != job.lineage.source_snapshot_hash:
        raise IllustrationGateError(
            "asset source_snapshot_hash does not match the job lineage"
        )
    if asset.lineage.cutoff_chapter != job.lineage.cutoff_chapter:
        raise IllustrationGateError(
            "asset cutoff_chapter does not match the job lineage"
        )
    if asset.lineage.model_lineage != job.lineage.model_lineage:
        raise IllustrationGateError(
            "asset model_lineage does not match the job lineage"
        )
    if asset.lineage.config_hash != job.lineage.config_hash:
        raise IllustrationGateError("asset config_hash does not match the job lineage")
    if asset.approval_state is not IllustrationApprovalState.CANDIDATE:
        raise IllustrationGateError(
            "a new AssetRevision must be created as a candidate (D-33-03)"
        )


# ---------------------------------------------------------------------------
# Candidate-only approval gate (append-only, explicit, idempotent)
# ---------------------------------------------------------------------------

ILLUSTRATION_ACTION_TO_STATE: dict[
    IllustrationReviewAction, IllustrationApprovalState
] = {
    IllustrationReviewAction.APPROVE: IllustrationApprovalState.PROPOSAL_READY,
    IllustrationReviewAction.REJECT: IllustrationApprovalState.REJECTED,
    IllustrationReviewAction.NEEDS_RELINK: IllustrationApprovalState.CANDIDATE,
    IllustrationReviewAction.SUPERSEDE: IllustrationApprovalState.SUPERSEDED,
}

LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS: dict[
    IllustrationApprovalState, frozenset[IllustrationReviewAction]
] = {
    IllustrationApprovalState.CANDIDATE: frozenset(
        {
            IllustrationReviewAction.APPROVE,
            IllustrationReviewAction.REJECT,
            IllustrationReviewAction.NEEDS_RELINK,
        }
    ),
    IllustrationApprovalState.PROPOSAL_READY: frozenset(
        {
            IllustrationReviewAction.REJECT,
            IllustrationReviewAction.SUPERSEDE,
            IllustrationReviewAction.NEEDS_RELINK,
        }
    ),
    IllustrationApprovalState.REJECTED: frozenset(
        {
            IllustrationReviewAction.SUPERSEDE,
            IllustrationReviewAction.NEEDS_RELINK,
        }
    ),
    IllustrationApprovalState.SUPERSEDED: frozenset(),
}


def is_legal_illustration_review_action(
    state: IllustrationApprovalState | str, action: IllustrationReviewAction | str
) -> bool:
    current = IllustrationApprovalState(state)
    requested = IllustrationReviewAction(action)
    return requested in LEGAL_ILLUSTRATION_REVIEW_TRANSITIONS[current]


def validate_legal_illustration_review_action(
    state: IllustrationApprovalState | str, action: IllustrationReviewAction | str
) -> None:
    current = IllustrationApprovalState(state)
    requested = IllustrationReviewAction(action)
    if not is_legal_illustration_review_action(current, requested):
        raise IllustrationGateError(
            f"illegal review action {requested.value!r} from state {current.value!r}"
        )


def approval_state_after(
    state: IllustrationApprovalState | str, action: IllustrationReviewAction | str
) -> IllustrationApprovalState:
    current = IllustrationApprovalState(state)
    requested = IllustrationReviewAction(action)
    validate_legal_illustration_review_action(current, requested)
    return ILLUSTRATION_ACTION_TO_STATE[requested]


class IllustrationReviewEventInput(StrictIllustrationModel):
    """One append-only approval action candidate; result state is server-derived."""

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    asset_revision_id: int = Field(gt=0)
    event_key: str = Field(min_length=1, max_length=160)
    action: IllustrationReviewAction
    actor_source: IllustrationActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_approval_state: IllustrationApprovalState


def validate_illustration_review_event(
    event: IllustrationReviewEventInput,
    *,
    seen_event_keys: frozenset[str] | set[str] | None = None,
) -> IllustrationApprovalState:
    """Validate an approval action and return its derived result state.

    Idempotency: a repeated ``event_key`` is rejected here; the durable layer
    enforces the unique event_key constraint so a duplicate action can never
    create a second approval.
    """
    seen = set(seen_event_keys or ())
    if event.event_key in seen:
        raise IllustrationGateError(
            f"duplicate review event_key {event.event_key!r} (idempotency)"
        )
    return approval_state_after(event.from_approval_state, event.action)


# ---------------------------------------------------------------------------
# Read envelopes (candidate-only, no canon exposure)
# ---------------------------------------------------------------------------


class IllustrationJobView(StrictIllustrationModel):
    """Read envelope: durable job with frozen lineage and explicit status."""

    id: int
    owner_id: int
    novel_id: int
    job_key: str
    idempotency_key: str
    status: IllustrationJobStatus
    status_reason: str | None = None
    error_code: str | None = None
    retry_count: int
    scene_spec_hash: str
    prompt_revision_id: int | None = None
    prompt_revision_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    config_hash: str
    price_snapshot: dict[str, Any] = Field(default_factory=dict)


class AssetRevisionView(StrictIllustrationModel):
    """Read envelope: candidate-only asset revision with full lineage."""

    id: int
    owner_id: int
    novel_id: int
    job_id: int
    revision_key: str
    revision_number: int
    asset_id: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    bytes_hash: str
    scene_spec_hash: str
    prompt_revision_id: int | None = None
    prompt_revision_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    provider: str
    provider_model: str
    provider_request_id: str | None = None
    rights_status: IllustrationRightsStatus
    approval_state: IllustrationApprovalState


class FrozenAssetRevisionView(StrictIllustrationModel):
    """Approved-only asset envelope for Phase 34 publish (never auto-created)."""

    id: int
    owner_id: int
    novel_id: int
    job_id: int
    revision_key: str
    revision_number: int
    asset_id: str
    storage_key: str
    mime_type: str
    width: int
    height: int
    size_bytes: int
    bytes_hash: str
    scene_spec_hash: str
    prompt_revision_hash: str
    visual_bible_revision_hash: str
    source_snapshot_id: str
    source_snapshot_hash: str
    cutoff_chapter: int
    provider: str
    provider_model: str
    provider_request_id: str | None = None
    rights_status: IllustrationRightsStatus
    approval_state: IllustrationApprovalState
    approved_by: str | None = None

    @model_validator(mode="after")
    def proposal_ready_only(self) -> "FrozenAssetRevisionView":
        if self.approval_state is not IllustrationApprovalState.PROPOSAL_READY:
            raise IllustrationGateError(
                "unapproved or unresolved asset revision cannot enter Phase 34 publish"
            )
        if self.rights_status is not IllustrationRightsStatus.CLEARED:
            raise IllustrationGateError(
                "asset rights must be cleared before proposal_ready can publish"
            )
        return self


# ---------------------------------------------------------------------------
# Wire request/response envelopes (moved from the API module)
# ---------------------------------------------------------------------------


class IllustrationJobListResponse(StrictIllustrationModel):
    items: list[IllustrationJobView]
    total: int


class IllustrationCreateJobResponse(StrictIllustrationModel):
    job: IllustrationJobView
    replayed: bool = False


class AssetListResponse(StrictIllustrationModel):
    items: list[AssetRevisionView]
    total: int


class ConsistencyEvaluateRequest(StrictIllustrationModel):
    """Candidate consistency evidence for one scene evaluation (D-33-04).

    The candidate's declared identity/style descriptors and the negative
    constraints it contains are compared against the frozen per-character
    fixture; scope comes from the path, never the body.
    """

    character_key: str = Field(min_length=1, max_length=60)
    scene_key: str = Field(min_length=1, max_length=60)
    report_key: str | None = Field(default=None, min_length=1, max_length=180)
    identity_attributes: list[str] = Field(default_factory=list, max_length=64)
    style_attributes: list[str] = Field(default_factory=list, max_length=64)
    negative_constraints_present: list[str] = Field(default_factory=list, max_length=64)


class IllustrationReviewActionRequest(StrictIllustrationModel):
    """One explicit approval action; scope comes from the path, never the body.

    Mirrors ``IllustrationReviewEventInput`` minus owner/novel/asset ids so the
    client can never widen scope. The server derives the result approval state
    from the legal transition map (D-33-03).
    """

    event_key: str = Field(min_length=1, max_length=160)
    action: IllustrationReviewAction
    actor_source: IllustrationActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_approval_state: IllustrationApprovalState
