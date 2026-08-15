"""Strict frozen contracts for the three knowledge spaces (Phase 35-01).

REQ-FORK-01 / REQ-CRE-01 / D-35-01: Original Canon, User Interpretation and
Fanfiction Canon exist only under explicit ``authority`` / ``namespace`` /
``version`` / ``citation`` rules. D-35-02 / D-35-03: the Original Canon space
is read-only by default and every space freezes owner, novel, version, source
snapshot/hash and spoiler cutoff; a mutable active row never replaces a version.

This module owns the deterministic contract layer only — it never touches the
database, and no write path for the Original Canon space exists here. The chain
is: strict DTO -> ORM composite scope -> migration constraints.

Design conventions (following ``narrative_memory/retrieval_contracts.py`` and
``schemas/illustration_anchor.py``): ``extra="forbid"`` frozen Pydantic v2
models, closed StrEnum vocabularies that mirror the ORM/migration, fail-closed
machine-readable rejections and byte-replayable canonical hashes.
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
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

Hash64 = Annotated[StrictStr, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NamespaceKey = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
VersionKey = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
EvidenceLeafKey = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
PipelineName = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
ContentText = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=50000),
]

CANON_SCHEMA_VERSION = "canon-fork.v1"
CANON_SCOPE_HASH_PREFIX = "canon-fork.v1:scope"
CANON_ARTIFACT_STATUSES = ("draft", "accepted", "rejected", "archived")
# Consumers that may only ever receive Original Canon rows; a derivative space
# must fail closed before any of these pipelines is entered (REQ-CRE-02).
ORIGINAL_PIPELINES = frozenset(
    {
        "original_analysis",
        "original_retrieval",
        "facet",
        "evaluation",
        "candidate_builder",
    }
)


class CanonForkContractError(ValueError):
    """Machine-readable fail-closed rejection from a knowledge-space boundary."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


class StrictCanonModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CanonSpace(StrEnum):
    ORIGINAL_CANON = "original_canon"
    USER_INTERPRETATION = "user_interpretation"
    FANFICTION_CANON = "fanfiction_canon"


class CanonAuthority(StrEnum):
    SOURCE_TEXT = "source_text"
    USER_ASSERTION = "user_assertion"
    CREATIVE_DRAFT = "creative_draft"


class CanonCitationPolicy(StrEnum):
    ORIGINAL_LEAF = "original_leaf"
    INTERPRETATION_WITH_ORIGINAL_REFS = "interpretation_with_original_refs"
    FANFICTION_ONLY = "fanfiction_only"


class CanonArtifactStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ARCHIVED = "archived"


# Deterministic per-space rules (D-35-01): space -> (authority, citation policy).
SPACE_RULES: dict[CanonSpace, tuple[CanonAuthority, CanonCitationPolicy]] = {
    CanonSpace.ORIGINAL_CANON: (
        CanonAuthority.SOURCE_TEXT,
        CanonCitationPolicy.ORIGINAL_LEAF,
    ),
    CanonSpace.USER_INTERPRETATION: (
        CanonAuthority.USER_ASSERTION,
        CanonCitationPolicy.INTERPRETATION_WITH_ORIGINAL_REFS,
    ),
    CanonSpace.FANFICTION_CANON: (
        CanonAuthority.CREATIVE_DRAFT,
        CanonCitationPolicy.FANFICTION_ONLY,
    ),
}

# Which spaces a citation may resolve to, per citing space (D-35-01).
# Original citations only ever resolve to original leaves; fanfiction citations
# stay inside fanfiction; interpretation citations anchor on original refs (or
# earlier interpretation rows) and never present fanfiction as original evidence.
CITATION_SOURCE_RULES: dict[CanonSpace, frozenset[CanonSpace]] = {
    CanonSpace.ORIGINAL_CANON: frozenset({CanonSpace.ORIGINAL_CANON}),
    CanonSpace.USER_INTERPRETATION: frozenset(
        {CanonSpace.ORIGINAL_CANON, CanonSpace.USER_INTERPRETATION}
    ),
    CanonSpace.FANFICTION_CANON: frozenset({CanonSpace.FANFICTION_CANON}),
}


