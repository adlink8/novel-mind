"""Server-derived frozen fork snapshot (Phase 35-02, D-35-03).

A Canon Fork is created by freezing, on the server:

- ``owner`` / ``novel``: always taken from the authenticated owner dependency,
  never from the client (T-35-02-01 elevation of privilege is mitigated by the
  server-derived scope).
- Original Canon ``source_version_key``: the latest ``original_canon`` artifact
  version for the owner/novel (deterministic fallback when none exists).
- ``source_snapshot_hash`` / ``source_snapshot_id``: a deterministic content
  address over the novel's current chapter set; a client-supplied expected hash
  that does not replay fails closed (stale source snapshot).
- spoiler ``cutoff``: computed by the server from owner/novel and explicit
  authorization. A future cutoff can never expand the scope, and the
  ``full_book_authorized`` flag cannot be elevated by an unauthorized client.
- ``citation_lineage``: frozen source-leaf provenance at or below the cutoff.
- deterministic ``scope_hash`` / ``manifest_hash``: identical input replays the
  identical manifest hash (byte-replayable lineage).

Mutation only persists a candidate fork row with ``active=false``; no
production active pointer is ever created or switched (no cutover).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.canon_fork import CANON_FORK_SPACE, CANON_FORK_STATUSES, CanonFork
from app.models.canon_space import CanonSpaceArtifact
from app.models.novel import Chapter
from app.models.user import User
from app.services.canon_fork.contracts import build_scope
from app.services.canon_fork.lineage import (
    CANON_FORK_SCHEMA_VERSION,
    build_leaf_lineage,
    canonical_lineage_hash,
    lineage_hash,
    lineage_payload,
    validate_leaf_lineage,
)

CANON_FORK_MANIFEST_PREFIX = "canon-fork.v1:manifest"
CANON_FORK_SCOPE_PREFIX = "canon-fork.v1:scope"
CANON_FORK_CUTOFF_PREFIX = "canon-fork.v1:cutoff"
DEFAULT_SOURCE_VERSION_PREFIX = "original"


class CanonForkScopeError(ValueError):
    """Machine-readable fail-closed rejection from the fork scope boundary."""

    def __init__(self, code: str, detail: str, *, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ForkChapterRecord:
    """One frozen chapter text record loaded fresh from the owning novel."""

    chapter_id: int
    chapter_number: int
    content: str


@dataclass(frozen=True)
class CutoffResolution:
    """Server-derived cutoff and its auditable authorization record."""

    through_chapter: int
    full_book_authorized: bool
    authorization: dict


def chapter_content_hash(content: str) -> str:
    """Deterministic 64-hex content hash of one chapter body."""
    return sha256(content.encode("utf-8")).hexdigest()


def canonical_fork_hash(payload: dict) -> str:
    """Byte-replayable canonical hash for fork lineage records."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(
        f"{CANON_FORK_MANIFEST_PREFIX}\n{encoded}".encode("utf-8")
    ).hexdigest()


def compute_source_snapshot_hash(
    *, owner_id: int, novel_id: int, chapters: tuple[ForkChapterRecord, ...]
) -> str:
    """Deterministic content address of a novel's current chapter set.

    The address binds owner/novel scope so two novels with identical chapter
    text still have distinct snapshot lineage; replay recomputes it so any
    chapter drift fails closed (stale snapshot lineage).
    """
    records = [
        {
            "chapter_number": record.chapter_number,
            "content_hash": chapter_content_hash(record.content),
        }
        for record in sorted(chapters, key=lambda c: c.chapter_number)
    ]
    return canonical_fork_hash(
        {
            "kind": f"{CANON_FORK_SCHEMA_VERSION}:source_snapshot",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "chapters": records,
        }
    )


