"""Strict candidate contracts for hierarchical narrative memory.

Only validated DTOs from this module may cross the Phase 13 persistence seam.
Free text is presentation metadata; authoritative state is represented by the
closed discriminated unions below.
"""

from __future__ import annotations

import json
from hashlib import sha256
from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)


Key = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=180),
]
Hash64 = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
VersionLabel = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
PositiveInt = Annotated[StrictInt, Field(gt=0)]


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class NodeKind(StrEnum):
    CHAPTER_STATE = "chapter_state"
    STORY_ARC = "story_arc"
    VOLUME = "volume"
    GLOBAL_STORY = "global_story"


class EdgeType(StrEnum):
    CONTAINS = "contains"
    DERIVES_FROM = "derives_from"


class SourceKind(StrEnum):
    HIERARCHY = "hierarchy"
    TIMELINE = "timeline"
    RELATIONSHIP = "relationship"
    CLUE = "clue"


class Uncertainty(StrEnum):
    CERTAIN = "certain"
    LIKELY = "likely"
    UNCERTAIN = "uncertain"
    UNKNOWN = "unknown"


class EntityKind(StrEnum):
    CHARACTER = "character"
    LOCATION = "location"
    OBJECT = "object"
    FACTION = "faction"
    WORLD = "world"


class EntityStateDimension(StrEnum):
    LOCATION = "location"
    CONDITION = "condition"
    GOAL = "goal"
    KNOWLEDGE = "knowledge"
    POSSESSION = "possession"
    ROLE = "role"


class StateChange(StrEnum):
    ESTABLISH = "establish"
    CHANGE = "change"
    RESOLVE = "resolve"
    REMOVE = "remove"
    UNCHANGED = "unchanged"


class EventKind(StrEnum):
    ACTION = "action"
    DECISION = "decision"
    DISCOVERY = "discovery"
    CONFLICT = "conflict"
    RESOLUTION = "resolution"
    TRANSITION = "transition"


class RelationshipKind(StrEnum):
    ALLY = "ally"
    ENEMY = "enemy"
    FAMILY = "family"
    MENTOR = "mentor"
    ROMANTIC = "romantic"


class RelationshipState(StrEnum):
    UNKNOWN = "unknown"
    ALLY = "ally"
    ENEMY = "enemy"
    FAMILY = "family"
    MENTOR = "mentor"
    ROMANTIC = "romantic"
    ENDED = "ended"


class RelationshipChange(StrEnum):
    ESTABLISH = "establish"
    CHANGE = "change"
    END = "end"


class ClueState(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    REINFORCED = "reinforced"
    PAID_OFF = "paid_off"
    DISMISSED = "dismissed"


class ClueChange(StrEnum):
    SEED = "seed"
    ACTIVATE = "activate"
    REINFORCE = "reinforce"
    PAY_OFF = "pay_off"
    DISMISS = "dismiss"


class WorldStateDimension(StrEnum):
    POLITICAL_ORDER = "political_order"
    SOCIAL_ORDER = "social_order"
    ENVIRONMENT = "environment"
    RULE = "rule"
    RESOURCE = "resource"


class OpenLoopState(StrEnum):
    OPEN = "open"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class OpenLoopChange(StrEnum):
    OPEN = "open"
    BLOCK = "block"
    RESOLVE = "resolve"
    ABANDON = "abandon"
    REOPEN = "reopen"


class TextValue(StrictFrozenModel):
    value_kind: Literal["text"]
    value: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]


class NumberValue(StrictFrozenModel):
    value_kind: Literal["number"]
    value: StrictFloat


class BooleanValue(StrictFrozenModel):
    value_kind: Literal["boolean"]
    value: StrictBool


class ReferenceValue(StrictFrozenModel):
    value_kind: Literal["reference"]
    value: Key


class UnknownValue(StrictFrozenModel):
    value_kind: Literal["unknown"]


