"""Strict wire DTOs for the three knowledge spaces (Phase 35-01).

REQ-FORK-01 / REQ-CRE-01 / D-35-01: every space is created with an explicit
``authority`` / ``namespace`` / ``version_key`` / ``citation_policy`` and an
immutable source snapshot + spoiler cutoff. D-35-02: the Original Canon space
is read-only — there is no write schema for it, and a create that claims the
Original space fails closed. The read view mirrors the ORM so API consumers
never read an unscoped or non-versioned artifact.

This module is the API boundary of the contract chain
(``services/canon_fork/contracts.py`` -> ``models/canon_space.py`` ->
``migrations/.../35_canon_space01.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.canon_fork.contracts import (
    CanonArtifactStatus,
    CanonAuthority,
    CanonCitationPolicy,
    CanonForkContractError,
    CanonSpace,
    CanonWriteIntent,
    build_scope,
    content_sha256,
    expected_authority,
    expected_citation_policy,
)

CANON_SPACE_SCHEMA_VERSION = "canon-space.v1"


class StrictCanonSchema(BaseModel):
    # ``extra="forbid"`` but non-strict enum coercion so wire strings and ORM
    # attributes (plain str) validate into the closed StrEnum vocabulary.
    model_config = ConfigDict(extra="forbid")


class CanonSpaceArtifactCreate(StrictCanonSchema):
    """Create a versioned artifact in a candidate (non-Original) space.

    The Original Canon space is read-only (D-35-02): a create with
    ``space = original_canon`` fails closed because no Original write path
    exists. ``content_hash`` must replay from the content and
    ``source_snapshot_hash`` / ``through_chapter`` freeze the lineage.
    """

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    space: CanonSpace
    namespace: str = Field(min_length=1, max_length=128)
    version_key: str = Field(min_length=1, max_length=128)
    authority: CanonAuthority
    citation_policy: CanonCitationPolicy
    content: str = Field(min_length=1, max_length=50000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    through_chapter: int = Field(gt=0)
    full_book_authorized: bool = False
    status: CanonArtifactStatus = CanonArtifactStatus.DRAFT

    @model_validator(mode="after")
    def _space_rules_fail_closed(self) -> "CanonSpaceArtifactCreate":
        if self.space is CanonSpace.ORIGINAL_CANON:
            raise CanonForkContractError(
                "original_readonly",
                "original_canon is read-only; no write schema exists",
            )
        want_authority = expected_authority(self.space)
        want_citation = expected_citation_policy(self.space)
        if self.authority is not want_authority:
            raise CanonForkContractError(
                "authority_mismatch",
                "authority does not match the knowledge space",
            )
        if self.citation_policy is not want_citation:
            raise CanonForkContractError(
                "citation_policy_mismatch",
                "citation policy does not match the knowledge space",
            )
        if self.content_hash != content_sha256(self.content):
            raise CanonForkContractError(
                "content_hash_mismatch",
                "content_hash does not replay from the content",
            )
        return self

    @property
    def read_only(self) -> bool:
        return self.space is CanonSpace.ORIGINAL_CANON

    def to_write_intent(self, *, cutoff_snapshot_hash: str) -> CanonWriteIntent:
        """Convert to the domain write candidate under the frozen cutoff."""
        scope = build_scope(
            owner_id=self.owner_id,
            novel_id=self.novel_id,
            space=self.space.value,
            namespace=self.namespace,
            version_key=self.version_key,
            source_snapshot_hash=self.source_snapshot_hash,
            through_chapter=self.through_chapter,
            cutoff_snapshot_hash=cutoff_snapshot_hash,
            full_book_authorized=self.full_book_authorized,
        )
        return CanonWriteIntent(
            scope=scope,
            content=self.content,
            content_hash=self.content_hash,
            status=self.status,
        )


class CanonSpaceArtifactView(StrictCanonSchema):
    """Read envelope for a persisted artifact (owner-scoped, never un-versioned)."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    space: CanonSpace
    namespace: str
    version_key: str
    authority: CanonAuthority
    citation_policy: CanonCitationPolicy
    status: CanonArtifactStatus
    content: str
    content_hash: str
    source_snapshot_hash: str
    through_chapter: int
    full_book_authorized: bool
    read_only: bool


class CanonSpaceQuery(StrictCanonSchema):
    """Read-only query contract; Original queries are always read-only.

    D-35-03: a query without owner/novel/space/version/cutoff is rejected — the
    scope is frozen before any ranking or write path is reached.
    """

    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    space: CanonSpace
    namespace: str = Field(min_length=1, max_length=128)
    version_key: str = Field(min_length=1, max_length=128)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    through_chapter: int = Field(gt=0)
    full_book_authorized: bool = False
    cutoff_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mode: Literal["read"] = "read"


__all__ = [
    "CANON_SPACE_SCHEMA_VERSION",
    "CanonSpaceArtifactCreate",
    "CanonSpaceArtifactView",
    "CanonSpaceQuery",
]
