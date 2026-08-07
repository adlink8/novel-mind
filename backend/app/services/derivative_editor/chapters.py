"""Owner-scoped derivative chapter plan service (Phase 36-02, D-36-02/D-36-03).

The service is the only owner-scoped transaction boundary for chapters:

- **Project scope (D-36-01):** every chapter is resolved inside the current
  owner + novel + project scope; a foreign/missing project or chapter is an
  identical 404 and no query keys on id alone.
- **Fanfiction-only drafts (D-36-03):** the chapter inherits the project's
  sealed ``fanfiction_canon`` space; the service never writes Original Canon or
  User Interpretation and exposes no publication state (Phase 39 owns that).
- **Ordered, replayable plan (D-36-02):** ``position`` is unique per project and
  stable; Markdown is deterministic-canonicalized (CRLF → LF, trailing-whitespace
  stripped) before storage so the SHA-256 ``markdown_checksum`` is replayable.
- **Optimistic concurrency:** every patch carries ``base_revision``; a stale
  write is rejected (409) with the current revision/checksum instead of
  overwriting newer content. Revision only bumps on a real Markdown change
  (semantic no-op detection), so unrelated title/status edits never cause a
  spurious conflict.
- **Explicit reorder:** reordering requires the exact full set of the project's
  current chapter ids (missing/extras/duplicates/foreign ids fail closed) and
  rewrites positions in one transaction.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_project import DerivativeProject
from app.models.derivative_chapter import (
    DERIVATIVE_CHAPTER_STATUSES,
    DerivativeChapter,
)
from app.models.derivative_revision import DerivativeRevision
from app.schemas.derivative_chapter import (
    DerivativeChapterPatch,
    DerivativeChapterReorderRequest,
    DerivativeChapterScope,
    DerivativeChapterStatus,
    DerivativeChapterView,
)


class DerivativeChapterError(ValueError):
    """Fail-closed chapter gate violation with an HTTP status code."""

    def __init__(self, code: str, detail: str, status_code: int = 400):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


# Temporary two-phase reorder offset: large enough that position+offset can never
# collide with another row's final position in a realistic plan (D-36-02).
_REORDER_OFFSET = 1_000_000


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeChapterError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


def canonicalize_markdown(text: str | None) -> str:
    """Deterministic Markdown canonical form (the checksum input, D-36-02).

    - CRLF / lone CR line endings are normalized to LF;
    - trailing whitespace is stripped from every line;
    - leading/trailing blank lines are removed.

    Empty / whitespace-only input canonicalizes to ``""`` so an empty draft and
    a ``"   "`` draft are the same no-op.
    """
    if text is None:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [line.rstrip() for line in lines]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def markdown_checksum(text: str | None) -> str:
    """SHA-256 hexdigest of the canonicalized Markdown (replayable, D-36-02)."""
    canonical = canonicalize_markdown(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _load_scoped_project(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> DerivativeProject:
    row = await db.scalar(
        select(DerivativeProject).where(
            DerivativeProject.id == project_id,
            DerivativeProject.owner_id == owner_id,
            DerivativeProject.novel_id == novel_id,
        )
    )
    if row is None:
        raise DerivativeChapterError(
            "project_not_found",
            "derivative project not found in the owner/novel scope",
            status_code=404,
        )
    return row


async def _load_scoped_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> DerivativeChapter:
    row = await db.scalar(
        select(DerivativeChapter).where(
            DerivativeChapter.id == chapter_id,
            DerivativeChapter.project_id == project_id,
            DerivativeChapter.owner_id == owner_id,
            DerivativeChapter.novel_id == novel_id,
        )
    )
    if row is None:
        raise DerivativeChapterError(
            "chapter_not_found",
            "derivative chapter not found in the owner/novel/project scope",
            status_code=404,
        )
    return row


def _require_writable_project(project: DerivativeProject) -> None:
    """Archived projects are read-only for chapter writes (soft close)."""
    if project.status == "archived":
        raise DerivativeChapterError(
            "project_archived",
            f"project {project.id} is archived; chapter writes are blocked",
            status_code=409,
        )


def _to_view(row: DerivativeChapter) -> DerivativeChapterView:
    return DerivativeChapterView(
        id=row.id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        position=row.position,
        title=row.title,
        markdown=row.markdown,
        markdown_checksum=row.markdown_checksum,
        status=DerivativeChapterStatus(row.status),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_scope(project: DerivativeProject) -> DerivativeChapterScope:
    # Echo the project's frozen fork/version/cutoff lineage (D-36-01).
    return DerivativeChapterScope(
        project_id=project.id,
        owner_id=project.owner_id,
        novel_id=project.novel_id,
        fork_id=project.fork_id,
        space=project.space,
        fork_key=project.fork_key,
        source_version_key=project.source_version_key,
        through_chapter=project.through_chapter,
        full_book_authorized=bool(project.full_book_authorized),
        cutoff_snapshot_hash=project.cutoff_snapshot_hash,
    )


async def _next_position(db: AsyncSession, *, project_id: int) -> int:
    """Append position: max(position)+1, or 0 for the first chapter."""
    current_max = await db.scalar(
        select(func.max(DerivativeChapter.position)).where(
            DerivativeChapter.project_id == project_id
        )
    )
    return 0 if current_max is None else current_max + 1


async def create_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    title: str,
    markdown: str = "",
    status: str | None = None,
) -> tuple[DerivativeChapterView, DerivativeChapterScope]:
    """Append one ordered chapter plan row to an owner-scoped project."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    title = (title or "").strip()
    if not title:
        raise DerivativeChapterError("invalid_title", "chapter title must be non-empty")

    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)

    canonical = canonicalize_markdown(markdown)
    row_status = status if status in DERIVATIVE_CHAPTER_STATUSES else "draft"

    # Append position = max(position)+1; on a (rare) concurrent create the
    # unique (project_id, position) constraint may fire — rebuild the row at the
    # next free slot (fresh instances keep the retry free of expired state).
    chapter: DerivativeChapter | None = None
    for _ in range(5):
        position = await _next_position(db, project_id=project.id)
        chapter = DerivativeChapter(
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project.id,
            position=position,
            title=title,
            markdown=canonical,
            markdown_checksum=markdown_checksum(canonical),
            status=row_status,
            revision=1,
        )
        db.add(chapter)
        try:
            await db.flush()
            break
        except IntegrityError as exc:
            await db.rollback()
            if "uq_derivative_chapters_position" not in str(exc):
                raise
    else:
        raise DerivativeChapterError(
            "position_conflict",
            "could not find a free chapter position after concurrent creates",
            status_code=409,
        )

    await db.refresh(chapter)
    # Seed the append-only revision lineage with the immutable root row
    # (D-36-02): the chapter's initial state is always recoverable and the
    # client's base_revision=1 maps deterministically to this row.
    db.add(
        DerivativeRevision(
            chapter_id=chapter.id,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project.id,
            revision_number=1,
            parent_revision_id=None,
            kind="create",
            content=canonical,
            content_checksum=markdown_checksum(canonical),
            actor_id=owner_id,
            approval_state="not_required",
        )
    )
    await db.flush()
    return _to_view(chapter), _to_scope(project)


