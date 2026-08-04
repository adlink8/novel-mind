"""Owner-scoped derivative project API (Phase 36-01, D-36-01/D-36-03).

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404. The project (and its fork) are always resolved inside the
current owner + novel scope; the client only ever selects the fork explicitly
and can never supply owner/novel/space/version/cutoff (D-36-01). Writes are
Fanfiction Canon only (D-36-03); there is no Original or Interpretation write
endpoint here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_project import (
    DerivativeProjectCreate,
    DerivativeProjectCreateResponse,
    DerivativeProjectListResponse,
    DerivativeProjectPatch,
    DerivativeProjectView,
)
from app.services.derivative_editor.projects import (
    DerivativeProjectError,
    create_project,
    delete_project,
    get_project,
    list_projects,
    update_project,
)

router = APIRouter(dependencies=[Depends(require_user)])


def _map_error(exc: DerivativeProjectError) -> HTTPException:
    # Keep the machine-readable code in the detail so a fail-closed rejection
    # stays auditable on the wire (mirrors the canon-fork error convention).
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


@router.post(
    "/{novel_id}/derivative-projects",
    response_model=DerivativeProjectCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_derivative_project(
    body: DerivativeProjectCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeProjectCreateResponse:
    """Create an owner-scoped project bound to an explicit Fanfiction Canon Fork."""
    try:
        view = await create_project(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            fork_id=body.fork_id,
            name=body.name,
            project_key=body.project_key,
            description=body.description,
        )
    except DerivativeProjectError as exc:
        raise _map_error(exc) from exc
    return DerivativeProjectCreateResponse(
        project=view, message="derivative project bound to the explicit canon fork"
    )


@router.get(
    "/{novel_id}/derivative-projects",
    response_model=DerivativeProjectListResponse,
)
async def list_derivative_projects(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeProjectListResponse:
    """List the owner's projects for one novel (no default active pick)."""
    views = await list_projects(
        db, owner_id=current_user.id, novel_id=novel.id
    )
    return DerivativeProjectListResponse(
        novel_id=novel.id, total=len(views), items=views
    )


@router.get(
    "/{novel_id}/derivative-projects/{project_id}",
    response_model=DerivativeProjectView,
)
async def get_derivative_project(
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeProjectView:
    """Read one project; a foreign/missing project is an identical 404."""
    try:
        return await get_project(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
        )
    except DerivativeProjectError as exc:
        raise _map_error(exc) from exc


@router.patch(
    "/{novel_id}/derivative-projects/{project_id}",
    response_model=DerivativeProjectView,
)
async def patch_derivative_project(
    project_id: int,
    body: DerivativeProjectPatch,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeProjectView:
    """Patch mutable project state; the frozen fork lineage never changes."""
    try:
        return await update_project(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            patch=body,
        )
    except DerivativeProjectError as exc:
        raise _map_error(exc) from exc


@router.delete(
    "/{novel_id}/derivative-projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_derivative_project(
    project_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    """Hard-delete one owner-scoped project (archiving is the soft option)."""
    try:
        await delete_project(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
        )
    except DerivativeProjectError as exc:
        raise _map_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
