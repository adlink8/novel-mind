"""Strict DTOs for the Phase 14 narrative-memory builder control plane."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal

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


class StageKind(StrEnum):
    CHAPTER_STATE = "chapter_state"
    ARC_VOLUME_PLAN = "arc_volume_plan"
    ARC_VOLUME_AGGREGATE = "arc_volume_aggregate"
    GLOBAL_AGGREGATE = "global_aggregate"
    MANIFEST_VALIDATION = "manifest_validation"


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
    # Kept only so old persisted runs and integration fixtures can resume;
    # new runs choose boundaries through the LLM arc-plan stage.
    arc_window_size: PositiveInt | None = None
    budget: BudgetPolicy
    prompt_hash: Hash64
    schema_hash: Hash64
    model_lineage: ModelLineage
    decoding_hash: Hash64
    config_hash: Hash64
    policy_hash: Hash64

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
    summary: Annotated[StrictStr, StringConstraints(max_length=2_000)] | None = None
    key_elements: tuple[dict[str, Any], ...] = ()
    narrative_progress: (
        Annotated[StrictStr, StringConstraints(max_length=2_000)] | None
    ) = None
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
