"""Strict DTOs for the Phase 14 narrative-memory builder control plane."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.services.narrative_memory.contracts import (
    CandidatePackage,
    Hash64,
    Key,
    ModelLineage,
    PositiveInt,
    VersionLabel,
)


class BuilderFrozenModel(BaseModel):
    """Frozen + forbid-extra, but not strict so JSON list/enum inputs validate."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


FORBIDDEN_PACKAGE_KEYS = frozenset(
    {
        "reader_chat",
        "conversation_id",
        "message_id",
        "chat_text",
        "citation",
        "citations",
        "similarity",
        "similarity_score",
        "active_pointer",
        "promote",
        "promotion",
        "current_version",
        "default_version",
    }
)


class SourceStatus(StrEnum):
    NON_EMPTY = "non_empty"
    HEALTHY_EMPTY = "healthy_empty"
    UNAVAILABLE = "unavailable"
    LINEAGE_MISMATCH = "lineage_mismatch"


class TerminalState(StrEnum):
    """Explicit durable terminal state for a builder stage (D-02/D-04).

    No stage may silently sit in `pending`/`running` forever. Every stage
    converges to exactly one terminal state, or carries a recoverable
    checkpoint that the recovery coordinator can resume.
    """

    COMPLETED = "completed"
    ISOLATED = "isolated"
    BLOCKED = "blocked"


class FailureClass(StrEnum):
    """Coarse failure families used to route recovery behaviour."""

    CANCELLED = "cancelled"
    BUDGET = "budget"
    PROVIDER = "provider"
    SCHEMA = "schema"
    SOURCE_DRIFT = "source_drift"
    OWNER_MISMATCH = "owner_mismatch"
    STALE_CACHE = "stale_cache"
    INTERNAL = "internal"


class ReasonCode(StrEnum):
    """Stable, replayable reason-code taxonomy for terminal states.

    Reason codes are versioned contract strings. They are stored verbatim on
    the stage row and inside the immutable checkpoint journal so that a later
    resume or audit can reproduce *why* a stage stopped without re-running it.
    """

    COMPLETED_CANDIDATE = "completed_candidate"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED_BEFORE_PERSIST = "cancelled_before_persist"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN_PRICING = "unknown_pricing"
    PROVIDER_TRANSPORT_ERROR = "provider_transport_error"
    SCHEMA_INVALID = "schema_invalid"
    SOURCE_DRIFT = "source_snapshot_drift"
    STALE_CACHE = "stale_cache_rejected"
    OWNER_MISMATCH = "owner_mismatch"
    DEPENDENCY_FAILED = "dependency_failed"
    PARENT_INCOMPLETE = "parent_incomplete"
    NO_HIERARCHY_CHAPTERS = "no_hierarchy_chapters"
    INTERNAL_ERROR = "internal_error"


class StageKind(StrEnum):
    CHAPTER_STATE = "chapter_state"
    ARC_VOLUME_PLAN = "arc_volume_plan"
    ARC_VOLUME_AGGREGATE = "arc_volume_aggregate"
    GLOBAL_AGGREGATE = "global_aggregate"
    MANIFEST_VALIDATION = "manifest_validation"


# Bounded context/continuity contract for ChapterAnalysisArtifact (D-08).
# These are hard caps that keep the candidate context payload finite; they are
# also the `max_length` lineage values bound onto the artifact itself.
CONTEXT_SUMMARY_MAX_LENGTH = 2000
NEXT_HINT_MAX_LENGTH = 1000
CONTINUITY_NOTES_MAX_LENGTH = 1200
SPOILER_POLICY_VERSION_DEFAULT = "spoiler-policy.v1"
NEXT_HINT_BLOCKED_REASON = "hint_unsafe_future_spoiler"
# Namespaced digest prefixes. Digests are compressed payloads only; the
# namespace keeps them disjoint from authoritative content hashes so they can
# never be mistaken for an EvidenceRef or a retrieval-index input.
CHAPTER_DIGEST_NAMESPACE = "narrative-memory.chapter-digest.v1"
CHUNK_DIGEST_NAMESPACE = "narrative-memory.chunk-digest.v1"


