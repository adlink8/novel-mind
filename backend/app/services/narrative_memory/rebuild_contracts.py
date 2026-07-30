"""Strict frozen contracts for Phase 16 dependency-aware rebuild.

All graph, change, decision, and report identities are deterministic script
logic. Provider, embedding, retrieval telemetry, and database IDs never enter
canonical identity.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.services.narrative_memory.contracts import (
    Hash64,
    Key,
    NonNegativeInt,
    VersionLabel,
)


class RebuildFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def stable_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_checksum(value: object) -> str:
    return sha256(stable_json(value).encode("utf-8")).hexdigest()


class RebuildDecision(StrEnum):
    DIRTY = "dirty"
    CARRIED = "carried"
    STALE_BLOCKED = "stale_blocked"
    NOT_APPLICABLE = "not_applicable"


class AssetKind(StrEnum):
    SOURCE_CHAPTER = "source_chapter"
    EVIDENCE_LEAF = "evidence_leaf"
    CHAPTER_STATE = "chapter_state"
    STORY_ARC = "story_arc"
    VOLUME = "volume"
    GLOBAL_STORY = "global_story"
    BOUNDARY_PLAN = "boundary_plan"
    OPTIONAL_SOURCE = "optional_source"


class ChangeKind(StrEnum):
    NO_CHANGE = "no_change"
    EDIT = "edit"
    INSERT = "insert"
    DELETE = "delete"
    REORDER = "reorder"
    EVIDENCE_REMAP = "evidence_remap"
    EVIDENCE_SPLIT = "evidence_split"
    EVIDENCE_MERGE = "evidence_merge"
    BOUNDARY_CHANGE = "boundary_change"
    OPTIONAL_SOURCE_DRIFT = "optional_source_drift"
    DEPENDENCY_UNCERTAINTY = "dependency_uncertainty"
    POLICY_INCOMPATIBLE = "policy_incompatible"
    MAPPING_AMBIGUOUS = "mapping_ambiguous"


class ReasonCode(StrEnum):
    CHAPTER_EDITED = "chapter_edited"
    CHAPTER_INSERTED = "chapter_inserted"
    CHAPTER_DELETED = "chapter_deleted"
    CHAPTER_REORDERED = "chapter_reordered"
    EVIDENCE_REMAPPED = "evidence_remapped"
    EVIDENCE_SPLIT = "evidence_split"
    EVIDENCE_MERGED = "evidence_merged"
    BOUNDARY_CHANGED = "boundary_changed"
    OPTIONAL_SOURCE_LINEAGE = "optional_source_lineage"
    CROSS_CHAPTER_UNCERTAIN = "cross_chapter_uncertain"
    MAPPING_UNPROVEN = "mapping_unproven"
    POLICY_INCOMPATIBLE = "policy_incompatible"
    CHILD_DIRTY = "child_dirty"
    PARENT_PROPAGATED = "parent_propagated"
    GLOBAL_PROPAGATED = "global_propagated"
    SUFFIX_EXPANDED = "suffix_expanded"
    CLEAN_IDENTICAL = "clean_identical"
    NO_PARENT_ASSET = "no_parent_asset"
    STALE_SEAL = "stale_seal"
    TARGET_CONFLICT = "target_conflict"


class EdgeKind(StrEnum):
    SOURCE_TO_CHAPTER_STATE = "source_to_chapter_state"
    EVIDENCE_TO_CHAPTER_STATE = "evidence_to_chapter_state"
    CHAPTER_TO_PARENT = "chapter_to_parent"
    PARENT_TO_GLOBAL = "parent_to_global"
    BOUNDARY_TO_PARENT = "boundary_to_parent"
    BOUNDARY_TO_GLOBAL = "boundary_to_global"
    OPTIONAL_TO_CLAIM = "optional_to_claim"


class OraclePolicy(RebuildFrozenModel):
    """Frozen reuse/compatibility policy included in plan hash."""

    policy_version: VersionLabel = "rebuild-oracle.v1"
    allow_model_revision_carry: bool = True
    require_sealed_parent: bool = True
    require_unsealed_target: bool = True
    expand_uncertain_to_suffix: bool = True
    expand_uncertain_to_global: bool = True

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class CompatibilityPolicy(RebuildFrozenModel):
    """Schema/model/policy compatibility gates for carry-forward."""

    schema_hash: Hash64
    policy_hash: Hash64
    prompt_hash: Hash64 | None = None
    decoding_hash: Hash64 | None = None
    config_hash: Hash64 | None = None
    allowed_model_revisions: tuple[VersionLabel, ...] = ()

    def checksum(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class GraphVertex(RebuildFrozenModel):
    asset_key: Key
    asset_kind: AssetKind
    chapter_start: NonNegativeInt | None = None
    chapter_end: NonNegativeInt | None = None
    content_checksum: Hash64 | None = None
    evidence_fingerprint: Hash64 | None = None
    optional_fingerprint: Hash64 | None = None
    compatibility_fingerprint: Hash64 | None = None
    stage_key: Key | None = None
    # Semantic identity attributes only (no DB ids).
    attributes: dict[str, StrictStr | StrictInt | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self) -> "GraphVertex":
        if self.chapter_start is not None:
            if self.chapter_end is None or self.chapter_end < self.chapter_start:
                raise ValueError("invalid chapter range on graph vertex")
        return self


class GraphEdge(RebuildFrozenModel):
    edge_kind: EdgeKind
    source_key: Key
    target_key: Key
    reason: ReasonCode | None = None


class DependencyGraph(RebuildFrozenModel):
    vertices: tuple[GraphVertex, ...]
    edges: tuple[GraphEdge, ...]
    graph_checksum: Hash64

    @classmethod
    def from_parts(
        cls, vertices: list[GraphVertex], edges: list[GraphEdge]
    ) -> "DependencyGraph":
        vuniq: dict[str, GraphVertex] = {}
        for vertex in vertices:
            vuniq.setdefault(vertex.asset_key, vertex)
        euniq: dict[tuple[str, str, str], GraphEdge] = {}
        for edge in edges:
            euniq[(edge.edge_kind.value, edge.source_key, edge.target_key)] = edge
        ordered_v = tuple(
            sorted(vuniq.values(), key=lambda v: (v.asset_kind.value, v.asset_key))
        )
        ordered_e = tuple(
            sorted(
                euniq.values(),
                key=lambda e: (e.edge_kind.value, e.source_key, e.target_key),
            )
        )
        body = {
            "vertices": [v.model_dump(mode="json") for v in ordered_v],
            "edges": [e.model_dump(mode="json") for e in ordered_e],
        }
        return cls(
            vertices=ordered_v,
            edges=ordered_e,
            graph_checksum=stable_checksum(body),
        )


class ChangeRecord(RebuildFrozenModel):
    asset_key: Key
    asset_kind: AssetKind
    change_kind: ChangeKind
    reasons: tuple[ReasonCode, ...]
    chapter_start: NonNegativeInt | None = None
    chapter_end: NonNegativeInt | None = None
    old_checksum: Hash64 | None = None
    new_checksum: Hash64 | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class RebuildItemDecision(RebuildFrozenModel):
    asset_key: Key
    asset_kind: AssetKind
    decision: RebuildDecision
    direct_reasons: tuple[ReasonCode, ...]
    propagated_reasons: tuple[ReasonCode, ...]
    predecessor_keys: tuple[Key, ...]
    chapter_start: NonNegativeInt | None = None
    chapter_end: NonNegativeInt | None = None
    old_content_checksum: Hash64 | None = None
    new_content_checksum: Hash64 | None = None
    dependency_checksum: Hash64 | None = None
    stage_key: Key | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class RebuildPlanSpec(RebuildFrozenModel):
    owner_id: PositiveInt
    novel_id: PositiveInt
    parent_version_id: PositiveInt
    target_version_id: PositiveInt
    old_source_snapshot_hash: Hash64
    new_source_snapshot_hash: Hash64
    old_hierarchy_build_id: Key
    new_hierarchy_build_id: Key
    old_hierarchy_checksum: Hash64
    new_hierarchy_checksum: Hash64
    boundary_plan: dict[str, Any]
    boundary_plan_checksum: Hash64
    oracle_policy: OraclePolicy
    compatibility_policy: CompatibilityPolicy
    eligibility_report_checksum: Hash64
    graph_checksum: Hash64
    items: tuple[RebuildItemDecision, ...]
    change_summary: dict[str, Any] = Field(default_factory=dict)

    def plan_checksum(self) -> str:
        body = {
            "owner_id": self.owner_id,
            "novel_id": self.novel_id,
            "parent_version_id": self.parent_version_id,
            "target_version_id": self.target_version_id,
            "old_source_snapshot_hash": self.old_source_snapshot_hash,
            "new_source_snapshot_hash": self.new_source_snapshot_hash,
            "old_hierarchy_build_id": self.old_hierarchy_build_id,
            "new_hierarchy_build_id": self.new_hierarchy_build_id,
            "old_hierarchy_checksum": self.old_hierarchy_checksum,
            "new_hierarchy_checksum": self.new_hierarchy_checksum,
            "boundary_plan_checksum": self.boundary_plan_checksum,
            "oracle_policy_checksum": self.oracle_policy.checksum(),
            "compatibility_policy_checksum": self.compatibility_policy.checksum(),
            "eligibility_report_checksum": self.eligibility_report_checksum,
            "graph_checksum": self.graph_checksum,
            "items": [
                item.model_dump(mode="json")
                for item in sorted(self.items, key=lambda i: i.asset_key)
            ],
            "change_summary": self.change_summary,
        }
        return stable_checksum(body)


class EvidenceFingerprint(RebuildFrozenModel):
    """Authoritative evidence identity independent of hierarchy node ids."""

    chapter_id: PositiveInt
    chapter_number: NonNegativeInt
    source_start: Annotated[StrictInt, Field(ge=0)]
    source_end: NonNegativeInt
    content_hash: Hash64

    def fingerprint(self) -> str:
        return stable_checksum(self.model_dump(mode="json"))


class ChapterIdentity(RebuildFrozenModel):
    """Stable scoped chapter identity (DB chapter id under owner/novel)."""

    chapter_id: PositiveInt
    chapter_number: NonNegativeInt
    content_hash: Hash64 | None = None
    narrative_order: NonNegativeInt | None = None

    def semantic_key(self) -> str:
        return f"source_chapter:{self.chapter_id}"


# Closed reason lattices used by tests and reports.
DIRECT_CHANGE_REASONS = frozenset(
    {
        ReasonCode.CHAPTER_EDITED,
        ReasonCode.CHAPTER_INSERTED,
        ReasonCode.CHAPTER_DELETED,
        ReasonCode.CHAPTER_REORDERED,
        ReasonCode.EVIDENCE_REMAPPED,
        ReasonCode.EVIDENCE_SPLIT,
        ReasonCode.EVIDENCE_MERGED,
        ReasonCode.BOUNDARY_CHANGED,
        ReasonCode.OPTIONAL_SOURCE_LINEAGE,
        ReasonCode.CROSS_CHAPTER_UNCERTAIN,
        ReasonCode.MAPPING_UNPROVEN,
        ReasonCode.POLICY_INCOMPATIBLE,
        ReasonCode.CLEAN_IDENTICAL,
        ReasonCode.NO_PARENT_ASSET,
        ReasonCode.STALE_SEAL,
        ReasonCode.TARGET_CONFLICT,
    }
)
PROPAGATED_REASONS = frozenset(
    {
        ReasonCode.CHILD_DIRTY,
        ReasonCode.PARENT_PROPAGATED,
        ReasonCode.GLOBAL_PROPAGATED,
        ReasonCode.SUFFIX_EXPANDED,
    }
)

FORBIDDEN_IDENTITY_FIELDS = frozenset(
    {
        "id",
        "node_id",
        "claim_id",
        "edge_id",
        "display_label",
        "query",
        "trace",
        "score",
        "similarity",
        "embedding",
        "citation",
        "citations",
        "retrieval_cache",
        "reader_chat",
        "conversation_id",
        "active_pointer",
        "current_version",
        "promotion",
    }
)


def stage_key_for_asset(asset_kind: AssetKind, asset_key: str) -> str | None:
    """Map semantic asset keys to Phase 14 stage keys when applicable."""

    if asset_kind == AssetKind.CHAPTER_STATE:
        # asset_key form: chapter_state:{chapter_id}
        suffix = asset_key.split(":", 1)[-1]
        return f"chapter_state:{suffix}"
    if asset_kind in {AssetKind.STORY_ARC, AssetKind.VOLUME}:
        return asset_key  # already stage-shaped (story_arc:1-3 / volume:1)
    if asset_kind == AssetKind.GLOBAL_STORY:
        return "global_story:book"
    if asset_kind == AssetKind.BOUNDARY_PLAN:
        return "arc_volume_plan:book"
    return None


def asset_kind_from_node_kind(node_kind: str) -> AssetKind:
    mapping = {
        "chapter_state": AssetKind.CHAPTER_STATE,
        "story_arc": AssetKind.STORY_ARC,
        "volume": AssetKind.VOLUME,
        "global_story": AssetKind.GLOBAL_STORY,
    }
    if node_kind not in mapping:
        raise ValueError(f"unknown node kind: {node_kind}")
    return mapping[node_kind]


def assert_no_forbidden_identity(payload: dict[str, Any]) -> None:
    bad = FORBIDDEN_IDENTITY_FIELDS.intersection(payload)
    if bad:
        raise ValueError(f"forbidden identity fields: {sorted(bad)}")
