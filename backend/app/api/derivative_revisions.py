"""Owner-scoped derivative chapter revision API (Phase 36-03, D-36-02).

Routes hang under the 36-02 chapter surface:
``/api/novels/{novel_id}/derivative-projects/{project_id}/chapters/{chapter_id}``.

- ``POST /autosave`` — conditional-CAS draft write (idempotent retries, 409 +
  latest revision on stale conflicts, never last-write-wins);
- ``GET /revisions`` — newest-first append-only history;
- ``GET /revisions/{revision_id}`` — one immutable revision (full content);
- ``GET /diff`` — deterministic canonical-Markdown diff between two revisions;
- ``POST /rollback`` — restore a target revision as a NEW child (history is
  never overwritten; actor/reason/approval are journaled).

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404, and the project/chapter/revision are always resolved inside
the current owner + novel + project + chapter scope. The client can never
supply owner/novel/project/chapter/revision-number/checksum/kind/approval
(strict ``extra="forbid"`` DTOs); all writes form Fanfiction Canon drafts only
(D-36-03).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_revision import (
    DerivativeAutosaveRequest,
    DerivativeAutosaveResponse,
    DerivativeDiffResponse,
    DerivativeRevisionHistoryResponse,
    DerivativeRevisionView,
    DerivativeRollbackRequest,
    DerivativeRollbackResponse,
)
from app.services.derivative_editor.revisions import (
    DerivativeRevisionError,
    autosave_revision,
    diff_revisions,
    get_revision,
    list_revisions,
    rollback_revision,
    to_revision_summary,
    to_revision_view,
)

router = APIRouter(dependencies=[Depends(require_user)])

REVISIONS_PATH = "/{novel_id}/derivative-projects/{project_id}/chapters/{chapter_id}"


def _map_error(exc: DerivativeRevisionError) -> HTTPException:
    # A 409 conflict carries the latest revision (structured detail) so the
    # stale client can recover immediately instead of being blind-rejected
    # (REQ-FORK-02 / optimistic-concurrency-recoverable).
    detail: dict[str, Any] = {"code": exc.code, "message": exc.detail}
    if exc.current_revision is not None:
        detail["current_revision_number"] = exc.current_revision.revision_number
        detail["current_checksum"] = exc.current_revision.content_checksum
        detail["current_revision"] = to_revision_view(
            exc.current_revision
        ).model_dump(mode="json")
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post(
    REVISIONS_PATH + "/autosave",
    response_model=DerivativeAutosaveResponse,
)
async def autosave_derivative_chapter(
    body: DerivativeAutosaveRequest,
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeAutosaveResponse:
    """Draft autosave: conditional CAS, idempotent retries, 409 on conflict."""
    try:
        chapter, revision, status_str = await autosave_revision(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
            content=body.content,
            base_revision=body.base_revision,
            actor_id=current_user.id,
        )
    except DerivativeRevisionError as exc:
        raise _map_error(exc) from exc
    return DerivativeAutosaveResponse(
        status=status_str,
        chapter=chapter,
        revision=revision,
        message=(
            "autosave acknowledged (Fanfiction Canon draft)"
            if status_str == "saved"
            else "draft already at the head; no new revision appended"
        ),
    )


@router.get(
    REVISIONS_PATH + "/revisions",
    response_model=DerivativeRevisionHistoryResponse,
)
async def list_derivative_chapter_revisions(
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeRevisionHistoryResponse:
    """Newest-first append-only history of one chapter."""
    try:
        chapter, rows = await list_revisions(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except DerivativeRevisionError as exc:
        raise _map_error(exc) from exc
    items = [to_revision_summary(row) for row in rows]
    return DerivativeRevisionHistoryResponse(
        chapter_id=chapter.id,
        project_id=project_id,
        total=len(items),
        items=items,
    )


@router.get(
    REVISIONS_PATH + "/revisions/{revision_id}",
    response_model=DerivativeRevisionView,
)
async def get_derivative_chapter_revision(
    project_id: int,
    chapter_id: int,
    revision_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeRevisionView:
    """Read one immutable revision; a foreign/missing revision is an identical 404."""
    try:
        row = await get_revision(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
            revision_id=revision_id,
        )
    except DerivativeRevisionError as exc:
        raise _map_error(exc) from exc
    return to_revision_view(row)


@router.get(
    REVISIONS_PATH + "/diff",
    response_model=DerivativeDiffResponse,
)
async def diff_derivative_chapter_revisions(
    project_id: int,
    chapter_id: int,
    base_revision_id: int = Query(gt=0),
    target_revision_id: int = Query(gt=0),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeDiffResponse:
    """Deterministic canonical-Markdown diff from base to target revision."""
    try:
        base, target, hunks = await diff_revisions(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
            base_revision_id=base_revision_id,
            target_revision_id=target_revision_id,
        )
    except DerivativeRevisionError as exc:
        raise _map_error(exc) from exc
    additions = sum(
        1
        for hunk in hunks
        for line in hunk["lines"]
        if line["op"] == "add"
    )
    deletions = sum(
        1
        for hunk in hunks
        for line in hunk["lines"]
        if line["op"] == "delete"
    )
    return DerivativeDiffResponse(
        base_revision_id=base.id,
        base_revision_number=base.revision_number,
        target_revision_id=target.id,
        target_revision_number=target.revision_number,
        additions=additions,
        deletions=deletions,
        hunks=hunks,
    )


@router.post(
    REVISIONS_PATH + "/rollback",
    response_model=DerivativeRollbackResponse,
)
async def rollback_derivative_chapter(
    body: DerivativeRollbackRequest,
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeRollbackResponse:
    """Restore a target revision as a NEW child; history is never overwritten."""
    try:
        chapter, revision = await rollback_revision(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
            target_revision_id=body.target_revision_id,
            reason=body.reason,
            base_revision=body.base_revision,
            actor_id=current_user.id,
        )
    except DerivativeRevisionError as exc:
        raise _map_error(exc) from exc
    return DerivativeRollbackResponse(
        chapter=chapter,
        revision=revision,
        target_revision_id=body.target_revision_id,
        message="rollback restored the target as a new immutable child revision",
    )


__all__ = ["router"]
