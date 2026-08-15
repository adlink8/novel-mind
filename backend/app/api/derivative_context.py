"""Owner-scoped derivative context package API (Phase 37-01, D-37-01).

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404. The fork (and its frozen lineage/cutoff) are always resolved
inside the current owner + novel scope; the client only ever selects the fork
explicitly and can never widen owner/novel/version/cutoff. Compilation is
read-only on every Canon space and persists only an append-only sealed package
row in the Fanfiction Canon context — no Original/Interpretation write exists.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.derivative_context import ContextPackageRecord
from app.schemas.derivative_context import (
    ContextPackageCreateRequest,
    ContextPackageCreateResponse,
    ContextPackageListResponse,
    ContextPackageSummary,
    ContextPackageView,
    DerivativeContextIntent,
)
from app.services.derivative_generation.context_package import (
    ContextPackageCompiler,
    ContextPackageError,
)

router = APIRouter(dependencies=[Depends(require_user)])


def _map_error(exc: ContextPackageError) -> HTTPException:
    # Keep the machine-readable code in the detail so a fail-closed rejection
    # stays auditable on the wire (mirrors the canon-fork error convention).
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


def _to_view(row: ContextPackageRecord) -> ContextPackageView:
    return ContextPackageView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        fork_id=row.fork_id,
        package_key=row.package_key,
        space=row.space,
        intent=DerivativeContextIntent(row.intent),
        fork_key=row.fork_key,
        source_version_key=row.source_version_key,
        source_snapshot_hash=row.source_snapshot_hash,
        through_chapter=row.through_chapter,
        full_book_authorized=bool(row.full_book_authorized),
        cutoff_snapshot_hash=row.cutoff_snapshot_hash,
        scope_hash=row.scope_hash,
        manifest_hash=row.manifest_hash,
        package_hash=row.package_hash,
        budget_estimate=dict(row.budget_estimate or {}),
        payload=dict(row.canonical_payload or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_summary(row: ContextPackageRecord) -> ContextPackageSummary:
    return ContextPackageSummary(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        fork_id=row.fork_id,
        package_key=row.package_key,
        intent=DerivativeContextIntent(row.intent),
        fork_key=row.fork_key,
        through_chapter=row.through_chapter,
        scope_hash=row.scope_hash,
        package_hash=row.package_hash,
        created_at=row.created_at,
    )


@router.post(
    "/{novel_id}/derivative-context-packages",
    response_model=ContextPackageCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_context_package(
    body: ContextPackageCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ContextPackageCreateResponse:
    """Compile and seal one immutable context package for an owned fork.

    The package freezes cutoff state, world/timeline/clue/world-rule dimensions,
    leaf evidence refs and user intent. A budget overrun is blocked before any
    provider call; identical input replays the sealed row.
    """
    try:
        result = await ContextPackageCompiler(db).compile(
            owner_id=current_user.id,
            novel_id=novel.id,
            user=current_user,
            fork_id=body.fork_id,
            intent=body.intent.value,
            through_chapter=body.through_chapter,
        )
    except ContextPackageError as exc:
        raise _map_error(exc) from exc
    return ContextPackageCreateResponse(
        package=_to_view(result.package),
        replayed=result.replayed,
        message=(
            "context package replayed from the sealed row"
            if result.replayed
            else "context package compiled and sealed"
        ),
    )


@router.get(
    "/{novel_id}/derivative-context-packages",
    response_model=ContextPackageListResponse,
)
async def list_context_packages(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ContextPackageListResponse:
    """List the owner's sealed context packages for one novel."""
    rows = await ContextPackageCompiler(db).list_packages(
        owner_id=current_user.id, novel_id=novel.id
    )
    return ContextPackageListResponse(
        novel_id=novel.id, total=len(rows), items=[_to_summary(r) for r in rows]
    )


@router.get(
    "/{novel_id}/derivative-context-packages/{package_id}",
    response_model=ContextPackageView,
)
async def get_context_package(
    package_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ContextPackageView:
    """Read one sealed package; a foreign/missing package is an identical 404."""
    try:
        row = await ContextPackageCompiler(db).get_package(
            owner_id=current_user.id,
            novel_id=novel.id,
            package_id=package_id,
        )
    except ContextPackageError as exc:
        raise _map_error(exc) from exc
    return _to_view(row)


__all__ = ["router"]