TypedValue: TypeAlias = Annotated[
    TextValue | NumberValue | BooleanValue | ReferenceValue | UnknownValue,
    Field(discriminator="value_kind"),
]


class ModelLineage(StrictFrozenModel):
    provider: VersionLabel
    model: VersionLabel
    deployment: VersionLabel
    revision: VersionLabel


class CandidateVersionSpec(StrictFrozenModel):
    """Caller-selected candidate identity and generation contract.

    Source/hierarchy/eligibility lineage is intentionally absent: authority.py
    derives it from the verified Phase 12 report and current PostgreSQL rows.
    """

    version_key: Key
    parent_version_id: PositiveInt | None = None
    prompt_hash: Hash64
    schema_hash: Hash64
    model_lineage: ModelLineage
    decoding_hash: Hash64
    config_hash: Hash64
    policy_hash: Hash64


class MemoryNodeBase(StrictFrozenModel):
    node_key: Key
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    schema_version: VersionLabel
    display_label: Annotated[StrictStr, StringConstraints(max_length=240)] | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "MemoryNodeBase":
        if self.chapter_end < self.chapter_start:
            raise ValueError("chapter_end must be >= chapter_start")
        return self


class ChapterStateNode(MemoryNodeBase):
    node_kind: Literal[NodeKind.CHAPTER_STATE]

    @model_validator(mode="after")
    def validate_singleton(self) -> "ChapterStateNode":
        if self.chapter_start != self.chapter_end:
            raise ValueError("chapter_state must cover exactly one chapter")
        return self


class StoryArcNode(MemoryNodeBase):
    node_kind: Literal[NodeKind.STORY_ARC]


class VolumeNode(MemoryNodeBase):
    node_kind: Literal[NodeKind.VOLUME]


class GlobalStoryNode(MemoryNodeBase):
    node_kind: Literal[NodeKind.GLOBAL_STORY]


MemoryNode: TypeAlias = Annotated[
    ChapterStateNode | StoryArcNode | VolumeNode | GlobalStoryNode,
    Field(discriminator="node_kind"),
]
MEMORY_NODE_ADAPTER = TypeAdapter(MemoryNode)


def parse_memory_node(value: object) -> MemoryNode:
    """Strictly parse Python or JSON-compatible node input without coercion."""

    if isinstance(value, MemoryNodeBase):
        return value
    return MEMORY_NODE_ADAPTER.validate_json(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")), strict=True
    )


class EntityStateClaim(StrictFrozenModel):
    claim_kind: Literal["entity_state"]
    entity_kind: EntityKind
    entity_key: Key
    dimension: EntityStateDimension
    prior: TypedValue
    current: TypedValue
    change: StateChange


class EventFactClaim(StrictFrozenModel):
    claim_kind: Literal["event_fact"]
    event_kind: EventKind
    actor_keys: Annotated[tuple[Key, ...], Field(min_length=1)]
    object_keys: tuple[Key, ...] = ()
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    outcome: TypedValue

    @field_validator("actor_keys", "object_keys")
    @classmethod
    def unique_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("entity keys must be unique")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "EventFactClaim":
        if self.chapter_end < self.chapter_start:
            raise ValueError("chapter_end must be >= chapter_start")
        return self


class RelationshipDeltaClaim(StrictFrozenModel):
    claim_kind: Literal["relationship_delta"]
    source_entity_key: Key
    target_entity_key: Key
    relationship_kind: RelationshipKind
    prior: RelationshipState
    current: RelationshipState
    change: RelationshipChange

    @model_validator(mode="after")
    def validate_endpoints_and_state(self) -> "RelationshipDeltaClaim":
        if self.source_entity_key == self.target_entity_key:
            raise ValueError("relationship endpoints must differ")
        if (
            self.change != RelationshipChange.END
            and self.current.value != self.relationship_kind.value
        ):
            raise ValueError("current relationship state must match relationship_kind")
        if (
            self.change == RelationshipChange.END
            and self.current != RelationshipState.ENDED
        ):
            raise ValueError("end transition requires current=ended")
        return self


