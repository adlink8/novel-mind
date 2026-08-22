"""Immutable typed contracts for the shared world-model event fact and causal edge.

Phase 27-01 / REQ-WM-01. Semantics locked by decisions D-01..D-04:

- D-01: ``Authority`` keeps canon_fact, probable_inference, literary_interpretation
  and user_interpretation as distinct labels. Nothing in this boundary can silently
  upgrade an inference or interpretation into ``canon_fact``; the gate rejects such
  attempts unless an explicit approval is present.
- D-02: Every durable projection is a versioned immutable candidate. There is no
  active-pointer / promotion / current-revision column and no promotion API.
- D-03: Every event and causal edge carries owner/novel/version/cutoff, source
  EvidenceRefs, effective interval, authority, confidence and gate status.
- D-04: Causality requires independent evidence and a gate. Co-occurrence or
  temporal adjacency alone is never causality; temporal conflicts are preserved
  and queryable instead of being overwritten.

Only validated immutable candidate projections (``WorldModelCandidateProjection``)
cross the persistence seam. Claims (``EventClaim`` / ``CausalEdgeClaim``) are gate
inputs and are never persisted by themselves.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Hash64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=180)
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]

WORLD_MODEL_SCHEMA_VERSION = "world-model-event.v1"
WORLD_MODEL_HASH_EVENT = f"{WORLD_MODEL_SCHEMA_VERSION}:event"
WORLD_MODEL_HASH_EDGE = f"{WORLD_MODEL_SCHEMA_VERSION}:edge"
WORLD_MODEL_HASH_CONFLICT = f"{WORLD_MODEL_SCHEMA_VERSION}:conflict"
WORLD_MODEL_HASH_PROJECTION = f"{WORLD_MODEL_SCHEMA_VERSION}:projection"
WORLD_MODEL_HASH_IDEM = f"{WORLD_MODEL_SCHEMA_VERSION}:idem"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Authority(StrEnum):
    """D-01 distinct epistemic authorities; never collapsed or auto-promoted."""

    CANON_FACT = "canon_fact"
    PROBABLE_INFERENCE = "probable_inference"
    LITERARY_INTERPRETATION = "literary_interpretation"
    USER_INTERPRETATION = "user_interpretation"


class CausalEdgeType(StrEnum):
    CAUSED = "caused"
    TRIGGERED = "triggered"
    RESPONDED = "responded"
    BLOCKED = "blocked"


class GateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    REJECTED = "rejected"


class ConflictKind(StrEnum):
    TEMPORAL_CONFLICT = "temporal_conflict"
    ASSERTION_CONFLICT = "assertion_conflict"


class EffectiveInterval(StrictModel):
    """Story-time interval (chapter-number units). Both ends are inclusive."""

    start: NonNegInt | None = None
    end: NonNegInt | None = None

    @model_validator(mode="after")
    def _ordered(self) -> "EffectiveInterval":
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("effective_end must be >= effective_start")
        return self


class EvidenceRef(StrictModel):
    """Exact frozen source evidence (D-03/D-04). Never a summary or chat text.

    ``source_snapshot_hash`` anchors the ref to a frozen source package; a ref
    that does not match the gate's expected snapshot is stale and fail-closed.
    """

    evidence_id: Key
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    source_start: NonNegInt
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64

    @model_validator(mode="after")
    def _half_open_range(self) -> "EvidenceRef":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(component: str, body: str) -> str:
    return hashlib.sha256(f"{component}\n{body}".encode("utf-8")).hexdigest()


def event_checksum(event: "EventFact") -> str:
    return _sha256(
        WORLD_MODEL_HASH_EVENT, _canonical_json(event.model_dump(mode="json"))
    )


def edge_checksum(edge: "CausalEdge") -> str:
    return _sha256(WORLD_MODEL_HASH_EDGE, _canonical_json(edge.model_dump(mode="json")))


def conflict_checksum(conflict: "WorldModelConflict") -> str:
    return _sha256(
        WORLD_MODEL_HASH_CONFLICT, _canonical_json(conflict.model_dump(mode="json"))
    )


def projection_checksum(projection: "WorldModelCandidateProjection") -> str:
    body = _canonical_json(
        {
            "owner_id": projection.owner_id,
            "novel_id": projection.novel_id,
            "version_id": projection.version_id,
            "events": [event.model_dump(mode="json") for event in projection.events],
            "edges": [edge.model_dump(mode="json") for edge in projection.edges],
            "conflicts": [
                conflict.model_dump(mode="json") for conflict in projection.conflicts
            ],
        }
    )
    return _sha256(WORLD_MODEL_HASH_PROJECTION, body)


def row_idempotency_key(component: str, payload: dict[str, Any]) -> str:
    """Deterministic replay key over one row's canonical payload."""
    return _sha256(WORLD_MODEL_HASH_IDEM, _canonical_json(payload))