def resolve_cutoff(
    *,
    user: User,
    requested_cutoff_chapter: int | None,
    full_book_requested: bool,
    novel_chapter_count: int,
) -> CutoffResolution:
    """Server-derived cutoff; the client can never expand the authorized scope.

    - full-book elevation requires an explicit server-side authorization
      (superuser); otherwise it fails closed (403).
    - a future cutoff (beyond the novel's chapter count) fails closed unless it
      was an authorized full-book request, in which case it is clamped to the
      novel size.
    """
    if novel_chapter_count < 1:
        raise CanonForkScopeError(
            "empty_source_snapshot",
            "the novel has no chapters; a canon fork needs a non-empty source",
            status_code=409,
        )

    authorized_full_book = bool(getattr(user, "is_superuser", False))
    if full_book_requested and not authorized_full_book:
        raise CanonForkScopeError(
            "full_book_requires_authorization",
            "full-book cutoff requires explicit server-side authorization; "
            "an unauthorized client cannot elevate the scope",
            status_code=403,
        )

    requested = (
        requested_cutoff_chapter
        if requested_cutoff_chapter is not None
        else novel_chapter_count
    )
    if requested < 1:
        raise CanonForkScopeError(
            "invalid_cutoff", "requested_cutoff_chapter must be >= 1"
        )

    if requested > novel_chapter_count:
        if full_book_requested and authorized_full_book:
            through_chapter = novel_chapter_count
        else:
            raise CanonForkScopeError(
                "cutoff_exceeds_scope",
                f"requested cutoff {requested} exceeds the novel's chapter "
                f"count {novel_chapter_count}; a future cutoff cannot expand "
                "the fork scope",
            )
    else:
        through_chapter = requested

    full_book_authorized = bool(full_book_requested)
    authorization = {
        "source": (
            "server_superuser" if full_book_authorized else "server_chapter_limit"
        ),
        "requested_cutoff_chapter": requested_cutoff_chapter,
        "full_book_requested": full_book_requested,
        "novel_chapter_count": novel_chapter_count,
        "authorized_cutoff_chapter": through_chapter,
        "granted_full_book": full_book_authorized,
    }
    return CutoffResolution(
        through_chapter=through_chapter,
        full_book_authorized=full_book_authorized,
        authorization=authorization,
    )


@dataclass(frozen=True)
class CanonForkManifest:
    """The immutable sealed manifest of one candidate fork (D-35-03)."""

    schema_version: str
    owner_id: int
    novel_id: int
    fork_key: str
    space: str
    status: str
    source_version_key: str
    source_snapshot_id: str
    source_snapshot_hash: str
    through_chapter: int
    full_book_authorized: bool
    cutoff_snapshot_hash: str
    scope_hash: str
    manifest_hash: str
    citation_lineage: list[dict]
    authorization: dict
    active: bool = False

    def payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "owner_id": self.owner_id,
            "novel_id": self.novel_id,
            "fork_key": self.fork_key,
            "space": self.space,
            "status": self.status,
            "source_version_key": self.source_version_key,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "through_chapter": self.through_chapter,
            "full_book_authorized": self.full_book_authorized,
            "cutoff_snapshot_hash": self.cutoff_snapshot_hash,
            "scope_hash": self.scope_hash,
            "citation_lineage": self.citation_lineage,
            "authorization": self.authorization,
            "active": self.active,
        }

    def recompute_manifest_hash(self) -> str:
        return canonical_fork_hash(self.payload())