class ClueDeltaClaim(StrictFrozenModel):
    claim_kind: Literal["clue_delta"]
    clue_key: Key
    prior: ClueState
    current: ClueState
    change: ClueChange


class WorldStateDeltaClaim(StrictFrozenModel):
    claim_kind: Literal["world_state_delta"]
    subject_key: Key
    dimension: WorldStateDimension
    prior: TypedValue
    current: TypedValue
    change: StateChange


class OpenLoopDeltaClaim(StrictFrozenModel):
    claim_kind: Literal["open_loop_delta"]
    loop_key: Key
    prior: OpenLoopState
    current: OpenLoopState
    change: OpenLoopChange


ClaimPayload: TypeAlias = Annotated[
    EntityStateClaim
    | EventFactClaim
    | RelationshipDeltaClaim
    | ClueDeltaClaim
    | WorldStateDeltaClaim
    | OpenLoopDeltaClaim,
    Field(discriminator="claim_kind"),
]


class MemoryClaim(StrictFrozenModel):
    claim_key: Key
    node_key: Key
    payload: ClaimPayload
    uncertainty: Uncertainty
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    visible_from_chapter: PositiveInt
    source_keys: Annotated[tuple[Key, ...], Field(min_length=1)]
    non_authoritative_statement: (
        Annotated[StrictStr, StringConstraints(max_length=1000)] | None
    ) = None

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_confidence_coercion(cls, value: object) -> object:
        if type(value) is not float:
            raise ValueError("confidence must be a JSON number with fractional type")
        return value

    @field_validator("source_keys")
    @classmethod
    def unique_source_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("source_keys must be unique")
        return value

    @property
    def claim_kind(self) -> str:
        return self.payload.claim_kind


class MemoryEdge(StrictFrozenModel):
    edge_type: EdgeType
    source_node_key: Key
    target_node_key: Key

    @model_validator(mode="after")
    def validate_distinct_endpoints(self) -> "MemoryEdge":
        if self.source_node_key == self.target_node_key:
            raise ValueError("edge endpoints must differ")
        return self


class ExactSourceLink(StrictFrozenModel):
    source_key: Key
    claim_key: Key
    source_kind: SourceKind
    hierarchy_build_id: Key
    evidence_node_id: Key
    chapter_id: PositiveInt
    chapter_number: PositiveInt
    source_start: Annotated[StrictInt, Field(ge=0)]
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64
    optional_domain_source_key: Key | None = None

    @model_validator(mode="after")
    def validate_exact_reference(self) -> "ExactSourceLink":
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        if self.source_kind == SourceKind.HIERARCHY and self.optional_domain_source_key:
            raise ValueError("hierarchy source cannot carry an optional domain key")
        if (
            self.source_kind != SourceKind.HIERARCHY
            and not self.optional_domain_source_key
        ):
            raise ValueError("optional domain source requires its exact source key")
        return self


