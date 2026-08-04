"""Owner-scoped derivative chapter revision service (Phase 36-03, D-36-02).

The service owns the append-only revision lineage of a chapter:

- **Autosave with conditional CAS (no last-write-wins, T-36-03-01):** the draft
  write is an atomic ``UPDATE ... WHERE revision = base_revision``. A stale or
  concurrent writer matches zero rows and is rejected with the current revision
  row (409) instead of overwriting newer content. A crash-before-ack retry that
  replays the exact head content resolves **idempotently** (``noop``, no new
  row) so retries never duplicate history or lose a draft.
- **Immutable history:** every real Markdown change appends a sealed
  ``DerivativeRevision`` row (``parent_revision_id``/``revision_number``/
  ``content_checksum``) inside the same transaction; existing rows are never
  mutated or deleted (the model's ``before_update`` listener fails closed).
- **Deterministic diff:** ``diff_markdown`` canonicalizes both sides first and
  emits a stable unified-diff-style hunk list, so identical logical content
  always diffs to zero hunks (replayable, D-36-02).
- **Rollback as a new child (T-36-03-02):** rollback restores the target
  revision's content into the chapter head and appends a **new** ``rollback``
  row whose parent is the current head; the actor, reason and approval state
  are journaled. No historical row is touched.
- **Owner isolation:** every query is scoped by owner + novel + project +
  chapter; a foreign/missing id is an identical 404.

Per D-36-03 every write only forms a **Fanfiction Canon draft**: the service
never writes Original Canon or User Interpretation and exposes no release
surface (Phase 39 owns release via the immutable revision service).
"""

from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_project import DerivativeProject
from app.models.derivative_revision import (
    DERIVATIVE_REVISION_APPROVAL_STATES,
    DERIVATIVE_REVISION_KINDS,
    DerivativeRevision,
)
from app.schemas.derivative_chapter import (
    DerivativeChapterStatus,
    DerivativeChapterView,
)
from app.schemas.derivative_revision import (
    DerivativeRevisionApproval,
    DerivativeRevisionKind,
    DerivativeRevisionSummary,
    DerivativeRevisionView,
)
from app.services.derivative_editor.chapters import (
    canonicalize_markdown,
    markdown_checksum,
)


class DerivativeRevisionError(ValueError):
    """Fail-closed revision gate violation with an HTTP status code.

    ``current_revision`` carries the head revision row for a 409 conflict so the
    API can return the latest revision to the stale client (recoverable, never
    a blind rejection).
    """

    def __init__(
        self,
        code: str,
        detail: str,
        status_code: int = 400,
        current_revision: DerivativeRevision | None = None,
    ):
        self.code = code
        self.detail = detail
        self.status_code = status_code
        self.current_revision = current_revision
        super().__init__(f"{code}: {detail}")


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeRevisionError(
            "invalid_scope", "scope identifiers must be explicit positive integers"
        )


def _split_lines(text: str) -> list[str]:
    """Canonical Markdown -> line list; the empty draft is an empty list."""
    if text == "":
        return []
    return text.split("\n")


def diff_markdown(old_text: str, new_text: str) -> list[dict[str, Any]]:
    """Deterministic line diff between two canonical Markdown documents.

    Both sides are canonicalized first (CRLF → LF, trailing whitespace
    stripped, D-36-02) so identical logical content always diffs to zero hunks.
    Returns unified-diff-style hunks: each hunk carries 1-based line numbers
    (``old_start``/``old_count``/``new_start``/``new_count``) and ordered lines
    with an op of ``delete``, ``add`` or ``context`` (3 lines of context around
    each contiguous change).
    """
    old_lines = _split_lines(canonicalize_markdown(old_text))
    new_lines = _split_lines(canonicalize_markdown(new_text))
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks: list[dict[str, Any]] = []
    for group in matcher.get_grouped_opcodes(n=3):
        first = group[0]
        last = group[-1]
        old_start = first[1] + 1
        old_count = last[2] - first[1]
        new_start = first[3] + 1
        new_count = last[4] - first[3]
        lines: list[dict[str, str]] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "context", "text": line})
            elif tag == "replace":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "delete", "text": line})
                for line in new_lines[j1:j2]:
                    lines.append({"op": "add", "text": line})
            elif tag == "delete":
                for line in old_lines[i1:i2]:
                    lines.append({"op": "delete", "text": line})
            elif tag == "insert":
                for line in new_lines[j1:j2]:
                    lines.append({"op": "add", "text": line})
        hunks.append(
            {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": lines,
            }
        )
    return hunks


# ---------------------------------------------------------------------------
# View builders (single source for both the service and the API error mapping)
# ---------------------------------------------------------------------------


def to_chapter_view(row: DerivativeChapter) -> DerivativeChapterView:
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