def expected_authority(space: CanonSpace) -> CanonAuthority:
    """The single authority a space may carry (D-35-01)."""
    if space not in SPACE_RULES:
        raise CanonForkContractError(
            "unknown_space", f"unsupported knowledge space: {space}"
        )
    return SPACE_RULES[space][0]


def expected_citation_policy(space: CanonSpace) -> CanonCitationPolicy:
    """The single citation policy a space may carry (D-35-01)."""
    if space not in SPACE_RULES:
        raise CanonForkContractError(
            "unknown_space", f"unsupported knowledge space: {space}"
        )
    return SPACE_RULES[space][1]


def content_sha256(content: str) -> str:
    """Deterministic content hash for immutable lineage (D-35-03)."""
    return sha256(content.encode("utf-8")).hexdigest()


def canonical_scope_hash(payload: dict[str, Any]) -> str:
    """Byte-replayable canonical hash of a scope (checksum-preserving lineage)."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(f"{CANON_SCOPE_HASH_PREFIX}\n{encoded}".encode("utf-8")).hexdigest()


class CanonCutoff(StrictCanonModel):
    """Server-derived spoiler cutoff; never a transient client guess (D-35-03)."""

    through_chapter: PositiveInt
    full_book_authorized: StrictBool = False
    snapshot_hash: Hash64


class CanonScope(StrictCanonModel):
    """Immutable authority boundary for one knowledge space.

    A scope without owner, novel, space, namespace, version, source snapshot or
    cutoff is a hard rejection — unscoped queries cannot exist (D-35-03).
    """

    owner_id: PositiveInt
    novel_id: PositiveInt
    space: CanonSpace
    namespace: NamespaceKey
    version_key: VersionKey
    authority: CanonAuthority
    citation_policy: CanonCitationPolicy
    source_snapshot_hash: Hash64
    cutoff: CanonCutoff

    @property
    def through_chapter(self) -> int:
        return self.cutoff.through_chapter

    @property
    def full_book_authorized(self) -> bool:
        return self.cutoff.full_book_authorized

    def scope_hash(self) -> str:
        return canonical_scope_hash(
            {
                "owner_id": self.owner_id,
                "novel_id": self.novel_id,
                "space": self.space.value,
                "namespace": self.namespace,
                "version_key": self.version_key,
                "authority": self.authority.value,
                "citation_policy": self.citation_policy.value,
                "source_snapshot_hash": self.source_snapshot_hash,
                "through_chapter": self.cutoff.through_chapter,
                "full_book_authorized": self.cutoff.full_book_authorized,
                "cutoff_snapshot_hash": self.cutoff.snapshot_hash,
            }
        )

    @model_validator(mode="after")
    def _authority_and_citation_match_space(self) -> "CanonScope":
        want_authority = expected_authority(self.space)
        want_citation = expected_citation_policy(self.space)
        if self.authority != want_authority:
            raise CanonForkContractError(
                "authority_mismatch",
                "authority does not match the knowledge space",
            )
        if self.citation_policy != want_citation:
            raise CanonForkContractError(
                "citation_policy_mismatch",
                "citation policy does not match the knowledge space",
            )
        return self


class CanonCitation(StrictCanonModel):
    """A citation that must stay inside its authority namespace (D-35-01)."""

    scope: CanonScope
    cited_space: CanonSpace
    cited_namespace: NamespaceKey
    leaf_key: EvidenceLeafKey
    content_hash: Hash64
    source_snapshot_hash: Hash64

    @model_validator(mode="after")
    def _citation_within_authority(self) -> "CanonCitation":
        allowed = CITATION_SOURCE_RULES[self.scope.space]
        if self.cited_space not in allowed:
            raise CanonForkContractError(
                "citation_scope",
                f"{self.scope.space.value} citations cannot resolve to "
                f"{self.cited_space.value} evidence",
            )
        return self


class CanonWriteIntent(StrictCanonModel):
    """A write candidate for one knowledge space.

    The Original Canon space is read-only: no write path exists for it
    (D-35-02). ``content_hash`` must replay from the content (immutable
    lineage, D-35-03); a duplicate version is rejected by the ORM composite
    unique scope and the migration constraint.
    """

    scope: CanonScope
    content: ContentText
    content_hash: Hash64
    status: CanonArtifactStatus = CanonArtifactStatus.DRAFT

    @model_validator(mode="after")
    def _original_readonly(self) -> "CanonWriteIntent":
        if self.scope.space is CanonSpace.ORIGINAL_CANON:
            raise CanonForkContractError(
                "original_readonly",
                "original_canon is read-only; no write path exists",
            )
        if self.content_hash != content_sha256(self.content):
            raise CanonForkContractError(
                "content_hash_mismatch",
                "content_hash does not replay from the content",
            )
        return self

    @property
    def read_only(self) -> bool:
        """Database read_only marker: True only for the Original Canon space."""
        return self.scope.space is CanonSpace.ORIGINAL_CANON


def validate_scope(scope: CanonScope) -> CanonScope:
    """Fail closed on an incomplete or mismatched retrieval/write scope.

    The frozen DTO already enforces positive owner/novel, non-empty
    namespace/version and a positive cutoff; this is the explicit boundary gate
    callers invoke before any read or write path.
    """
    if scope.owner_id < 1 or scope.novel_id < 1:
        raise CanonForkContractError(
            "invalid_scope", "owner_id and novel_id must be positive"
        )
    if not scope.namespace or not scope.version_key:
        raise CanonForkContractError(
            "missing_lineage", "namespace and version_key are required"
        )
    return scope


def assert_citation_authority(
    citing_space: CanonSpace, cited_space: CanonSpace
) -> None:
    """Prevent citations from crossing into a different authority namespace."""
    if cited_space not in CITATION_SOURCE_RULES[citing_space]:
        raise CanonForkContractError(
            "citation_scope",
            f"{citing_space.value} citations cannot resolve to "
            f"{cited_space.value} evidence",
        )


def assert_original_readonly(space: CanonSpace, *, mutation: bool = False) -> None:
    """Original Canon is queried read-only; mutation is always forbidden."""
    if space is CanonSpace.ORIGINAL_CANON and mutation:
        raise CanonForkContractError(
            "original_readonly",
            "original_canon is read-only; no mutation path exists",
        )


def assert_original_pipeline_input(space: CanonSpace, pipeline: PipelineName) -> None:
    """Reject derivative spaces from every Original Canon consumer (REQ-CRE-02)."""
    if pipeline in ORIGINAL_PIPELINES and space is not CanonSpace.ORIGINAL_CANON:
        raise CanonForkContractError(
            "space_excluded",
            f"{space.value} cannot enter {pipeline}; original_canon is required",
        )


def build_scope(
    *,
    owner_id: int,
    novel_id: int,
    space: str,
    namespace: str,
    version_key: str,
    source_snapshot_hash: str,
    through_chapter: int,
    cutoff_snapshot_hash: str,
    full_book_authorized: bool = False,
) -> CanonScope:
    """Convenience constructor that derives authority/citation from the space."""
    canon_space = CanonSpace(space)
    return CanonScope(
        owner_id=owner_id,
        novel_id=novel_id,
        space=canon_space,
        namespace=namespace,
        version_key=version_key,
        authority=expected_authority(canon_space),
        citation_policy=expected_citation_policy(canon_space),
        source_snapshot_hash=source_snapshot_hash,
        cutoff=CanonCutoff(
            through_chapter=through_chapter,
            full_book_authorized=full_book_authorized,
            snapshot_hash=cutoff_snapshot_hash,
        ),
    )


__all__ = [
    "CANON_ARTIFACT_STATUSES",
    "CANON_SCHEMA_VERSION",
    "CITATION_SOURCE_RULES",
    "ORIGINAL_PIPELINES",
    "SPACE_RULES",
    "CanonArtifactStatus",
    "CanonAuthority",
    "CanonCitation",
    "CanonCitationPolicy",
    "CanonCutoff",
    "CanonForkContractError",
    "CanonScope",
    "CanonSpace",
    "CanonWriteIntent",
    "assert_citation_authority",
    "assert_original_pipeline_input",
    "assert_original_readonly",
    "build_scope",
    "canonical_scope_hash",
    "content_sha256",
    "expected_authority",
    "expected_citation_policy",
    "validate_scope",
]