class CandidatePackage(StrictFrozenModel):
    """One closed, package-local set of candidate content rows."""

    nodes: Annotated[tuple[MemoryNode, ...], Field(min_length=1)]
    claims: Annotated[tuple[MemoryClaim, ...], Field(min_length=1)]
    edges: tuple[MemoryEdge, ...] = ()
    source_links: Annotated[tuple[ExactSourceLink, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_package_local_references(self) -> "CandidatePackage":
        nodes = self._unique_by_key(self.nodes, "node_key", "node")
        claims = self._unique_by_key(self.claims, "claim_key", "claim")
        sources = self._unique_by_key(self.source_links, "source_key", "source")

        edge_identities = {
            (edge.edge_type, edge.source_node_key, edge.target_node_key)
            for edge in self.edges
        }
        if len(edge_identities) != len(self.edges):
            raise ValueError("edge identities must be unique")

        for edge in self.edges:
            if edge.source_node_key not in nodes or edge.target_node_key not in nodes:
                raise ValueError("edge endpoints must be package-local node keys")
            parent = nodes[edge.source_node_key]
            child = nodes[edge.target_node_key]
            allowed = (
                parent.node_kind == NodeKind.GLOBAL_STORY
                and child.node_kind in {NodeKind.STORY_ARC, NodeKind.VOLUME}
            ) or (
                parent.node_kind in {NodeKind.STORY_ARC, NodeKind.VOLUME}
                and child.node_kind == NodeKind.CHAPTER_STATE
            )
            if not allowed:
                raise ValueError("edge endpoints violate the closed memory hierarchy")
            if (
                parent.chapter_start > child.chapter_start
                or parent.chapter_end < child.chapter_end
            ):
                raise ValueError("parent range must contain child range")

        links_by_claim: dict[str, set[str]] = {}
        for link in self.source_links:
            if link.claim_key not in claims:
                raise ValueError("source link claim_key must be package-local")
            links_by_claim.setdefault(link.claim_key, set()).add(link.source_key)

        build_ids = {link.hierarchy_build_id for link in self.source_links}
        snapshots = {link.source_snapshot_hash for link in self.source_links}
        if len(build_ids) != 1 or len(snapshots) != 1:
            raise ValueError("all source links must use one frozen hierarchy snapshot")

        for claim in self.claims:
            node = nodes.get(claim.node_key)
            if node is None:
                raise ValueError("claim node_key must be package-local")
            if not node.chapter_start <= claim.visible_from_chapter <= node.chapter_end:
                raise ValueError("claim visibility must be inside its node range")
            expected_sources = set(claim.source_keys)
            if expected_sources != links_by_claim.get(claim.claim_key, set()):
                raise ValueError(
                    "claim source_keys must resolve exactly inside the package"
                )
            claim_links = [sources[key] for key in claim.source_keys]
            if any(
                not node.chapter_start <= link.chapter_number <= node.chapter_end
                for link in claim_links
            ):
                raise ValueError("claim evidence chapter must be inside its node range")
            if (
                max(link.chapter_number for link in claim_links)
                > claim.visible_from_chapter
            ):
                raise ValueError("claim cannot be visible before its latest evidence")
            if isinstance(claim.payload, EventFactClaim) and (
                claim.payload.chapter_start < node.chapter_start
                or claim.payload.chapter_end > node.chapter_end
            ):
                raise ValueError("event range must be inside its node range")

        return self

    @staticmethod
    def _unique_by_key(
        rows: tuple[object, ...], attribute: str, label: str
    ) -> dict[str, object]:
        indexed = {str(getattr(row, attribute)): row for row in rows}
        if len(indexed) != len(rows):
            raise ValueError(f"{label} keys must be unique")
        return indexed


def canonical_json(value: BaseModel) -> str:
    """Canonical Unicode JSON for an already validated contract model."""

    if not isinstance(value, BaseModel):
        raise TypeError("canonical_json accepts validated Pydantic models only")
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _component_checksum(component: str, value: BaseModel) -> str:
    encoded = f"narrative-memory.v1:{component}\n{canonical_json(value)}"
    return sha256(encoded.encode("utf-8")).hexdigest()


def model_lineage_checksum(value: ModelLineage) -> str:
    return _component_checksum("model-lineage", value)


def version_spec_checksum(value: CandidateVersionSpec) -> str:
    return _component_checksum("version-spec", value)


def node_checksum(value: MemoryNode) -> str:
    return _component_checksum("node", value)


def claim_checksum(value: MemoryClaim) -> str:
    return _component_checksum("claim", value)


def edge_checksum(value: MemoryEdge) -> str:
    return _component_checksum("edge", value)


def source_link_checksum(value: ExactSourceLink) -> str:
    return _component_checksum("source-link", value)
