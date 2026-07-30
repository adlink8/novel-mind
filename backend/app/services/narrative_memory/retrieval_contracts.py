"""Strict frozen contracts for offline hierarchical retrieval experiments.

Phase 15 is candidate-only and read-only. These DTOs never carry free-text
rationales, raw cache keys, unread titles, or hidden future counts.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)


Hash64 = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[StrictInt, Field(gt=0)]
NonNegInt = Annotated[StrictInt, Field(ge=0)]
PolicyVersion = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
BuildId = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RouteMode(StrEnum):
    LOCAL = "local"
    ARC = "arc"
    GLOBAL = "global"
    MIXED = "mixed"


class RouteReasonCode(StrEnum):
    SELECTION_ANCHOR = "selection_anchor"
    LOCAL_FACT_INTENT = "local_fact_intent"
    CROSS_CHAPTER_INTENT = "cross_chapter_intent"
    WHOLE_BOOK_INTENT = "whole_book_intent"
    MULTIPLE_SCOPE_SIGNALS = "multiple_scope_signals"
    SAFE_DEFAULT = "safe_default"
    UNAUTHORIZED_GLOBAL = "unauthorized_global"
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    NO_ANSWER_SHAPE = "no_answer_shape"


class StartLevel(StrEnum):
    CHAPTER_STATE = "chapter_state"
    STORY_ARC = "story_arc"
    VOLUME = "volume"
    GLOBAL_STORY = "global_story"


class CandidateSourceStatus(StrEnum):
    OK = "ok"
    ABSENT = "absent"
    INCOMPLETE = "incomplete"
    UNSEALED = "unsealed"
    MISMATCH = "mismatch"
    BLOCKED = "blocked"


class FallbackReasonCode(StrEnum):
    NONE = "none"
    UPPER_ABSENT = "upper_absent"
    UPPER_PARTIAL = "upper_partial"
    NO_VISIBLE_CHILD = "no_visible_child"
    INVALID_LEAF = "invalid_leaf"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RAW_FALLBACK = "raw_fallback"
    MISROUTE = "misroute"
    NO_ANSWER = "no_answer"


class RetrievalRunStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"


class SafeSourceStatus(StrEnum):
    OK = "ok"
    ABSENT = "absent"
    FALLBACK = "fallback"
    BLOCKED = "blocked"


class RetrievalBudgets(StrictFrozenModel):
    max_nodes: PositiveInt = 32
    max_claims: PositiveInt = 64
    max_leaves: PositiveInt = 16
    max_depth: PositiveInt = 6
    max_fanout: PositiveInt = 8


class CutoffSnapshot(StrictFrozenModel):
    """Persisted reading-progress cutoff; never a transient client guess."""

    through_chapter: NonNegativeInt
    full_book_authorized: StrictBool = False
    snapshot_hash: Hash64


class RetrievalScope(StrictFrozenModel):
    """Immutable authority boundary applied before any ranking or trace."""

    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    source_snapshot_hash: Hash64
    hierarchy_build_id: BuildId
    hierarchy_checksum: Hash64
    candidate_manifest_checksum: Hash64
    cutoff: CutoffSnapshot
    policy_version: PolicyVersion
    policy_hash: Hash64
    budgets: RetrievalBudgets = Field(default_factory=RetrievalBudgets)

    @property
    def through_chapter(self) -> int:
        return self.cutoff.through_chapter

    @property
    def full_book_authorized(self) -> bool:
        return self.cutoff.full_book_authorized


class RetrievalQuestion(StrictFrozenModel):
    """Normalized question; raw text is never logged or traced."""

    normalized_text: Annotated[
        StrictStr,
        StringConstraints(min_length=1, max_length=2000),
    ]
    query_hash: Hash64
    selected_chapter: NonNegativeInt | None = None
    selected_start: NonNegInt | None = None
    selected_end: NonNegativeInt | None = None

    @model_validator(mode="after")
    def _selection_bounds(self) -> RetrievalQuestion:
        has_start = self.selected_start is not None
        has_end = self.selected_end is not None
        if has_start != has_end:
            raise ValueError("selection offsets must be provided together")
        if has_start and has_end:
            assert self.selected_start is not None
            assert self.selected_end is not None
            if self.selected_end <= self.selected_start:
                raise ValueError("selected_end must be greater than selected_start")
            if self.selected_chapter is None:
                raise ValueError("selected_chapter required with offsets")
        return self


class RouteDecision(StrictFrozenModel):
    mode: RouteMode
    start_levels: tuple[StartLevel, ...]
    reason_codes: tuple[RouteReasonCode, ...]
    policy_version: PolicyVersion
    policy_hash: Hash64

    @field_validator("start_levels")
    @classmethod
    def _non_empty_levels(cls, value: tuple[StartLevel, ...]) -> tuple[StartLevel, ...]:
        if not value:
            raise ValueError("start_levels must be non-empty")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _non_empty_reasons(
        cls, value: tuple[RouteReasonCode, ...]
    ) -> tuple[RouteReasonCode, ...]:
        if not value:
            raise ValueError("reason_codes must be non-empty")
        return value


class VisibleCandidate(StrictFrozenModel):
    """Already-admitted visible identity only; no titles/summaries."""

    candidate_kind: Literal["node", "claim", "edge", "source_link"]
    entity_id: NonNegativeInt
    stable_key: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    node_kind: StartLevel | None = None
    chapter_start: NonNegativeInt | None = None
    chapter_end: NonNegativeInt | None = None
    parent_node_id: NonNegativeInt | None = None
    rank_key: Annotated[
        StrictStr,
        StringConstraints(min_length=1, max_length=240),
    ]


class TraversalStep(StrictFrozenModel):
    level: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
    ]
    candidate_key: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    parent_key: (
        Annotated[
            StrictStr,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        ]
        | None
    ) = None
    relation: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
    ]
    visible_candidate_count: NonNegInt
    omitted_after_budget: NonNegInt
    outcome: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
    ]


class LeafCitation(StrictFrozenModel):
    """Final evidence only after fresh Chapter re-slice and hash validation."""

    chapter_id: PositiveInt
    chapter_number: NonNegativeInt
    evidence_node_id: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    ]
    hierarchy_build_id: BuildId
    source_start: NonNegInt
    source_end: NonNegativeInt
    content_hash: Hash64
    excerpt: Annotated[
        StrictStr,
        StringConstraints(min_length=1, max_length=4000),
    ]
    source_snapshot_hash: Hash64
    link_id: NonNegativeInt | None = None
    claim_id: NonNegativeInt | None = None


class CacheEnvelope(StrictFrozenModel):
    """Opaque cache identity; raw key never exposed publicly."""

    identity_hash: Hash64
    scope_hash: Hash64
    route_hash: Hash64
    query_hash: Hash64
    budget_hash: Hash64
    source_status: CandidateSourceStatus


class SafeTrace(StrictFrozenModel):
    """Public/audit-safe trace: only visible-set derived fields."""

    route: RouteDecision
    source_status: SafeSourceStatus
    fallback_reason: FallbackReasonCode
    visible_node_count: NonNegInt
    visible_claim_count: NonNegInt
    visible_leaf_count: NonNegInt
    omitted_after_budget: NonNegInt
    traversal: tuple[TraversalStep, ...]
    run_status: RetrievalRunStatus


class RetrievalManifest(StrictFrozenModel):
    schema_version: Literal["narrative-memory-retrieval-manifest.v1"] = (
        "narrative-memory-retrieval-manifest.v1"
    )
    scope_hash: Hash64
    query_hash: Hash64
    policy_hash: Hash64
    candidate_manifest_checksum: Hash64
    hierarchy_build_id: BuildId
    hierarchy_checksum: Hash64
    source_snapshot_hash: Hash64
    cutoff_snapshot_hash: Hash64
    route: RouteDecision
    fallback_reason: FallbackReasonCode
    source_status: SafeSourceStatus
    run_status: RetrievalRunStatus
    traversal: tuple[TraversalStep, ...]
    citations: tuple[LeafCitation, ...]
    omitted_after_budget: NonNegInt
    manifest_checksum: Hash64


_WS_RE = re.compile(r"\s+", re.UNICODE)


def normalize_query_text(raw: str) -> str:
    """NFKC normalize, collapse whitespace; preserve Chinese Unicode semantics."""

    if not isinstance(raw, str):
        raise TypeError("query text must be str")
    text = unicodedata.normalize("NFKC", raw).strip()
    text = _WS_RE.sub(" ", text)
    if not text:
        raise ValueError("query text must be non-empty after normalization")
    return text


def hash_query_text(normalized: str) -> str:
    """Hash normalized query; never store or log the raw question."""

    payload = f"narrative-memory-retrieval.v1:query\n{normalized}"
    return sha256(payload.encode("utf-8")).hexdigest()


def build_question(
    raw: str,
    *,
    selected_chapter: int | None = None,
    selected_start: int | None = None,
    selected_end: int | None = None,
) -> RetrievalQuestion:
    normalized = normalize_query_text(raw)
    return RetrievalQuestion(
        normalized_text=normalized,
        query_hash=hash_query_text(normalized),
        selected_chapter=selected_chapter,
        selected_start=selected_start,
        selected_end=selected_end,
    )


def canonical_retrieval_json(value: BaseModel) -> str:
    if not isinstance(value, BaseModel):
        raise TypeError("canonical_retrieval_json accepts Pydantic models only")
    import json

    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def retrieval_component_hash(component: str, value: BaseModel | dict) -> str:
    import json

    if isinstance(value, BaseModel):
        body = canonical_retrieval_json(value)
    else:
        body = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    encoded = f"narrative-memory-retrieval.v1:{component}\n{body}"
    return sha256(encoded.encode("utf-8")).hexdigest()


def scope_hash(scope: RetrievalScope) -> str:
    return retrieval_component_hash("scope", scope)


def route_hash(route: RouteDecision) -> str:
    return retrieval_component_hash("route", route)


def budget_hash(budgets: RetrievalBudgets) -> str:
    return retrieval_component_hash("budgets", budgets)


def build_cache_envelope(
    *,
    scope: RetrievalScope,
    route: RouteDecision,
    question: RetrievalQuestion,
    source_status: CandidateSourceStatus,
) -> CacheEnvelope:
    s_hash = scope_hash(scope)
    r_hash = route_hash(route)
    b_hash = budget_hash(scope.budgets)
    identity = retrieval_component_hash(
        "cache-identity",
        {
            "scope_hash": s_hash,
            "route_hash": r_hash,
            "query_hash": question.query_hash,
            "budget_hash": b_hash,
            "source_status": source_status.value,
            "owner_id": scope.owner_id,
            "novel_id": scope.novel_id,
            "version_id": scope.version_id,
            "manifest": scope.candidate_manifest_checksum,
            "snapshot": scope.source_snapshot_hash,
            "hierarchy": scope.hierarchy_checksum,
            "cutoff": scope.cutoff.snapshot_hash,
            "policy": scope.policy_hash,
        },
    )
    return CacheEnvelope(
        identity_hash=identity,
        scope_hash=s_hash,
        route_hash=r_hash,
        query_hash=question.query_hash,
        budget_hash=b_hash,
        source_status=source_status,
    )