def build_canon_fork_manifest(
    *,
    owner_id: int,
    novel_id: int,
    fork_key: str,
    source_version_key: str,
    source_snapshot_id: str,
    source_snapshot_hash: str,
    through_chapter: int,
    full_book_authorized: bool,
    cutoff_snapshot_hash: str,
    citation_lineage: list[dict],
    authorization: dict,
    status: str = "candidate",
) -> CanonForkManifest:
    """Assemble the frozen manifest and seal deterministic scope/manifest hashes."""
    if status not in CANON_FORK_STATUSES:
        raise CanonForkScopeError(
            "invalid_status", f"unsupported fork status: {status}"
        )
    validate_leaf_lineage(
        citation_lineage,
        source_snapshot_hash=source_snapshot_hash,
        through_chapter=through_chapter,
    )

    scope = build_scope(
        owner_id=owner_id,
        novel_id=novel_id,
        space=CANON_FORK_SPACE,
        namespace=f"fork:{fork_key}",
        version_key=fork_key,
        source_snapshot_hash=source_snapshot_hash,
        through_chapter=through_chapter,
        cutoff_snapshot_hash=cutoff_snapshot_hash,
        full_book_authorized=full_book_authorized,
    )
    scope_hash = scope.scope_hash()
    manifest = CanonForkManifest(
        schema_version=CANON_FORK_SCHEMA_VERSION,
        owner_id=owner_id,
        novel_id=novel_id,
        fork_key=fork_key,
        space=CANON_FORK_SPACE,
        status=status,
        source_version_key=source_version_key,
        source_snapshot_id=source_snapshot_id,
        source_snapshot_hash=source_snapshot_hash,
        through_chapter=through_chapter,
        full_book_authorized=full_book_authorized,
        cutoff_snapshot_hash=cutoff_snapshot_hash,
        scope_hash=scope_hash,
        manifest_hash="",
        citation_lineage=citation_lineage,
        authorization=authorization,
        active=False,
    )
    sealed = CanonForkManifest(
        **{
            **manifest.payload(),
            "manifest_hash": canonical_fork_hash(manifest.payload()),
        }
    )
    return sealed


def compute_cutoff_snapshot_hash(
    *, source_snapshot_hash: str, through_chapter: int, lineage: list[dict]
) -> str:
    """Deterministic hash of the cutoff boundary and its visible leaves."""
    return canonical_lineage_hash(
        {
            "kind": f"{CANON_FORK_SCHEMA_VERSION}:cutoff",
            "source_snapshot_hash": source_snapshot_hash,
            "through_chapter": through_chapter,
            "leaf_hashes": lineage_hash(lineage),
        }
    )


