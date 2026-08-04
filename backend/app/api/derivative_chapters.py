"""Owner-scoped derivative chapter plan API (Phase 36-02, D-36-02/D-36-03).

Routes hang under the 36-01 project surface:
``/api/novels/{novel_id}/derivative-projects/{project_id}/chapters``.

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404, and the project/chapter are always resolved inside the
current owner + novel + project scope. The client can never supply
owner/novel/project/position/revision/checksum/space/version/cutoff (strict
``extra="forbid"`` DTOs); all writes form Fanfiction Canon drafts only
(D-36-03). Reorder is a dedicated full-set endpoint with a conflict gate, and
there is no Original/Interpretation write or publication surface here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_chapter import (
    DerivativeChapterCreate,
    DerivativeChapterCreateResponse,
    DerivativeChapterListResponse,
    DerivativeChapterPatch,
    DerivativeChapterReorderRequest,
    DerivativeChapterView,
)
from app.services.derivative_editor.chapters import (
    DerivativeChapterError,
    create_chapter,
    delete_chapter,
    get_chapter,
    list_chapters,
    reorder_chapters,
    update_chapter,
)

router = APIRouter(dependencies=[Depends(require_user)])

CHAPTERS_PATH = "/{novel_id}/derivative-projects/{project_id}/chapters"


def _map_error(exc: DerivativeChapterError) -> HTTPException:
    # Keep the machine-readable code in the detail so a fail-closed rejection
    # stays auditable on the wire (mirrors the 36-01 project error convention).
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


@router.post(
    CHAPTERS_PATH,
    response_model=DerivativeChapterCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_derivative_chapter(
    body: DerivativeChapterCreate,
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeChapterCreateResponse:
    """Append one ordered chapter plan row to an owner-scoped project."""
    try:
        view, scope = await create_chapter(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            title=body.title,
            markdown=body.markdown or "",
            status=body.status.value if body.status is not None else None,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return DerivativeChapterCreateResponse(
        chapter=view,
        scope=scope,
        message="chapter appended to the derivative chapter plan (Fanfiction draft only)",
    )


@router.get(
    CHAPTERS_PATH,
    response_model=DerivativeChapterListResponse,
)
async def list_derivative_chapters(
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeChapterListResponse:
    """List the chapter plan in stable order with the fork/version scope."""
    try:
        scope, views = await list_chapters(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return DerivativeChapterListResponse(
        project_id=project_id, scope=scope, total=len(views), items=views
    )


@router.get(
    CHAPTERS_PATH + "/{chapter_id}",
    response_model=DerivativeChapterView,
)
async def get_derivative_chapter(
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeChapterView:
    """Read one chapter; a foreign/missing chapter is an identical 404."""
    try:
        view, _scope = await get_chapter(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return view


@router.put(
    CHAPTERS_PATH + "/order",
    response_model=DerivativeChapterListResponse,
)
async def reorder_derivative_chapters(
    body: DerivativeChapterReorderRequest,
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeChapterListResponse:
    """Rewrite the full chapter order; missing/extras/duplicates fail closed."""
    try:
        scope, views = await reorder_chapters(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            request=body,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return DerivativeChapterListResponse(
        project_id=project_id, scope=scope, total=len(views), items=views
    )


@router.patch(
    CHAPTERS_PATH + "/{chapter_id}",
    response_model=DerivativeChapterView,
)
async def patch_derivative_chapter(
    body: DerivativeChapterPatch,
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeChapterView:
    """Patch editable plan fields guarded by the optimistic-concurrency token."""
    try:
        view, _scope = await update_chapter(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
            patch=body,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return view


@router.delete(
    CHAPTERS_PATH + "/{chapter_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_derivative_chapter(
    project_id: int,
    chapter_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    """Hard-delete one owner-scoped chapter (archiving is the soft option)."""
    try:
        await delete_chapter(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            chapter_id=chapter_id,
        )
    except DerivativeChapterError as exc:
        raise _map_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
