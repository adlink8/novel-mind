"""Isolated branch-aware retrieval for the three knowledge spaces (Phase 35-03).

REQ-FORK-01 / REQ-CRE-01 / D-35-01: retrieval is isolated by
space/owner/novel/namespace/version/cutoff/snapshot. D-35-03: the scope is
frozen *before* any candidate is loaded or ranked. Original Canon, User
Interpretation and Fanfiction Canon each use an independent namespace/index
adapter; a cross-space candidate, a future leaf (beyond the server-derived
cutoff) or a stale snapshot hash never enters a result (T-35-03-01). An empty
dimension is reported as ``absent`` or ``blocked`` — never as a fake successful
empty array (REQ-CRE-01 pitfall #4).

Chain (scope-before-ranking):

    frozen CanonScope -> space adapter (independent namespace/index)
    -> cutoff predicate -> snapshot replay predicate -> rank -> result + trace

The trace records the count of every scope gate so an auditor can prove the
filters ran before any ranking and that no future metadata leaked into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.canon_fork import CanonFork
from app.models.canon_space import CanonSpaceArtifact
from app.models.novel import Chapter
from app.models.user import User
from app.services.canon_fork.contracts import (
    CanonAuthority,
    CanonCitationPolicy,
    CanonForkContractError,
    CanonScope,
    CanonSpace,
    build_scope,
    expected_authority,
    expected_citation_policy,
    validate_scope,
)
from app.services.canon_fork.lineage import (
    FORK_LEAF_NAMESPACE,
    build_leaf_lineage,
    lineage_payload,
)
from app.services.canon_fork.snapshot import (
    CANON_FORK_SPACE,
    CanonForkScopeError,
    CanonForkSnapshotService,
    ForkChapterRecord,
    chapter_content_hash,
    compute_cutoff_snapshot_hash,
    compute_source_snapshot_hash,
    resolve_cutoff,
)

CANON_RETRIEVAL_SCHEMA_VERSION = "canon-fork.v1:retrieval"


class RetrievalStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ABSENT = "absent"


class RetrievalBlockReason(StrEnum):
    UNKNOWN_SPACE = "unknown_space"
    STALE_SNAPSHOT = "stale_snapshot"
    BEYOND_CUTOFF = "beyond_cutoff"
    UNSEALED = "unsealed"
    EMPTY_SOURCE = "empty_source"


@dataclass(frozen=True)
class CanonIndexRecord:
    """One raw candidate loaded from a single space namespace/index adapter.

    ``chapter_number`` is the leaf's source chapter (for original/fork leaves)
    or the artifact's own server-derived cutoff (for interpretation rows); the
    cutoff predicate compares it against the frozen scope cutoff.
    """

    candidate_key: str
    chapter_number: int
    content_hash: str
    source_snapshot_hash: str
    artifact_id: int | None = None
    namespace: str = ""
    version_key: str = ""


@dataclass(frozen=True)
class CanonRetrievalCandidate:
    """One admitted candidate carrying authority, lineage and evidence refs."""

    candidate_key: str
    space: CanonSpace
    namespace: str
    version_key: str
    authority: CanonAuthority
    citation_policy: CanonCitationPolicy
    source_snapshot_hash: str
    chapter_number: int
    content_hash: str
    artifact_id: int | None
    evidence_ref: dict


@dataclass(frozen=True)
class CanonRetrievalTrace:
    """Auditable proof that scope filters ran before any ranking.

    The trace exposes only counts and scope identity — it never carries a
    candidate key, content hash or future chapter number (no leak, T-35-03-01).
    """

    scope_hash: str
    space: CanonSpace
    namespace: str
    version_key: str
    through_chapter: int
    loaded_scoped_count: int
    beyond_cutoff_count: int
    stale_snapshot_count: int
    ranked_count: int
    status: RetrievalStatus
    block_reason: RetrievalBlockReason | None = None


@dataclass(frozen=True)
class CanonRetrievalResult:
    candidates: tuple[CanonRetrievalCandidate, ...]
    trace: CanonRetrievalTrace


# ---------------------------------------------------------------------------
# Pure scope predicates (deterministic, DB-free, unit-testable)
# ---------------------------------------------------------------------------


def within_cutoff(scope: CanonScope, chapter_number: int) -> bool:
    """Cutoff predicate: a future leaf can never pass (T-35-03-01)."""
    return (
        isinstance(chapter_number, int)
        and chapter_number >= 1
        and chapter_number <= scope.through_chapter
    )


def snapshot_replays(scope: CanonScope, source_snapshot_hash: str) -> bool:
    """Snapshot predicate: a stale source hash can never pass (D-35-03)."""
    return source_snapshot_hash == scope.source_snapshot_hash


def _rank_key(candidate: CanonIndexRecord) -> tuple:
    # Ranking happens strictly after the scope gates; it is deterministic and
    # never consults un-filtered candidates (scope-before-ranking).
    return (candidate.chapter_number, candidate.candidate_key)


def filter_and_rank(
    scope: CanonScope, records: list[CanonIndexRecord]
) -> tuple[list[CanonIndexRecord], int, int]:
    """Apply cutoff then snapshot replay predicates, then rank.

    The order is fixed and provable: (1) cutoff filter, (2) snapshot filter,
    (3) sort. A future leaf or stale hash is dropped before any ranking. The
    two returned counts are the number of records dropped by each gate.
    """
    within = [r for r in records if within_cutoff(scope, r.chapter_number)]
    beyond_cutoff_count = len(records) - len(within)
    replayed = [r for r in within if snapshot_replays(scope, r.source_snapshot_hash)]
    stale_count = len(within) - len(replayed)
    ranked = sorted(replayed, key=_rank_key)
    return ranked, beyond_cutoff_count, stale_count


# ---------------------------------------------------------------------------
# Independent namespace/index adapters (one per knowledge space, D-35-01)
# ---------------------------------------------------------------------------


@runtime_checkable
class CanonSpaceIndexAdapter(Protocol):
    """Independent namespace/index boundary for exactly one knowledge space.

    Each adapter loads candidates only from its own space's namespace/index;
    there is no shared unscoped collection (anti-pattern: metadata-after-filter).
    """

    space: CanonSpace

    async def load_scoped_candidates(
        self, session: AsyncSession, *, scope: CanonScope
    ) -> list[CanonIndexRecord]: ...


class OriginalCanonIndexAdapter:
    """Original Canon index adapter: the owned novel's chapter leaves.

    The index is the novel's authoritative chapter set. The adapter recomputes
    the deterministic source snapshot hash from the *current* chapters so a
    stale scope snapshot fails closed at the snapshot predicate.
    """

    space = CanonSpace.ORIGINAL_CANON

    async def load_scoped_candidates(
        self, session: AsyncSession, *, scope: CanonScope
    ) -> list[CanonIndexRecord]:
        rows = list(
            (
                await session.scalars(
                    select(Chapter)
                    .options(undefer(Chapter.content))
                    .where(Chapter.novel_id == scope.novel_id)
                    .order_by(Chapter.chapter_number.asc())
                )
            ).all()
        )
        if not rows:
            return []
        records = tuple(
            ForkChapterRecord(
                chapter_id=row.id,
                chapter_number=row.chapter_number,
                content=row.content or "",
            )
            for row in rows
        )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=scope.owner_id, novel_id=scope.novel_id, chapters=records
        )
        return [
            CanonIndexRecord(
                candidate_key=f"original:chapter:{record.chapter_number}",
                chapter_number=record.chapter_number,
                content_hash=chapter_content_hash(record.content),
                source_snapshot_hash=snapshot_hash,
                artifact_id=record.chapter_id,
                namespace=FORK_LEAF_NAMESPACE,
                version_key="",
            )
            for record in records
        ]


class UserInterpretationIndexAdapter:
    """User Interpretation index adapter: accepted artifacts in the space."""

    space = CanonSpace.USER_INTERPRETATION

    async def load_scoped_candidates(
        self, session: AsyncSession, *, scope: CanonScope
    ) -> list[CanonIndexRecord]:
        rows = list(
            (
                await session.scalars(
                    select(CanonSpaceArtifact).where(
                        CanonSpaceArtifact.owner_id == scope.owner_id,
                        CanonSpaceArtifact.novel_id == scope.novel_id,
                        CanonSpaceArtifact.space
                        == CanonSpace.USER_INTERPRETATION.value,
                        CanonSpaceArtifact.namespace == scope.namespace,
                        CanonSpaceArtifact.version_key == scope.version_key,
                        CanonSpaceArtifact.status == "accepted",
                    )
                )
            ).all()
        )
        return [
            CanonIndexRecord(
                candidate_key=f"interpretation:artifact:{row.id}",
                chapter_number=row.through_chapter,
                content_hash=row.content_hash,
                source_snapshot_hash=row.source_snapshot_hash,
                artifact_id=row.id,
                namespace=row.namespace,
                version_key=row.version_key,
            )
            for row in rows
        ]


class FanfictionCanonIndexAdapter:
    """Fanfiction Canon index adapter: a fork's frozen citation-lineage leaves.

    The index is the fork's sealed citation lineage — the source-leaf evidence
    the derivative is bound to. Only the fork matching the frozen scope's
    version key is touched; no other branch can leak in.
    """

    space = CanonSpace.FANFICTION_CANON

    async def load_scoped_candidates(
        self, session: AsyncSession, *, scope: CanonScope
    ) -> list[CanonIndexRecord]:
        fork = await session.scalar(
            select(CanonFork).where(
                CanonFork.owner_id == scope.owner_id,
                CanonFork.novel_id == scope.novel_id,
                CanonFork.fork_key == scope.version_key,
                CanonFork.space == CANON_FORK_SPACE,
            )
        )
        if fork is None:
            return []
        records: list[CanonIndexRecord] = []
        for leaf in fork.citation_lineage or []:
            chapter_number = leaf.get("chapter_number")
            if not isinstance(chapter_number, int):
                continue
            records.append(
                CanonIndexRecord(
                    candidate_key=f"fork:{fork.fork_key}:{leaf.get('leaf_key', '')}",
                    chapter_number=chapter_number,
                    content_hash=leaf.get("content_hash", ""),
                    source_snapshot_hash=leaf.get("source_snapshot_hash", ""),
                    artifact_id=fork.id,
                    namespace=f"fork:{fork.fork_key}",
                    version_key=fork.fork_key,
                )
            )
        return records


def index_adapter_for(space: CanonSpace) -> CanonSpaceIndexAdapter:
    """Dispatch to the single adapter for the frozen space (fail closed)."""
    if space is CanonSpace.ORIGINAL_CANON:
        return OriginalCanonIndexAdapter()
    if space is CanonSpace.USER_INTERPRETATION:
        return UserInterpretationIndexAdapter()
    if space is CanonSpace.FANFICTION_CANON:
        return FanfictionCanonIndexAdapter()
    raise CanonForkContractError(
        "unknown_space", f"unsupported knowledge space: {space}"
    )


# ---------------------------------------------------------------------------
# Retrieval service (scope-before-ranking orchestration)
# ---------------------------------------------------------------------------


class CanonRetrievalService:
    """Read-only retrieval inside one frozen knowledge-space scope.

    Never writes, never selects an active pointer and never ranks before the
    scope gates have run. An empty namespace is ``absent``; rows that are all
    inadmissible (future or stale) are ``blocked`` with an auditable reason.
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def retrieve(
        self,
        scope: CanonScope,
        *,
        adapter: CanonSpaceIndexAdapter | None = None,
    ) -> CanonRetrievalResult:
        validate_scope(scope)
        if adapter is None:
            adapter = index_adapter_for(scope.space)
        if self._session is None:
            raise CanonForkContractError(
                "missing_session", "retrieval requires a database session"
            )

        records = await adapter.load_scoped_candidates(self._session, scope=scope)
        ranked, beyond_cutoff, stale = filter_and_rank(scope, records)

        if ranked:
            status = RetrievalStatus.COMPLETED
            block_reason = None
        elif records:
            # Rows existed in the namespace but none were admissible: this is a
            # hard blocked outcome, never a fake successful empty array.
            status = RetrievalStatus.BLOCKED
            block_reason = (
                RetrievalBlockReason.BEYOND_CUTOFF
                if beyond_cutoff > 0
                else RetrievalBlockReason.STALE_SNAPSHOT
            )
        else:
            status = RetrievalStatus.ABSENT
            block_reason = None

        authority = expected_authority(scope.space)
        citation_policy = expected_citation_policy(scope.space)
        candidates = tuple(
            CanonRetrievalCandidate(
                candidate_key=record.candidate_key,
                space=scope.space,
                namespace=record.namespace or scope.namespace,
                version_key=record.version_key or scope.version_key,
                authority=authority,
                citation_policy=citation_policy,
                source_snapshot_hash=record.source_snapshot_hash,
                chapter_number=record.chapter_number,
                content_hash=record.content_hash,
                artifact_id=record.artifact_id,
                evidence_ref={
                    "candidate_key": record.candidate_key,
                    "namespace": record.namespace or scope.namespace,
                    "version_key": record.version_key or scope.version_key,
                    "chapter_number": record.chapter_number,
                    "content_hash": record.content_hash,
                    "source_snapshot_hash": record.source_snapshot_hash,
                    "artifact_id": record.artifact_id,
                },
            )
            for record in ranked
        )
        trace = CanonRetrievalTrace(
            scope_hash=scope.scope_hash(),
            space=scope.space,
            namespace=scope.namespace,
            version_key=scope.version_key,
            through_chapter=scope.through_chapter,
            loaded_scoped_count=len(records),
            beyond_cutoff_count=beyond_cutoff,
            stale_snapshot_count=stale,
            ranked_count=len(ranked),
            status=status,
            block_reason=block_reason,
        )
        return CanonRetrievalResult(candidates=candidates, trace=trace)


