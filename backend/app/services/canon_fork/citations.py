"""Leaf citation revalidation for the three knowledge spaces (Phase 35-03).

D-35-01 / D-35-03: a citation is admitted only after it is revalidated against
the frozen scope — owner, fork/version (authorized namespace/version), cutoff
and leaf offset/hash. Every failure returns an auditable ``blocked_reason``;
a citation is never resolved to a fake empty success (REQ-CRE-01, T-35-03-02).

Chain (leaf hash revalidation -> citation response):

    citation policy gate -> authorized namespace/version (fork/version)
    -> leaf resolve (per cited space) -> owner/novel -> cutoff
    -> snapshot replay -> offset/hash replay -> verdict

The provider is the namespace boundary for the *cited* space; it resolves only
authorized leaf evidence (for a fanfiction fork, only leaves frozen in the
fork's own citation lineage).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, StrictBool, StrictInt, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.canon_fork import CanonFork
from app.models.canon_space import CanonSpaceArtifact
from app.models.novel import Chapter
from app.services.canon_fork.contracts import (
    CITATION_SOURCE_RULES,
    ContentText,
    EvidenceLeafKey,
    Hash64,
    NamespaceKey,
    PositiveInt,
    CanonAuthority,
    CanonCitationPolicy,
    CanonForkContractError,
    CanonScope,
    CanonSpace,
    StrictCanonModel,
    assert_citation_authority,
    content_sha256,
    expected_authority,
    expected_citation_policy,
)
from app.services.canon_fork.lineage import FORK_LEAF_NAMESPACE
from app.services.canon_fork.snapshot import (
    CANON_FORK_SPACE,
    CanonForkSnapshotService,
    compute_source_snapshot_hash,
)

NonNegInt = Annotated[StrictInt, Field(ge=0)]


class CitationBlockedReason(StrEnum):
    CITATION_SCOPE = "citation_scope"
    OWNER_SCOPE = "owner_scope"
    NOVEL_SCOPE = "novel_scope"
    FORK_VERSION_MISMATCH = "fork_version_mismatch"
    BEYOND_CUTOFF = "beyond_cutoff"
    STALE_HASH = "stale_hash"
    INVALID_OFFSET = "invalid_offset"
    UNKNOWN_LEAF = "unknown_leaf"
    UNSEALED = "unsealed"


class CanonCitationRef(StrictCanonModel):
    """One citation that must be revalidated before it can be resolved.

    The client supplies only the ref; owner/novel/version/cutoff/snapshot are
    bound by the frozen scope the citation is revalidated against.
    """

    cited_space: CanonSpace
    cited_namespace: NamespaceKey
    leaf_key: EvidenceLeafKey
    content_hash: Hash64
    source_snapshot_hash: Hash64
    chapter_number: PositiveInt | None = None
    source_start: NonNegInt | None = None
    source_end: PositiveInt | None = None

    @model_validator(mode="after")
    def _offsets_together(self) -> "CanonCitationRef":
        has_start = self.source_start is not None
        has_end = self.source_end is not None
        if has_start != has_end:
            raise CanonForkContractError(
                "invalid_citation_offset",
                "source_start and source_end must be provided together",
            )
        if has_start and has_end:
            assert self.source_end is not None and self.source_start is not None
            if self.source_end <= self.source_start:
                raise CanonForkContractError(
                    "invalid_citation_offset",
                    "source_end must be greater than source_start",
                )
        return self


class RevalidatedLeaf(StrictCanonModel):
    """Admitted evidence: the excerpt re-sliced and hash-replayed at the leaf."""

    leaf_key: EvidenceLeafKey
    cited_space: CanonSpace
    chapter_number: PositiveInt
    source_start: NonNegInt
    source_end: PositiveInt
    content_hash: Hash64
    source_snapshot_hash: Hash64
    excerpt: ContentText
    authority: CanonAuthority
    citation_policy: CanonCitationPolicy
    evidence_ref: dict


class CitationVerdict(StrictCanonModel):
    """Per-citation outcome: allowed leaf or auditable blocked reason."""

    leaf_key: EvidenceLeafKey
    allowed: StrictBool
    leaf: RevalidatedLeaf | None = None
    blocked_reason: CitationBlockedReason | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ResolvedLeaf:
    """Leaf evidence resolved by the cited-space namespace provider."""

    owner_id: int
    novel_id: int
    namespace: str
    version_key: str | None
    chapter_number: int
    content: str
    source_snapshot_hash: str


@runtime_checkable
class CitationLeafProvider(Protocol):
    """Namespace boundary for one *cited* space; resolves authorized leaves only."""

    async def resolve_leaf(
        self, session: AsyncSession, *, ref: CanonCitationRef, scope: CanonScope
    ) -> ResolvedLeaf | None: ...


class OriginalLeafProvider:
    """Original Canon leaf provider: an owned novel chapter leaf.

    The leaf's source snapshot hash is recomputed from the current chapter set
    so any drift since the scope was frozen fails closed at the snapshot gate.
    """

    async def resolve_leaf(
        self, session: AsyncSession, *, ref: CanonCitationRef, scope: CanonScope
    ) -> ResolvedLeaf | None:
        if ref.cited_namespace != FORK_LEAF_NAMESPACE:
            return None
        if not ref.leaf_key.startswith("chapter:"):
            return None
        chapter_number = ref.chapter_number or _parse_chapter_leaf(ref.leaf_key)
        if chapter_number is None or chapter_number < 1:
            return None
        chapter = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(
                Chapter.novel_id == scope.novel_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            return None
        _, chapters = await CanonForkSnapshotService(session).load_source_snapshot(
            owner_id=scope.owner_id, novel_id=scope.novel_id
        )
        return ResolvedLeaf(
            owner_id=scope.owner_id,
            novel_id=scope.novel_id,
            namespace=FORK_LEAF_NAMESPACE,
            version_key=None,
            chapter_number=chapter_number,
            content=chapter.content or "",
            source_snapshot_hash=compute_source_snapshot_hash(
                owner_id=scope.owner_id,
                novel_id=scope.novel_id,
                chapters=chapters,
            ),
        )


class InterpretationLeafProvider:
    """User Interpretation leaf provider: one accepted artifact row."""

    async def resolve_leaf(
        self, session: AsyncSession, *, ref: CanonCitationRef, scope: CanonScope
    ) -> ResolvedLeaf | None:
        if not ref.leaf_key.startswith("artifact:"):
            return None
        try:
            artifact_id = int(ref.leaf_key.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
        artifact = await session.get(CanonSpaceArtifact, artifact_id)
        if artifact is None:
            return None
        return ResolvedLeaf(
            owner_id=artifact.owner_id,
            novel_id=artifact.novel_id,
            namespace=artifact.namespace,
            version_key=artifact.version_key,
            chapter_number=artifact.through_chapter,
            content=artifact.content or "",
            source_snapshot_hash=artifact.source_snapshot_hash,
        )


class FanfictionLeafProvider:
    """Fanfiction Canon leaf provider: a fork's frozen citation-lineage leaf.

    Only leaves frozen in the fork's own citation lineage resolve; the fork is
    looked up by the frozen scope's version key (the fork key), so no other
    branch can be cited (T-35-03-02).
    """

    async def resolve_leaf(
        self, session: AsyncSession, *, ref: CanonCitationRef, scope: CanonScope
    ) -> ResolvedLeaf | None:
        if ref.cited_namespace != f"fork:{scope.version_key}":
            return None
        fork = await session.scalar(
            select(CanonFork).where(
                CanonFork.owner_id == scope.owner_id,
                CanonFork.novel_id == scope.novel_id,
                CanonFork.fork_key == scope.version_key,
                CanonFork.space == CANON_FORK_SPACE,
            )
        )
        if fork is None:
            return None
        lineage = {leaf.get("leaf_key"): leaf for leaf in (fork.citation_lineage or [])}
        leaf = lineage.get(ref.leaf_key)
        if leaf is None:
            return None
        chapter_number = leaf.get("chapter_number")
        if not isinstance(chapter_number, int) or chapter_number < 1:
            return None
        chapter = await session.scalar(
            select(Chapter)
            .options(undefer(Chapter.content))
            .where(
                Chapter.novel_id == scope.novel_id,
                Chapter.chapter_number == chapter_number,
            )
        )
        if chapter is None:
            return None
        return ResolvedLeaf(
            owner_id=fork.owner_id,
            novel_id=fork.novel_id,
            namespace=f"fork:{fork.fork_key}",
            version_key=fork.fork_key,
            chapter_number=chapter_number,
            content=chapter.content or "",
            source_snapshot_hash=leaf.get("source_snapshot_hash", ""),
        )


def leaf_provider_for(cited_space: CanonSpace) -> CitationLeafProvider:
    """Dispatch to the single provider for the cited space (fail closed)."""
    if cited_space is CanonSpace.ORIGINAL_CANON:
        return OriginalLeafProvider()
    if cited_space is CanonSpace.USER_INTERPRETATION:
        return InterpretationLeafProvider()
    if cited_space is CanonSpace.FANFICTION_CANON:
        return FanfictionLeafProvider()
    raise CanonForkContractError(
        "unknown_space", f"unsupported knowledge space: {cited_space}"
    )


# ---------------------------------------------------------------------------
# Pure revalidation helpers (deterministic, DB-free, unit-testable)
# ---------------------------------------------------------------------------


def authorized_citation_namespaces(scope: CanonScope) -> frozenset[str]:
    """Namespaces the frozen scope may legitimately cite (D-35-01)."""
    allowed_spaces = CITATION_SOURCE_RULES[scope.space]
    namespaces = set()
    if CanonSpace.ORIGINAL_CANON in allowed_spaces:
        namespaces.add(FORK_LEAF_NAMESPACE)
    if scope.space in allowed_spaces:
        namespaces.add(scope.namespace)
    return frozenset(namespaces)


def effective_offsets(
    ref: CanonCitationRef, resolved: ResolvedLeaf
) -> tuple[int | None, int | None]:
    if ref.source_start is not None and ref.source_end is not None:
        return ref.source_start, ref.source_end
    return 0, len(resolved.content)


def revalidation_gate(
    ref: CanonCitationRef,
    scope: CanonScope,
    resolved: ResolvedLeaf,
) -> tuple[bool, CitationBlockedReason | None, str | None]:
    """Deterministic revalidation chain over an already-resolved leaf.

    Returns ``(allowed, blocked_reason, detail)``. The chain is fixed:
    owner -> novel -> fork/version -> cutoff -> snapshot replay.
    """
    if resolved.owner_id != scope.owner_id:
        return (
            False,
            CitationBlockedReason.OWNER_SCOPE,
            ("cited leaf owner is outside the frozen scope"),
        )
    if resolved.novel_id != scope.novel_id:
        return (
            False,
            CitationBlockedReason.NOVEL_SCOPE,
            ("cited leaf novel is outside the frozen scope"),
        )
    if resolved.namespace not in authorized_citation_namespaces(scope):
        return (
            False,
            CitationBlockedReason.FORK_VERSION_MISMATCH,
            (
                f"cited namespace {resolved.namespace!r} is not authorized by the "
                f"{scope.space.value} scope"
            ),
        )
    if resolved.version_key is not None and resolved.version_key != scope.version_key:
        return (
            False,
            CitationBlockedReason.FORK_VERSION_MISMATCH,
            (
                f"cited version {resolved.version_key!r} does not match the scope "
                f"version {scope.version_key!r}"
            ),
        )
    if resolved.chapter_number > scope.through_chapter:
        return (
            False,
            CitationBlockedReason.BEYOND_CUTOFF,
            (
                f"cited leaf chapter {resolved.chapter_number} is beyond the "
                f"server-derived cutoff {scope.through_chapter}"
            ),
        )
    if ref.source_snapshot_hash != resolved.source_snapshot_hash:
        return (
            False,
            CitationBlockedReason.STALE_HASH,
            (
                "citation source_snapshot_hash does not replay from the resolved "
                "leaf lineage"
            ),
        )
    if resolved.source_snapshot_hash != scope.source_snapshot_hash:
        return (
            False,
            CitationBlockedReason.STALE_HASH,
            ("cited leaf source snapshot does not replay from the frozen scope"),
        )
    return True, None, None


def slice_revalidation(
    ref: CanonCitationRef, resolved: ResolvedLeaf
) -> tuple[
    bool, CitationBlockedReason | None, str | None, tuple[int, int] | None, str | None
]:
    """Offset/hash replay over the resolved leaf content (T-35-03-02)."""
    start, end = effective_offsets(ref, resolved)
    if (
        start is None
        or end is None
        or start < 0
        or end <= start
        or end > len(resolved.content)
    ):
        return (
            False,
            CitationBlockedReason.INVALID_OFFSET,
            ("citation offsets are out of bounds for the resolved leaf"),
            None,
            None,
        )
    excerpt = resolved.content[start:end]
    if not excerpt:
        return (
            False,
            CitationBlockedReason.STALE_HASH,
            ("citation slice is empty"),
            None,
            None,
        )
    recomputed = content_sha256(excerpt)
    if recomputed != ref.content_hash:
        return (
            False,
            CitationBlockedReason.STALE_HASH,
            ("citation content_hash does not replay from the leaf slice"),
            None,
            None,
        )
    return True, None, None, (start, end), excerpt


# ---------------------------------------------------------------------------
# Citation service
# ---------------------------------------------------------------------------


class CanonCitationService:
    """Revalidates citations against a frozen scope; never fake empty success."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def revalidate(
        self,
        ref: CanonCitationRef,
        *,
        scope: CanonScope,
        provider: CitationLeafProvider | None = None,
    ) -> CitationVerdict:
        # 1. citation policy gate (D-35-01): the cited space must be authorized.
        try:
            assert_citation_authority(scope.space, ref.cited_space)
        except CanonForkContractError as exc:
            return _blocked(ref, CitationBlockedReason.CITATION_SCOPE, exc.detail)

        if provider is None:
            provider = leaf_provider_for(ref.cited_space)
        if self._session is None:
            raise CanonForkContractError(
                "missing_session", "citation revalidation requires a database session"
            )
        resolved = await provider.resolve_leaf(self._session, ref=ref, scope=scope)
        if resolved is None:
            return _blocked(
                ref,
                CitationBlockedReason.UNKNOWN_LEAF,
                f"no {ref.cited_space.value} leaf {ref.leaf_key!r} resolves in "
                f"{ref.cited_namespace!r}",
            )

        allowed, reason, detail = revalidation_gate(ref, scope, resolved)
        if not allowed:
            assert reason is not None
            return _blocked(ref, reason, detail)

        ok, reason, detail, offsets, excerpt = slice_revalidation(ref, resolved)
        if not ok:
            assert reason is not None
            return _blocked(ref, reason, detail)
        assert offsets is not None and excerpt is not None
        start, end = offsets

        return CitationVerdict(
            leaf_key=ref.leaf_key,
            allowed=True,
            leaf=RevalidatedLeaf(
                leaf_key=ref.leaf_key,
                cited_space=ref.cited_space,
                chapter_number=resolved.chapter_number,
                source_start=start,
                source_end=end,
                content_hash=ref.content_hash,
                source_snapshot_hash=resolved.source_snapshot_hash,
                excerpt=excerpt,
                authority=expected_authority(scope.space),
                citation_policy=expected_citation_policy(scope.space),
                evidence_ref={
                    "cited_space": ref.cited_space.value,
                    "cited_namespace": resolved.namespace,
                    "leaf_key": ref.leaf_key,
                    "chapter_number": resolved.chapter_number,
                    "source_start": start,
                    "source_end": end,
                    "content_hash": ref.content_hash,
                    "source_snapshot_hash": resolved.source_snapshot_hash,
                },
            ),
        )

    async def revalidate_many(
        self,
        refs: list[CanonCitationRef],
        *,
        scope: CanonScope,
    ) -> tuple[CitationVerdict, ...]:
        """Revalidate a batch; each verdict is independent and auditable."""
        return tuple(
            await asyncio.gather(*(self.revalidate(ref, scope=scope) for ref in refs))
        )


def _blocked(
    ref: CanonCitationRef,
    reason: CitationBlockedReason,
    detail: str | None,
) -> CitationVerdict:
    return CitationVerdict(
        leaf_key=ref.leaf_key,
        allowed=False,
        blocked_reason=reason,
        detail=detail,
    )


def _parse_chapter_leaf(leaf_key: str) -> int | None:
    if not leaf_key.startswith("chapter:"):
        return None
    try:
        return int(leaf_key.split(":", 1)[1])
    except (ValueError, IndexError):
        return None


__all__ = [
    "CanonCitationRef",
    "CanonCitationService",
    "CitationBlockedReason",
    "CitationLeafProvider",
    "CitationVerdict",
    "FanfictionLeafProvider",
    "InterpretationLeafProvider",
    "OriginalLeafProvider",
    "RevalidatedLeaf",
    "ResolvedLeaf",
    "authorized_citation_namespaces",
    "effective_offsets",
    "leaf_provider_for",
    "revalidation_gate",
    "slice_revalidation",
]