class CanonForkSnapshotService:
    """Owner/novel-scoped fork snapshot seam; reads the novel as fresh authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_source_snapshot(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[str, tuple[ForkChapterRecord, ...]]:
        """Load all chapter bodies of the owned novel and hash the snapshot."""
        rows = (
            await self._session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == novel_id)
                .order_by(Chapter.chapter_number.asc())
            )
        ).all()
        chapters = tuple(
            ForkChapterRecord(
                chapter_id=row.id,
                chapter_number=row.chapter_number,
                content=row.content or "",
            )
            for row in rows
        )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=owner_id,
            novel_id=novel_id,
            chapters=chapters,
        )
        return snapshot_hash, chapters

    async def resolve_source_version_key(
        self, *, owner_id: int, novel_id: int, source_snapshot_hash: str
    ) -> str:
        """Latest Original Canon artifact version, or a deterministic fallback."""
        artifact = await self._session.scalar(
            select(CanonSpaceArtifact)
            .where(
                CanonSpaceArtifact.owner_id == owner_id,
                CanonSpaceArtifact.novel_id == novel_id,
                CanonSpaceArtifact.space == "original_canon",
            )
            .order_by(CanonSpaceArtifact.id.desc())
            .limit(1)
        )
        if artifact is not None:
            return artifact.version_key
        return f"{DEFAULT_SOURCE_VERSION_PREFIX}:{source_snapshot_hash[:16]}"

    async def freeze_manifest(
        self,
        *,
        owner_id: int,
        novel_id: int,
        user: User,
        fork_key: str,
        requested_cutoff_chapter: int | None,
        full_book_requested: bool,
        expected_source_snapshot_hash: str | None,
    ) -> CanonForkManifest:
        """Seal a candidate fork manifest from the server-derived scope."""
        if not fork_key or not fork_key.strip():
            raise CanonForkScopeError(
                "invalid_fork_key", "fork_key must be a non-empty identifier"
            )

        snapshot_hash, chapters = await self.load_source_snapshot(
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
                "the novel has no chapters; a canon fork needs a non-empty source",
                status_code=409,
            )

        cutoff = resolve_cutoff(
            user=user,
            requested_cutoff_chapter=requested_cutoff_chapter,
            full_book_requested=full_book_requested,
            novel_chapter_count=len(chapters),
        )

        source_version_key = await self.resolve_source_version_key(
            owner_id=owner_id,
            novel_id=novel_id,
            source_snapshot_hash=snapshot_hash,
        )
        source_snapshot_id = f"novel:{novel_id}:{snapshot_hash[:16]}"

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
        citation_lineage = lineage_payload(leaves)
        cutoff_snapshot_hash = compute_cutoff_snapshot_hash(
            source_snapshot_hash=snapshot_hash,
            through_chapter=cutoff.through_chapter,
            lineage=citation_lineage,
        )

        return build_canon_fork_manifest(
            owner_id=owner_id,
            novel_id=novel_id,
            fork_key=fork_key,
            source_version_key=source_version_key,
            source_snapshot_id=source_snapshot_id,
            source_snapshot_hash=snapshot_hash,
            through_chapter=cutoff.through_chapter,
            full_book_authorized=cutoff.full_book_authorized,
            cutoff_snapshot_hash=cutoff_snapshot_hash,
            citation_lineage=citation_lineage,
            authorization=cutoff.authorization,
        )

    async def persist_fork(
        self, *, manifest: CanonForkManifest
    ) -> tuple[CanonFork, bool]:
        """Persist one immutable candidate fork; identical input replays.

        Mutation only ever creates a candidate row (``active=false``). A
        conflicting retry of the same ``fork_key`` fails closed with 409; an
        identical replay returns the sealed row with ``replayed=True``.
        """
        existing = await self._session.scalar(
            select(CanonFork).where(
                CanonFork.owner_id == manifest.owner_id,
                CanonFork.novel_id == manifest.novel_id,
                CanonFork.fork_key == manifest.fork_key,
            )
        )
        if existing is not None:
            if existing.manifest_hash == manifest.manifest_hash:
                return existing, True
            raise CanonForkScopeError(
                "fork_key_conflict",
                f"fork_key {manifest.fork_key!r} is already sealed with a "
                "different frozen scope; a fork is immutable",
                status_code=409,
            )

        row = CanonFork(
            owner_id=manifest.owner_id,
            novel_id=manifest.novel_id,
            fork_key=manifest.fork_key,
            space=manifest.space,
            status=manifest.status,
            source_version_key=manifest.source_version_key,
            source_snapshot_id=manifest.source_snapshot_id,
            source_snapshot_hash=manifest.source_snapshot_hash,
            through_chapter=manifest.through_chapter,
            full_book_authorized=manifest.full_book_authorized,
            cutoff_snapshot_hash=manifest.cutoff_snapshot_hash,
            scope_hash=manifest.scope_hash,
            manifest_hash=manifest.manifest_hash,
            citation_lineage=manifest.citation_lineage,
            authorization=manifest.authorization,
            active=False,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row, False


__all__ = [
    "CANON_FORK_CUTOFF_PREFIX",
    "CANON_FORK_MANIFEST_PREFIX",
    "CANON_FORK_SCOPE_PREFIX",
    "CanonForkManifest",
    "CanonForkScopeError",
    "CanonForkSnapshotService",
    "CutoffResolution",
    "ForkChapterRecord",
    "build_canon_fork_manifest",
    "canonical_fork_hash",
    "chapter_content_hash",
    "compute_cutoff_snapshot_hash",
    "compute_source_snapshot_hash",
    "resolve_cutoff",
]