async def list_chapters(
    db: AsyncSession, *, owner_id: int, novel_id: int, project_id: int
) -> tuple[DerivativeChapterScope, list[DerivativeChapterView]]:
    """List the chapter plan in stable order (position, then id)."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    rows = list(
        (
            await db.scalars(
                select(DerivativeChapter)
                .where(
                    DerivativeChapter.project_id == project.id,
                    DerivativeChapter.owner_id == owner_id,
                    DerivativeChapter.novel_id == novel_id,
                )
                .order_by(DerivativeChapter.position.asc(), DerivativeChapter.id.asc())
            )
        ).all()
    )
    return _to_scope(project), [_to_view(row) for row in rows]


async def get_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> tuple[DerivativeChapterView, DerivativeChapterScope]:
    """Read one chapter; a foreign/missing chapter is an identical 404."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    row = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    return _to_view(row), _to_scope(project)


async def update_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    patch: DerivativeChapterPatch,
) -> tuple[DerivativeChapterView, DerivativeChapterScope]:
    """Patch editable plan fields guarded by the optimistic-concurrency token.

    A stale ``base_revision`` fails closed with the current revision/checksum
    (409); the newer content is never overwritten. Revision bumps only when the
    canonicalized Markdown actually changes (semantic no-op detection).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    row = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )

    if patch.base_revision != row.revision:
        raise DerivativeChapterError(
            "revision_conflict",
            f"stale write: chapter {row.id} is at revision {row.revision} with "
            f"checksum {row.markdown_checksum}; client sent base_revision "
            f"{patch.base_revision}",
            status_code=409,
        )

    if patch.markdown is not None:
        canonical = canonicalize_markdown(patch.markdown)
        checksum = markdown_checksum(canonical)
        if canonical != row.markdown or checksum != row.markdown_checksum:
            row.markdown = canonical
            row.markdown_checksum = checksum
            row.revision += 1
            # Every real Markdown change appends an immutable revision row
            # (D-36-02/36-03) so the lineage stays complete across write paths.
            # Lazy import avoids a module-level cycle (revisions imports the
            # canonicalization helpers from this module).
            from app.services.derivative_editor.revisions import append_revision_row

            await append_revision_row(
                db,
                chapter=row,
                revision_number=row.revision,
                kind="autosave",
                content=canonical,
                checksum=checksum,
                actor_id=owner_id,
            )
    if patch.title is not None:
        new_title = patch.title.strip()
        if not new_title:
            raise DerivativeChapterError(
                "invalid_title", "chapter title must be non-empty"
            )
        row.title = new_title
    if patch.status is not None:
        row.status = patch.status.value

    await db.flush()
    await db.refresh(row)
    return _to_view(row), _to_scope(project)


async def reorder_chapters(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    request: DerivativeChapterReorderRequest,
) -> tuple[DerivativeChapterScope, list[DerivativeChapterView]]:
    """Rewrites positions from an exact full-set ordering; conflicts fail closed."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)

    rows = list(
        (
            await db.scalars(
                select(DerivativeChapter).where(
                    DerivativeChapter.project_id == project.id,
                    DerivativeChapter.owner_id == owner_id,
                    DerivativeChapter.novel_id == novel_id,
                )
            )
        ).all()
    )
    by_id = {row.id: row for row in rows}
    chapter_ids = request.chapter_ids

    if len(chapter_ids) != len(rows):
        raise DerivativeChapterError(
            "reorder_mismatch",
            f"reorder must cover exactly the project's {len(rows)} chapters, "
            f"got {len(chapter_ids)}",
            status_code=409,
        )
    if len(set(chapter_ids)) != len(chapter_ids):
        raise DerivativeChapterError(
            "reorder_duplicate",
            "reorder chapter_ids must not contain duplicates",
            status_code=409,
        )
    foreign = [cid for cid in chapter_ids if cid not in by_id]
    if foreign:
        raise DerivativeChapterError(
            "reorder_foreign_chapter",
            f"reorder contains chapter ids outside this project: {sorted(foreign)}",
            status_code=409,
        )

    for position, chapter_id in enumerate(chapter_ids):
        by_id[chapter_id].position = position
    # A single multi-row UPDATE would trip the immediate (project_id, position)
    # unique index on intermediate states; phase the reorder through a
    # collision-free offset so every intermediate row keeps a distinct,
    # non-negative position (one transaction, two flushes).
    for chapter in by_id.values():
        chapter.position = chapter.position + _REORDER_OFFSET
    await db.flush()
    for position, chapter_id in enumerate(chapter_ids):
        by_id[chapter_id].position = position
    await db.flush()
    # Server-generated timestamps are expired by the flush; reload before views.
    for chapter in by_id.values():
        await db.refresh(chapter)

    ordered = [by_id[cid] for cid in chapter_ids]
    return _to_scope(project), [_to_view(row) for row in ordered]


async def delete_chapter(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> None:
    """Hard-delete one owner-scoped chapter (archiving is the soft option)."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    row = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    await db.delete(row)
    await db.flush()


__all__ = [
    "DERIVATIVE_CHAPTER_STATUSES",
    "DerivativeChapterError",
    "canonicalize_markdown",
    "create_chapter",
    "delete_chapter",
    "get_chapter",
    "list_chapters",
    "markdown_checksum",
    "reorder_chapters",
    "update_chapter",
]