class EventFact(StrictModel):
    """Immutable event fact candidate (D-03). Owns its full lineage in-place."""

    event_key: Key
    title: Key
    description: Description
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    effective: EffectiveInterval
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    gate_status: GateStatus
    gate_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @property
    def checksum(self) -> str:
        return event_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(WORLD_MODEL_HASH_EVENT, self.model_dump(mode="json"))


class CausalEdge(StrictModel):
    """Immutable evidence-gated causal edge candidate (D-04)."""

    edge_key: Key
    source_event_key: Key
    target_event_key: Key
    edge_type: CausalEdgeType
    authority: Authority
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    disclosure_cutoff: PositiveInt
    source_refs: Annotated[tuple[EvidenceRef, ...], Field(min_length=1)]
    gate_status: GateStatus
    gate_reason: Annotated[str, StringConstraints(max_length=120)] | None = None
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> "CausalEdge":
        if self.source_event_key == self.target_event_key:
            raise ValueError("causal edge endpoints must differ")
        return self

    @field_validator("source_refs")
    @classmethod
    def _unique_evidence(
        cls, value: tuple[EvidenceRef, ...]
    ) -> tuple[EvidenceRef, ...]:
        ids = [ref.evidence_id for ref in value]
        if len(ids) != len(set(ids)):
            raise ValueError("source_refs must be unique")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @property
    def checksum(self) -> str:
        return edge_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(WORLD_MODEL_HASH_EDGE, self.model_dump(mode="json"))


class WorldModelConflict(StrictModel):
    """A preserved conflict between durable rows; never resolved by overwrite."""

    conflict_key: Key
    kind: ConflictKind
    involved_keys: Annotated[tuple[Key, ...], Field(min_length=1)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=400)]

    @property
    def checksum(self) -> str:
        return conflict_checksum(self)

    @property
    def idempotency_key(self) -> str:
        return row_idempotency_key(
            WORLD_MODEL_HASH_CONFLICT, self.model_dump(mode="json")
        )


class WorldModelCandidateProjection(StrictModel):
    """The only output that may be persisted (D-02: immutable candidates only).

    No active-pointer, promotion or cutover field exists. ``projection_hash``
    is recomputed on every read so byte-drifted rows fail closed.
    """

    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    schema_version: str = WORLD_MODEL_SCHEMA_VERSION
    events: Annotated[tuple[EventFact, ...], Field(min_length=1)]
    edges: tuple[CausalEdge, ...] = ()
    conflicts: tuple[WorldModelConflict, ...] = ()
    projection_hash: Hash64 = "0" * 64

    @model_validator(mode="after")
    def _scope_matches_rows(self) -> "WorldModelCandidateProjection":
        for event in self.events:
            if (
                event.owner_id != self.owner_id
                or event.novel_id != self.novel_id
                or event.version_id != self.version_id
            ):
                raise ValueError("event scope must match the projection scope")
        for edge in self.edges:
            if (
                edge.owner_id != self.owner_id
                or edge.novel_id != self.novel_id
                or edge.version_id != self.version_id
            ):
                raise ValueError("edge scope must match the projection scope")
        event_keys = {event.event_key for event in self.events}
        for edge in self.edges:
            if edge.source_event_key not in event_keys:
                raise ValueError("edge source must be a projection-local event")
            if edge.target_event_key not in event_keys:
                raise ValueError("edge target must be a projection-local event")
        return self

    @property
    def idempotency_key(self) -> str:
        body = _canonical_json(
            {
                "owner_id": self.owner_id,
                "novel_id": self.novel_id,
                "version_id": self.version_id,
                "events": [event.model_dump(mode="json") for event in self.events],
                "edges": [edge.model_dump(mode="json") for edge in self.edges],
                "conflicts": [
                    conflict.model_dump(mode="json") for conflict in self.conflicts
                ],
            }
        )
        return _sha256(WORLD_MODEL_HASH_IDEM, body)


def build_projection(
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    events: list[EventFact],
    edges: list[CausalEdge],
    conflicts: list[WorldModelConflict],
) -> WorldModelCandidateProjection:
    """Construct the immutable candidate with a sealed projection hash."""
    projection = WorldModelCandidateProjection(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        events=tuple(events),
        edges=tuple(edges),
        conflicts=tuple(conflicts),
        projection_hash="0" * 64,
    )
    checksum = projection_checksum(projection)
    return projection.model_copy(update={"projection_hash": checksum})


def projection_verified(projection: WorldModelCandidateProjection) -> bool:
    """Recompute and compare the sealed projection hash (byte-equivalence)."""
    return projection_checksum(projection) == projection.projection_hash
