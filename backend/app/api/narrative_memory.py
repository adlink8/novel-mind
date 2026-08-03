"""Read-only Narrative Memory structure API for Structure Workspace (Phase 20).

No promotion, no builder start, no active-pointer resolution.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.narrative_memory_product import (
    NmClaimsResponse,
    NmSourceLinksResponse,
    NmStructureTreeResponse,
    NmVersionListResponse,
)
from app.services.narrative_memory.closure import (
    ClosureError,
    analysis_report_with_progress,
)
from app.services.narrative_memory.structure_query import (
    StructureQueryError,
    list_versions,
    load_node_claims,
    load_node_source_links,
    load_structure_tree,
    resolve_through_chapter,
)

router = APIRouter(dependencies=[Depends(require_user)])


def _map_error(exc: StructureQueryError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/{novel_id}/versions", response_model=NmVersionListResponse)
async def get_versions(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List candidate NM versions for an owned novel (no default active pick)."""

    try:
        return await list_versions(db, owner_id=current_user.id, novel_id=novel.id)
    except StructureQueryError as exc:
        raise _map_error(exc) from exc


@router.get(
    "/{novel_id}/versions/{version_id}/tree",
    response_model=NmStructureTreeResponse,
)
async def get_structure_tree(
    version_id: int,
    through_chapter: int | None = Query(
        default=None,
        description="Spoiler cutoff chapter; nodes with chapter_end beyond this are hidden",
    ),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Return candidate structure tree filtered by through_chapter."""

    try:
        cutoff = resolve_through_chapter(
            through_chapter, novel_chapter_count=novel.chapter_count
        )
        return await load_structure_tree(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
            through_chapter=cutoff,
        )
    except StructureQueryError as exc:
        raise _map_error(exc) from exc


@router.get(
    "/{novel_id}/versions/{version_id}/nodes/{node_id}/claims",
    response_model=NmClaimsResponse,
)
async def get_node_claims(
    version_id: int,
    node_id: int,
    through_chapter: int | None = Query(default=None),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Return claims on a node visible at through_chapter."""

    try:
        cutoff = resolve_through_chapter(
            through_chapter, novel_chapter_count=novel.chapter_count
        )
        return await load_node_claims(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
            node_id=node_id,
            through_chapter=cutoff,
        )
    except StructureQueryError as exc:
        raise _map_error(exc) from exc


@router.get(
    "/{novel_id}/versions/{version_id}/nodes/{node_id}/source-links",
    response_model=NmSourceLinksResponse,
)
async def get_node_source_links(
    version_id: int,
    node_id: int,
    through_chapter: int | None = Query(default=None),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Return evidence source links for claims on a node (cutoff-filtered)."""

    try:
        cutoff = resolve_through_chapter(
            through_chapter, novel_chapter_count=novel.chapter_count
        )
        return await load_node_source_links(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
            node_id=node_id,
            through_chapter=cutoff,
        )
    except StructureQueryError as exc:
        raise _map_error(exc) from exc


# ---------------------------------------------------------------------------
# Phase 28-04 (REQ-NM-03/04): one-click cross-dimension closure analysis.
# Each dimension reports available/partial/blocked with durable progress and
# resume. Reports are candidate-only; nothing here writes an active pointer.
# GET reads the DB-authoritative snapshot; POST runs + persists progress.
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/versions/{version_id}/analysis")
async def get_dimension_analysis(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Read the deterministic cross-dimension closure from DB authority rows.

    Reconnect-safe: recomputed from PostgreSQL (run/stages/checkpoints), never
    from browser memory. Does not persist anything.
    """

    try:
        return await analysis_report_with_progress(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
            persist=False,
        )
    except ClosureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{novel_id}/versions/{version_id}/analysis")
async def run_dimension_analysis(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """One-click analysis: close every dimension and persist durable progress.

    Persists dimension statuses onto the build-run ``progress`` JSONB
    (idempotent, candidate-only). The DB checkpoint remains authoritative for
    reconnect; the returned ``sse_frames`` are notification-only and reuse the
    existing Agent SSE/Job transport envelope.
    """

    try:
        return await analysis_report_with_progress(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
            persist=True,
        )
    except ClosureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