def to_revision_view(row: DerivativeRevision) -> DerivativeRevisionView:
    return DerivativeRevisionView(
        id=row.id,
        chapter_id=row.chapter_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        revision_number=row.revision_number,
        parent_revision_id=row.parent_revision_id,
        kind=DerivativeRevisionKind(row.kind),
        content=row.content,
        content_checksum=row.content_checksum,
        actor_id=row.actor_id,
        reason=row.reason,
        approval_state=DerivativeRevisionApproval(row.approval_state),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_revision_summary(row: DerivativeRevision) -> DerivativeRevisionSummary:
    return DerivativeRevisionSummary(
        id=row.id,
        chapter_id=row.chapter_id,
        project_id=row.project_id,
        revision_number=row.revision_number,
        parent_revision_id=row.parent_revision_id,
        kind=DerivativeRevisionKind(row.kind),
        content_checksum=row.content_checksum,
        actor_id=row.actor_id,
        reason=row.reason,
        approval_state=DerivativeRevisionApproval(row.approval_state),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Owner-scoped loads (a foreign/missing id is an identical 404)
# ---------------------------------------------------------------------------


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
        raise DerivativeRevisionError(
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
        raise DerivativeRevisionError(
            "chapter_not_found",
            "derivative chapter not found in the owner/novel/project scope",
            status_code=404,
        )
    return row


async def _load_scoped_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    revision_id: int,
) -> DerivativeRevision:
    row = await db.scalar(
        select(DerivativeRevision).where(
            DerivativeRevision.id == revision_id,
            DerivativeRevision.owner_id == owner_id,
            DerivativeRevision.novel_id == novel_id,
            DerivativeRevision.project_id == project_id,
            DerivativeRevision.chapter_id == chapter_id,
        )
    )
    if row is None:
        raise DerivativeRevisionError(
            "revision_not_found",
            "revision not found in the owner/novel/project/chapter scope",
            status_code=404,
        )
    return row


async def _latest_revision_row(
    db: AsyncSession, *, chapter_id: int, owner_id: int
) -> DerivativeRevision | None:
    return await db.scalar(
        select(DerivativeRevision)
        .where(
            DerivativeRevision.chapter_id == chapter_id,
            DerivativeRevision.owner_id == owner_id,
        )
        .order_by(
            DerivativeRevision.revision_number.desc(),
            DerivativeRevision.id.desc(),
        )
        .limit(1)
    )


def _require_writable_project(project: DerivativeProject) -> None:
    """Archived projects are read-only for revision writes (soft close)."""
    if project.status == "archived":
        raise DerivativeRevisionError(
            "project_archived",
            f"project {project.id} is archived; revision writes are blocked",
            status_code=409,
        )


async def append_revision_row(
    db: AsyncSession,
    *,
    chapter: DerivativeChapter,
    revision_number: int,
    kind: str,
    content: str,
    checksum: str,
    actor_id: int,
    reason: str | None = None,
    approval_state: str = "not_required",
) -> DerivativeRevision:
    """Append one immutable revision row; existing rows are never touched.

    ``parent_revision_id`` always points at the current head row of the same
    chapter (the last appended row), so the lineage chain stays complete even
    when plan-field patches and autosaves interleave.
    """
    if kind not in DERIVATIVE_REVISION_KINDS:
        raise DerivativeRevisionError(
            "invalid_revision_kind", f"unknown revision kind {kind!r}"
        )
    if approval_state not in DERIVATIVE_REVISION_APPROVAL_STATES:
        raise DerivativeRevisionError(
            "invalid_approval_state", f"unknown approval state {approval_state!r}"
        )
    latest = await _latest_revision_row(
        db, chapter_id=chapter.id, owner_id=chapter.owner_id
    )
    row = DerivativeRevision(
        chapter_id=chapter.id,
        owner_id=chapter.owner_id,
        novel_id=chapter.novel_id,
        project_id=chapter.project_id,
        revision_number=revision_number,
        parent_revision_id=latest.id if latest is not None else None,
        kind=kind,
        content=content,
        content_checksum=checksum,
        actor_id=actor_id,
        reason=reason,
        approval_state=approval_state,
    )
    db.add(row)
    await db.flush()
    return row


# ---------------------------------------------------------------------------
# Autosave: conditional CAS, idempotent replay, never last-write-wins
# ---------------------------------------------------------------------------


async def autosave_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    content: str,
    base_revision: int,
    actor_id: int,
) -> tuple[DerivativeChapterView, DerivativeRevisionView, str]:
    """Draft autosave guarded by an atomic conditional update (D-36-02).

    Returns ``(chapter view, revision view, status)`` where status is:

    - ``saved``: a new immutable row was appended and the chapter advanced;
    - ``noop``: the submitted canonical content already equals the head — a
      same-base no-op or a crash-before-ack retry resolves idempotently with no
      new row and no revision bump.

    A stale/conflicting base raises 409 carrying the current head revision.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    canonical = canonicalize_markdown(content)
    checksum = markdown_checksum(canonical)

    # No semantic change: a duplicate autosave or a crash-before-ack retry of
    # the already-committed head both resolve to an idempotent success (no new
    # row, no revision bump).
    if checksum == chapter.markdown_checksum:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        return to_chapter_view(chapter), to_revision_view(latest), "noop"

    if base_revision != chapter.revision:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    # Atomic conditional update: only the row still at `base_revision` matches,
    # so a concurrent writer can never be silently overwritten (no LWW). The
    # losing writer's UPDATE blocks and then re-evaluates the WHERE on the
    # committed row — it matches 0 rows and fails closed below.
    result = await db.execute(
        update(DerivativeChapter)
        .where(
            DerivativeChapter.id == chapter.id,
            DerivativeChapter.revision == base_revision,
        )
        .values(
            markdown=canonical,
            markdown_checksum=checksum,
            revision=DerivativeChapter.revision + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        # A concurrent writer committed between our read and the conditional
        # update; reload the head and re-evaluate (replay vs conflict).
        await db.refresh(chapter)
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        if checksum == chapter.markdown_checksum:
            return to_chapter_view(chapter), to_revision_view(latest), "noop"
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    await db.refresh(chapter)
    revision = await append_revision_row(
        db,
        chapter=chapter,
        revision_number=chapter.revision,
        kind="autosave",
        content=canonical,
        checksum=checksum,
        actor_id=actor_id,
    )
    return to_chapter_view(chapter), to_revision_view(revision), "saved"


# ---------------------------------------------------------------------------
# History + single revision detail
# ---------------------------------------------------------------------------


async def list_revisions(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
) -> tuple[DerivativeChapterView, list[DerivativeRevision]]:
    """Newest-first append-only history of one chapter."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    rows = list(
        (
            await db.scalars(
                select(DerivativeRevision)
                .where(
                    DerivativeRevision.chapter_id == chapter.id,
                    DerivativeRevision.owner_id == owner_id,
                    DerivativeRevision.novel_id == novel_id,
                    DerivativeRevision.project_id == project.id,
                )
                .order_by(
                    DerivativeRevision.revision_number.desc(),
                    DerivativeRevision.id.desc(),
                )
            )
        ).all()
    )
    return to_chapter_view(chapter), rows