class BuildOutcome(StrEnum):
    COMPLETED_CANDIDATE = "completed_candidate"
    PARTIAL = "partial"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BudgetPolicy(BuilderFrozenModel):
    max_calls: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_cost_usd: Annotated[StrictStr, StringConstraints(pattern=r"^\d+(\.\d+)?$")]

    def as_decimals(self) -> tuple[int, int, int, Decimal]:
        return (
            self.max_calls,
            self.max_input_tokens,
            self.max_output_tokens,
            Decimal(self.max_cost_usd),
        )


class RunPolicy(BuilderFrozenModel):
    """Frozen execution policy stored on the run row."""

    policy_version: VersionLabel
    stage_order: tuple[StageKind, ...]
    max_schema_repairs: Annotated[StrictInt, Field(ge=0, le=1)] = 1
    chapter_concurrency: PositiveInt = 1
    arc_window_size: PositiveInt = 3
    budget: BudgetPolicy
    prompt_hash: Hash64
    schema_hash: Hash64
    model_lineage: ModelLineage
    decoding_hash: Hash64
    config_hash: Hash64
    policy_hash: Hash64
    # Lineage bound onto every ChapterAnalysisArtifact context field (D-08):
    # the spoiler policy decides what the next-context hint may disclose.
    spoiler_policy_version: VersionLabel = SPOILER_POLICY_VERSION_DEFAULT

    @model_validator(mode="after")
    def validate_order(self) -> "RunPolicy":
        if StageKind.CHAPTER_STATE not in self.stage_order:
            raise ValueError("stage_order must include chapter_state")
        return self


class OptionalSourceSignal(BuilderFrozenModel):
    source_kind: Literal["timeline", "relationship", "clue"]
    status: SourceStatus
    reason_code: VersionLabel | None = None
    signal_keys: tuple[Key, ...] = ()
    lineage: dict[str, StrictStr | StrictInt | None] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_keys(cls, value: object) -> object:
        if isinstance(value, dict):
            bad = FORBIDDEN_PACKAGE_KEYS.intersection(value)
            if bad:
                raise ValueError(f"forbidden package keys: {sorted(bad)}")
        return value