# ---------------------------------------------------------------------------
# Server-derived scope resolution (the client can never widen the scope)
# ---------------------------------------------------------------------------


async def resolve_canon_scope(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    user: User,
    space: CanonSpace,
    namespace: str | None = None,
    version_key: str | None = None,
    fork_id: int | None = None,
    through_chapter: int | None = None,
    full_book: bool = False,
    expected_source_snapshot_hash: str | None = None,
) -> CanonScope:
    """Freeze a retrieval scope from server-side authority only.

    owner/novel always come from the authenticated owner dependency; the fork
    snapshot, the Original Canon version and the effective cutoff are derived on
    the server (D-35-03, T-35-02-01). A future cutoff, an unauthorized full-book
    request or a stale expected snapshot fails closed before any retrieval.
    """
    snapshot_service = CanonForkSnapshotService(session)

    if space is CanonSpace.FANFICTION_CANON:
        fork_query = select(CanonFork).where(
            CanonFork.owner_id == owner_id,
            CanonFork.novel_id == novel_id,
            CanonFork.space == CANON_FORK_SPACE,
        )
        if fork_id is not None:
            fork_query = fork_query.where(CanonFork.id == fork_id)
        elif version_key:
            fork_query = fork_query.where(CanonFork.fork_key == version_key)
        else:
            raise CanonForkScopeError(
                "missing_fork", "fanfiction retrieval requires fork_id or fork_key"
            )
        fork = await session.scalar(fork_query)
        if fork is None:
            raise CanonForkScopeError(
                "fork_not_found",
                "no sealed fork resolves for the requested fanfiction branch",
                status_code=404,
            )
        effective_cutoff = (
            through_chapter if through_chapter is not None else fork.through_chapter
        )
        if effective_cutoff > fork.through_chapter:
            raise CanonForkScopeError(
                "cutoff_exceeds_scope",
                f"requested cutoff {effective_cutoff} exceeds the frozen fork "
                f"cutoff {fork.through_chapter}; a future cutoff cannot expand "
                "the fork scope",
            )
        if full_book and not fork.full_book_authorized:
            raise CanonForkScopeError(
                "full_book_requires_authorization",
                "full-book cutoff requires explicit server-side authorization; "
                "an unauthorized client cannot elevate the scope",
                status_code=403,
            )
        lineage = [
            leaf
            for leaf in (fork.citation_lineage or [])
            if leaf.get("chapter_number", 0) <= effective_cutoff
        ]
        cutoff_snapshot_hash = compute_cutoff_snapshot_hash(
            source_snapshot_hash=fork.source_snapshot_hash,
            through_chapter=effective_cutoff,
            lineage=lineage,
        )
        return build_scope(
            owner_id=owner_id,
            novel_id=novel_id,
            space=CANON_FORK_SPACE,
            namespace=f"fork:{fork.fork_key}",
            version_key=fork.fork_key,
            source_snapshot_hash=fork.source_snapshot_hash,
            through_chapter=effective_cutoff,
            cutoff_snapshot_hash=cutoff_snapshot_hash,
            full_book_authorized=fork.full_book_authorized,
        )

    if space is CanonSpace.ORIGINAL_CANON:
        snapshot_hash, chapters = await snapshot_service.load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        if expected_source_snapshot_hash is not None:
            if expected_source_snapshot_hash != snapshot_hash:
                raise CanonForkScopeError(
                    "stale_source_snapshot",
                    "expected_source_snapshot_hash does not replay from the "
                    "novel's current chapter set; the source changed since the "
                    "expected snapshot",
                    status_code=409,
                )
        if not chapters:
            raise CanonForkScopeError(
                "empty_source_snapshot",
                "the novel has no chapters; original retrieval needs a "
                "non-empty source",
                status_code=409,
            )
        cutoff = resolve_cutoff(
            user=user,
            requested_cutoff_chapter=through_chapter,
            full_book_requested=full_book,
            novel_chapter_count=len(chapters),
        )
        content_hashes = {
            record.chapter_number: chapter_content_hash(record.content)
            for record in chapters
        }
        leaves = build_leaf_lineage(
            source_snapshot_hash=snapshot_hash,
            chapter_numbers=[record.chapter_number for record in chapters],
            content_hashes=content_hashes,
            through_chapter=cutoff.through_chapter,
        )
        cutoff_snapshot_hash = compute_cutoff_snapshot_hash(
            source_snapshot_hash=snapshot_hash,
            through_chapter=cutoff.through_chapter,
            lineage=lineage_payload(leaves),
        )
        source_version_key = await snapshot_service.resolve_source_version_key(
            owner_id=owner_id, novel_id=novel_id, source_snapshot_hash=snapshot_hash
        )
        return build_scope(
            owner_id=owner_id,
            novel_id=novel_id,
            space=CanonSpace.ORIGINAL_CANON.value,
            namespace=FORK_LEAF_NAMESPACE,
            version_key=source_version_key,
            source_snapshot_hash=snapshot_hash,
            through_chapter=cutoff.through_chapter,
            cutoff_snapshot_hash=cutoff_snapshot_hash,
            full_book_authorized=cutoff.full_book_authorized,
        )

    if space is CanonSpace.USER_INTERPRETATION:
        if not namespace or not namespace.strip():
            raise CanonForkScopeError(
                "missing_namespace",
                "user_interpretation retrieval requires a namespace",
            )
        artifact_query = select(CanonSpaceArtifact).where(
            CanonSpaceArtifact.owner_id == owner_id,
            CanonSpaceArtifact.novel_id == novel_id,
            CanonSpaceArtifact.space == CanonSpace.USER_INTERPRETATION.value,
            CanonSpaceArtifact.namespace == namespace,
            CanonSpaceArtifact.status == "accepted",
        )
        if version_key is not None:
            artifact_query = artifact_query.where(
                CanonSpaceArtifact.version_key == version_key
            )
        artifact = await session.scalar(
            artifact_query.order_by(CanonSpaceArtifact.id.desc())
        )
        if artifact is None:
            raise CanonForkScopeError(
                "interpretation_artifact_not_found",
                "no accepted interpretation artifact resolves for the requested "
                "namespace/version",
                status_code=404,
            )
        effective_cutoff = (
            through_chapter if through_chapter is not None else artifact.through_chapter
        )
        if effective_cutoff > artifact.through_chapter:
            raise CanonForkScopeError(
                "cutoff_exceeds_scope",
                f"requested cutoff {effective_cutoff} exceeds the frozen "
                f"artifact cutoff {artifact.through_chapter}",
            )
        if full_book and not artifact.full_book_authorized:
            raise CanonForkScopeError(
                "full_book_requires_authorization",
                "full-book cutoff requires explicit server-side authorization; "
                "an unauthorized client cannot elevate the scope",
                status_code=403,
            )
        cutoff_snapshot_hash = compute_cutoff_snapshot_hash(
            source_snapshot_hash=artifact.source_snapshot_hash,
            through_chapter=effective_cutoff,
            lineage=[
                {
                    "leaf_key": f"chapter:{effective_cutoff}",
                    "chapter_number": effective_cutoff,
                    "content_hash": artifact.content_hash,
                    "source_snapshot_hash": artifact.source_snapshot_hash,
                }
            ],
        )
        return build_scope(
            owner_id=owner_id,
            novel_id=novel_id,
            space=CanonSpace.USER_INTERPRETATION.value,
            namespace=namespace,
            version_key=artifact.version_key,
            source_snapshot_hash=artifact.source_snapshot_hash,
            through_chapter=effective_cutoff,
            cutoff_snapshot_hash=cutoff_snapshot_hash,
            full_book_authorized=artifact.full_book_authorized,
        )

    raise CanonForkScopeError("unknown_space", f"unsupported knowledge space: {space}")


__all__ = [
    "CANON_RETRIEVAL_SCHEMA_VERSION",
    "CanonIndexRecord",
    "CanonRetrievalCandidate",
    "CanonRetrievalResult",
    "CanonRetrievalService",
    "CanonRetrievalTrace",
    "CanonSpaceIndexAdapter",
    "FanfictionCanonIndexAdapter",
    "OriginalCanonIndexAdapter",
    "RetrievalBlockReason",
    "RetrievalStatus",
    "UserInterpretationIndexAdapter",
    "filter_and_rank",
    "index_adapter_for",
    "resolve_canon_scope",
    "snapshot_replays",
    "within_cutoff",
]