async def get_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    revision_id: int,
) -> DerivativeRevision:
    """Read one immutable revision; a foreign/missing revision is an identical 404."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    return await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=revision_id,
    )


# ---------------------------------------------------------------------------
# Deterministic diff between two revision rows
# ---------------------------------------------------------------------------


async def diff_revisions(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    base_revision_id: int,
    target_revision_id: int,
) -> tuple[DerivativeRevision, DerivativeRevision, list[dict[str, Any]]]:
    """Canonical-Markdown diff from ``base`` to ``target`` revision row."""
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter_id,
    )
    base = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=base_revision_id,
    )
    target = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=target_revision_id,
    )
    return base, target, diff_markdown(base.content, target.content)


# ---------------------------------------------------------------------------
# Rollback: a NEW child revision, never an in-place history rewrite
# ---------------------------------------------------------------------------


async def rollback_revision(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    chapter_id: int,
    target_revision_id: int,
    reason: str | None,
    base_revision: int,
    actor_id: int,
) -> tuple[DerivativeChapterView, DerivativeRevisionView]:
    """Restore a target revision's content as a NEW child (T-36-03-02).

    The rollback writes the target's canonical Markdown into the chapter head
    (CAS-guarded exactly like an autosave) and appends an immutable ``rollback``
    row whose parent is the current head, journaling the actor, reason and
    approval state. Historical rows are never modified or deleted, so the
    rollback itself remains a recoverable, auditable event.
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    project = await _load_scoped_project(
        db, owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    _require_writable_project(project)
    chapter = await _load_scoped_chapter(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        chapter_id=chapter_id,
    )
    target = await _load_scoped_revision(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project.id,
        chapter_id=chapter.id,
        revision_id=target_revision_id,
    )

    if base_revision != chapter.revision:
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    # Conditional update: never clobber a concurrent write while restoring.
    result = await db.execute(
        update(DerivativeChapter)
        .where(
            DerivativeChapter.id == chapter.id,
            DerivativeChapter.revision == base_revision,
        )
        .values(
            markdown=target.content,
            markdown_checksum=target.content_checksum,
            revision=DerivativeChapter.revision + 1,
            updated_at=func.now(),
        )
    )
    if result.rowcount == 0:
        await db.refresh(chapter)
        latest = await _latest_revision_row(
            db, chapter_id=chapter.id, owner_id=owner_id
        )
        raise DerivativeRevisionError(
            "revision_conflict",
            f"stale write: chapter {chapter.id} is at revision {chapter.revision} "
            f"with checksum {chapter.markdown_checksum}; client sent "
            f"base_revision {base_revision}",
            status_code=409,
            current_revision=latest,
        )

    await db.refresh(chapter)
    revision = await append_revision_row(
        db,
        chapter=chapter,
        revision_number=chapter.revision,
        kind="rollback",
        content=target.content,
        checksum=target.content_checksum,
        actor_id=actor_id,
        reason=(reason or "").strip() or None,
        approval_state="approved",
    )
    return to_chapter_view(chapter), to_revision_view(revision)


__all__ = [
    "DerivativeRevisionError",
    "append_revision_row",
    "autosave_revision",
    "diff_markdown",
    "diff_revisions",
    "get_revision",
    "list_revisions",
    "rollback_revision",
    "to_chapter_view",
    "to_revision_summary",
    "to_revision_view",
]