class EvidenceLeafRef(BuilderFrozenModel):
    hierarchy_build_id: Key
    evidence_node_id: Key
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    source_start: Annotated[StrictInt, Field(ge=0)]
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64

    @model_validator(mode="after")
    def validate_span(self) -> "EvidenceLeafRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class ChapterStateInputPackage(BuilderFrozenModel):
    """Strict provider input for one Chapter State stage."""

    stage_key: Key
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    hierarchy_build_id: Key
    source_snapshot_hash: Hash64
    hierarchy_checksum: Hash64
    eligibility_report_checksum: Hash64
    evidence_leaves: Annotated[tuple[EvidenceLeafRef, ...], Field(min_length=1)]
    optional_signals: tuple[OptionalSourceSignal, ...] = ()
    prompt_hash: Hash64
    schema_hash: Hash64
    model_lineage: ModelLineage
    decoding_hash: Hash64
    config_hash: Hash64
    policy_hash: Hash64

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_and_caller_hashes(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        bad = FORBIDDEN_PACKAGE_KEYS.intersection(value)
        if bad:
            raise ValueError(f"forbidden package keys: {sorted(bad)}")
        for banned in (
            "caller_evidence_hash",
            "summary_text",
            "chat_message",
            "conversation",
        ):
            if banned in value:
                raise ValueError(f"forbidden field: {banned}")
        return value

    @model_validator(mode="after")
    def validate_leaves(self) -> "ChapterStateInputPackage":
        for leaf in self.evidence_leaves:
            if leaf.chapter_number != self.chapter_number:
                raise ValueError("evidence leaf chapter must match package chapter")
            if leaf.hierarchy_build_id != self.hierarchy_build_id:
                raise ValueError("evidence leaf build must match frozen hierarchy")
            if leaf.source_snapshot_hash != self.source_snapshot_hash:
                raise ValueError("evidence leaf snapshot must match frozen snapshot")
        return self


class ChapterStateModelOutput(BuilderFrozenModel):
    """Strict model output before script rebinding into CandidatePackage."""

    node_key: Key
    display_label: Annotated[StrictStr, StringConstraints(max_length=240)] | None = None
    claims: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    source_bindings: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden(cls, value: object) -> object:
        if isinstance(value, dict):
            bad = FORBIDDEN_PACKAGE_KEYS.intersection(value)
            if bad:
                raise ValueError(f"forbidden output keys: {sorted(bad)}")
        return value


class StageCheckpoint(BuilderFrozenModel):
    stage_key: Key
    status: VersionLabel
    package_checksum: Hash64 | None = None
    cache_key: VersionLabel | None = None
    artifact_checksum: Hash64 | None = None
    reason_code: VersionLabel | None = None


class StageLineage(BuilderFrozenModel):
    """Exact lineage that gates cache reuse (D-04: checksum-identical only)."""

    model_lineage: ModelLineage
    prompt_hash: Hash64
    schema_hash: Hash64
    decoding_hash: Hash64
    config_hash: Hash64
    policy_hash: Hash64


class ChapterAnalysisArtifact(BuilderFrozenModel):
    """Immutable candidate chapter context/continuity artifact (D-08).

    ``chapter_digest`` and ``chunk_digests`` are compressed payloads only —
    namespaced hashes used for context compaction. They are never
    retrieval-index inputs and never ``EvidenceRef`` authority. The free-text
    fields are bounded by ``max_length`` and the spoiler policy; ``next_context_hint``
    must never disclose facts beyond ``cutoff``, otherwise it is omitted and
    ``next_hint_reason_code`` records the stable block reason.
    """

    schema_version: VersionLabel
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    # Source/input binding: the frozen snapshot and the exact input package that
    # produced this artifact.
    source_snapshot_hash: Hash64
    input_hash: Hash64
    # Context boundary: this analysis is valid only through `cutoff`.
    cutoff: PositiveInt
    max_length: PositiveInt
    spoiler_policy_version: VersionLabel
    # Compressed payload digests (never indexed, never EvidenceRefs).
    chapter_digest: Hash64
    chunk_digests: Annotated[tuple[Hash64, ...], Field(max_length=512)]
    previous_context_summary: (
        Annotated[StrictStr, StringConstraints(max_length=CONTEXT_SUMMARY_MAX_LENGTH)]
        | None
    ) = None
    next_context_hint: (
        Annotated[StrictStr, StringConstraints(max_length=NEXT_HINT_MAX_LENGTH)]
        | None
    ) = None
    next_hint_reason_code: VersionLabel | None = None
    continuity_notes: (
        Annotated[StrictStr, StringConstraints(max_length=CONTINUITY_NOTES_MAX_LENGTH)]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_hint_reason(self) -> "ChapterAnalysisArtifact":
        if self.next_context_hint is None and self.next_hint_reason_code is None:
            raise ValueError(
                "next_context_hint omitted without a stable reason code"
            )
        if (
            self.next_hint_reason_code is not None
            and self.next_context_hint is not None
        ):
            raise ValueError(
                "next_context_hint cannot carry both a hint and a block reason"
            )
        if len(self.chunk_digests) != len(set(self.chunk_digests)):
            raise ValueError("chunk_digests must be unique")
        return self


def chapter_digest_for(value: str | dict[str, Any]) -> str:
    """Namespaced compressed-payload digest for a chapter analysis context."""
    canonical = value if isinstance(value, str) else _stable_json(value)
    return sha256(
        f"{CHAPTER_DIGEST_NAMESPACE}\n{canonical}".encode("utf-8")
    ).hexdigest()


def chunk_digest_for(chunk_repr: str | dict[str, Any]) -> str:
    """Namespaced compressed-payload digest for one context chunk."""
    canonical = chunk_repr if isinstance(chunk_repr, str) else _stable_json(chunk_repr)
    return sha256(
        f"{CHUNK_DIGEST_NAMESPACE}\n{canonical}".encode("utf-8")
    ).hexdigest()


def hint_safe_at_cutoff(hint: str, *, cutoff: int) -> bool:
    """A next-context hint is safe only within the current cutoff.

    Any reference to a chapter strictly beyond ``cutoff`` would leak future
    facts, so such hints are rejected. Unverifiable hints fail closed.
    """
    if not hint:
        return True
    for match in re.finditer(r"(?:chapter[:\s]+|ch\.)?(\d{1,5})", hint, re.IGNORECASE):
        try:
            number = int(match.group(1))
        except ValueError:
            continue
        # Disambiguation-only references to chapters <= cutoff are allowed.
        if number > cutoff:
            return False
    return True


def build_chapter_analysis_artifact(
    *,
    chapter_id: int,
    chapter_number: int,
    source_snapshot_hash: str,
    input_hash: str,
    spoiler_policy_version: str,
    max_length: int,
    context_payload: str | dict[str, Any],
    chunk_reprs: Sequence[str | dict[str, Any]],
    previous_context_summary: str | None = None,
    next_context_hint: str | None = None,
    continuity_notes: str | None = None,
) -> ChapterAnalysisArtifact:
    """Build the bounded candidate artifact with spoiler-safe next hint.

    If ``next_context_hint`` cannot be proven safe at the chapter cutoff, it is
    omitted and the stable ``hint_unsafe_future_spoiler`` reason is recorded.
    """
    if next_context_hint and not hint_safe_at_cutoff(
        next_context_hint, cutoff=chapter_number
    ):
        next_context_hint = None
        next_hint_reason_code = NEXT_HINT_BLOCKED_REASON
    else:
        next_hint_reason_code = None
    return ChapterAnalysisArtifact(
        schema_version="chapter-analysis-artifact.v1",
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_snapshot_hash=source_snapshot_hash,
        input_hash=input_hash,
        cutoff=chapter_number,
        max_length=max_length,
        spoiler_policy_version=spoiler_policy_version,
        chapter_digest=chapter_digest_for(context_payload),
        chunk_digests=tuple(chunk_digest_for(chunk) for chunk in chunk_reprs),
        previous_context_summary=previous_context_summary,
        next_context_hint=next_context_hint,
        next_hint_reason_code=next_hint_reason_code,
        continuity_notes=continuity_notes,
    )


def assert_digests_never_evidence_refs(
    digests: Sequence[str],
    *,
    authority_content_hashes: Sequence[str],
    retrieval_index_inputs: Sequence[str] = (),
) -> None:
    """Fail closed if any digest doubles as evidence/retrieval authority.

    Digests are compressed payloads only (D-08). If one collides with an
    authoritative source content hash or a retrieval-index input, the artifact
    is invalid and must be rejected.
    """
    authority = set(authority_content_hashes)
    indexed = set(retrieval_index_inputs)
    for digest in digests:
        if digest in authority:
            raise ValueError("chapter digest cannot double as an EvidenceRef hash")
        if digest in indexed:
            raise ValueError("chapter digest cannot enter the retrieval index")


class ResumePlanItem(BuilderFrozenModel):
    """One stage's decision in the idempotent resume plan."""

    stage_key: Key
    status: VersionLabel
    terminal_state: TerminalState | None = None
    reason_code: ReasonCode | None = None
    attempt_count: int = 0
    runnable: StrictBool = False
    blocked_by: tuple[Key, ...] = ()


class ResumePlan(BuilderFrozenModel):
    """Deterministic resume view over a run's stages (D-03/D-04).

    `runnable` stages are exactly the ones that still need work; terminal
    stages (completed/isolated/blocked) are never re-run. The plan is derived
    from durable rows only, so crash recovery rehydrates from the DB.
    """

    run_id: PositiveInt
    runnable: tuple[ResumePlanItem, ...]
    terminal: tuple[ResumePlanItem, ...]
    has_silent_pending: StrictBool
    silent_pending_keys: tuple[Key, ...] = ()


def classify_failure(
    exc: BaseException,
    *,
    attempt_count: int | None = None,
) -> tuple[ReasonCode, FailureClass]:
    """Map any exception to a stable reason code + failure class.

    This is the single classification seam. It never raises and never returns
    an unstable string, so failure handling is replayable across restarts.
    """
    from app.services.narrative_memory.builder_budget import (
        BudgetExceeded,
        UnknownPricing,
    )
    from app.services.narrative_memory.builder_gateway import (
        CancelledBeforePersist,
        GatewayError,
    )
    from app.services.narrative_memory.builder_packages import PackageBuildError
    from app.services.narrative_memory.builder_repository import (
        BuilderRepositoryError,
    )

    name = type(exc).__name__
    if isinstance(exc, CancelledBeforePersist):
        return ReasonCode.CANCELLED_BEFORE_PERSIST, FailureClass.CANCELLED
    if isinstance(exc, UnknownPricing):
        return ReasonCode.UNKNOWN_PRICING, FailureClass.BUDGET
    if isinstance(exc, BudgetExceeded):
        return ReasonCode.BUDGET_EXCEEDED, FailureClass.BUDGET
    if isinstance(exc, PackageBuildError):
        return ReasonCode.SCHEMA_INVALID, FailureClass.SCHEMA
    if isinstance(exc, GatewayError):
        return ReasonCode.PROVIDER_TRANSPORT_ERROR, FailureClass.PROVIDER
    if isinstance(exc, BuilderRepositoryError):
        text = str(exc)
        if "eligibility" in text and "mismatch" in text:
            return ReasonCode.SOURCE_DRIFT, FailureClass.SOURCE_DRIFT
        if "chapter id" in text or "hierarchy" in text:
            return ReasonCode.NO_HIERARCHY_CHAPTERS, FailureClass.SOURCE_DRIFT
        return ReasonCode.INTERNAL_ERROR, FailureClass.INTERNAL
    if "owner" in name.lower() or "owner" in str(exc).lower():
        return ReasonCode.OWNER_MISMATCH, FailureClass.OWNER_MISMATCH
    if name in {"ValueError", "TypeError", "AssertionError"}:
        return ReasonCode.SCHEMA_INVALID, FailureClass.SCHEMA
    return ReasonCode.INTERNAL_ERROR, FailureClass.INTERNAL


class CallAuditRecord(BuilderFrozenModel):
    stage_key: Key
    attempt_number: PositiveInt
    status: VersionLabel
    request_hash: Hash64
    response_hash: Hash64 | None = None
    cache_key: VersionLabel | None = None
    cost_usd: (
        Annotated[StrictStr, StringConstraints(pattern=r"^\d+(\.\d+)?$")] | None
    ) = None
    input_tokens: Annotated[StrictInt, Field(ge=0)] = 0
    output_tokens: Annotated[StrictInt, Field(ge=0)] = 0
    error_code: VersionLabel | None = None


class ModelDeploymentSnapshot(BuilderFrozenModel):
    provider: VersionLabel
    model: VersionLabel
    deployment: VersionLabel
    revision: VersionLabel
    supports_structured_output: StrictBool
    input_price_per_million: (
        Annotated[StrictStr, StringConstraints(pattern=r"^\d+(\.\d+)?$")] | None
    )
    output_price_per_million: (
        Annotated[StrictStr, StringConstraints(pattern=r"^\d+(\.\d+)?$")] | None
    )

    @property
    def lineage(self) -> ModelLineage:
        return ModelLineage(
            provider=self.provider,
            model=self.model,
            deployment=self.deployment,
            revision=self.revision,
        )

    def prices(self) -> tuple[Decimal | None, Decimal | None]:
        inp = (
            Decimal(self.input_price_per_million)
            if self.input_price_per_million is not None
            else None
        )
        out = (
            Decimal(self.output_price_per_million)
            if self.output_price_per_million is not None
            else None
        )
        return inp, out


def package_checksum(package: BaseModel) -> str:
    payload = package.model_dump(mode="json")
    return sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def exact_cache_key(
    *,
    stage_key: str,
    source_snapshot_hash: str,
    hierarchy_checksum: str,
    package_checksum_value: str,
    prompt_hash: str,
    schema_hash: str,
    model_lineage: ModelLineage,
    decoding_hash: str,
    config_hash: str,
    policy_hash: str,
    optional_source_lineage: dict[str, Any] | None = None,
) -> str:
    body = {
        "stage_key": stage_key,
        "source_snapshot_hash": source_snapshot_hash,
        "hierarchy_checksum": hierarchy_checksum,
        "package_checksum": package_checksum_value,
        "prompt_hash": prompt_hash,
        "schema_hash": schema_hash,
        "model_lineage": model_lineage.model_dump(mode="json"),
        "decoding_hash": decoding_hash,
        "config_hash": config_hash,
        "policy_hash": policy_hash,
        "optional_source_lineage": optional_source_lineage or {},
    }
    digest = sha256(_stable_json(body).encode("utf-8")).hexdigest()
    return f"nmb:{digest[:120]}"


def assert_no_forbidden_keys(payload: object, *, path: str = "$") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in FORBIDDEN_PACKAGE_KEYS:
                raise ValueError(f"forbidden key {key} at {path}")
            assert_no_forbidden_keys(value, path=f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            assert_no_forbidden_keys(value, path=f"{path}[{index}]")


def dump_canonical(payload: object) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return _stable_json(payload)


def load_candidate_package(payload: object) -> CandidatePackage:
    if isinstance(payload, CandidatePackage):
        return payload
    if isinstance(payload, str):
        return CandidatePackage.model_validate_json(payload)
    return CandidatePackage.model_validate(payload)
