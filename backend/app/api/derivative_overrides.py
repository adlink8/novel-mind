"""Owner-scoped explicit divergence override API (Phase 37-04, D-37-03).

Routes hang under the novel surface: ``/api/novels/{novel_id}/derivative-overrides``.

- ``POST /`` — freeze an explicit divergence override for a blocked /
  ``needs_override`` candidate (reason + affected evidence required; a clean
  candidate, a missing reason/evidence, a foreign scope or a cross-fork
  project all fail closed).
- ``GET /`` / ``GET /{override_id}`` — list / read the owner's override audit.
- ``POST /{override_id}/approve`` — explicit owner approval (approval note
  required); materializes the candidate into a **Fanfiction Canon**
  ``derivative_revisions`` row and returns the immutable
  ``PublishedDerivativeRevision`` DTO. Original / Interpretation / NM are
  never written and no active pointer is promoted (D-37-02 / D-37-04).
- ``POST /{override_id}/reject`` — terminate a pending override; no revision.

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404, and the override/candidate/project/chapter are always
resolved inside the current owner + novel scope. The client can never supply
owner/novel/fork/approval fields (strict ``extra="forbid"`` DTOs).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_override import (
    OverrideApproveRequest,
    OverrideApproveResponse,
    OverrideCreateRequest,
    OverrideCreateResponse,
    OverrideDetailResponse,
    OverrideKind,
    OverrideListResponse,
    OverrideRejectRequest,
    OverrideRejectResponse,
    OverrideStatus,
    OverrideView,
    PublishedDerivativeRevisionView,
)
from app.services.derivative_generation.overrides import (
    OverrideError,
    OverrideApprovalResult,
    approve_override,
    create_override,
    get_override,
    list_overrides,
    reject_override,
)
from app.services.derivative_generation.published_revision import (
    PublishedDerivativeRevision,
)

router = APIRouter(dependencies=[Depends(require_user)])

OVERRIDES_PATH = "/{novel_id}/derivative-overrides"


def _map_error(exc: OverrideError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


def _to_override_view(row) -> OverrideView:
    return OverrideView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        project_id=row.project_id,
        chapter_id=row.chapter_id,
        fork_id=row.fork_id,
        candidate_id=row.candidate_id,
        job_id=row.job_id,
        kind=OverrideKind(row.kind),
        reason=row.reason,
        affected_evidence=list(row.affected_evidence or []),
        canon_delta_hash=row.canon_delta_hash,
        evidence_snapshot=dict(row.evidence_snapshot or {}),
        actor_id=row.actor_id,
        approval_state=OverrideStatus(row.approval_state),
        approver_id=row.approver_id,
        approved_at=row.approved_at,
        rejected_at=row.rejected_at,
        approval_reason=row.approval_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_published_view(
    published: PublishedDerivativeRevision,
) -> PublishedDerivativeRevisionView:
    return PublishedDerivativeRevisionView(**published.as_dict())


@router.post(
    OVERRIDES_PATH,
    response_model=OverrideCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_derivative_override(
    body: OverrideCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> OverrideCreateResponse:
    """Freeze an explicit divergence override for a blocked/override candidate."""
    try:
        row = await create_override(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=body.project_id,
            chapter_id=body.chapter_id,
            candidate_id=body.candidate_id,
            reason=body.reason,
            affected_evidence=list(body.affected_evidence),
            kind=body.kind.value if body.kind is not None else None,
            actor_id=current_user.id,
        )
    except OverrideError as exc:
        raise _map_error(exc) from exc
    return OverrideCreateResponse(
        override=_to_override_view(row),
        message=(
            "divergence override recorded (pending); it becomes a derivative "
            "revision only after the owner's explicit approval"
        ),
    )


@router.get(OVERRIDES_PATH, response_model=OverrideListResponse)
async def list_derivative_overrides(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> OverrideListResponse:
    """List the owner's divergence override audit for one novel."""
    rows = await list_overrides(db, owner_id=current_user.id, novel_id=novel.id)
    return OverrideListResponse(
        novel_id=novel.id,
        total=len(rows),
        items=[_to_override_view(r) for r in rows],
    )


@router.get(
    OVERRIDES_PATH + "/{override_id}",
    response_model=OverrideDetailResponse,
)
async def get_derivative_override(
    override_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> OverrideDetailResponse:
    """Read one override; a foreign/missing override is an identical 404."""
    try:
        row = await get_override(
            db, owner_id=current_user.id, novel_id=novel.id, override_id=override_id
        )
    except OverrideError as exc:
        raise _map_error(exc) from exc
    return OverrideDetailResponse(override=_to_override_view(row))


@router.post(
    OVERRIDES_PATH + "/{override_id}/approve",
    response_model=OverrideApproveResponse,
)
async def approve_derivative_override(
    override_id: int,
    body: OverrideApproveRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> OverrideApproveResponse:
    """Explicit approval: materialize into a Fanfiction revision (derivative only)."""
    try:
        result: OverrideApprovalResult = await approve_override(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            override_id=override_id,
            approval_reason=body.approval_reason,
            actor_id=current_user.id,
        )
    except OverrideError as exc:
        raise _map_error(exc) from exc
    return OverrideApproveResponse(
        override=_to_override_view(result.override),
        published=_to_published_view(result.published),
        message=(
            "override approved; candidate materialized into a Fanfiction Canon "
            "derivative revision (never Original, never promoted)"
            if result.status == "applied"
            else "override approved; the approved draft already equals the chapter head"
        ),
    )


@router.post(
    OVERRIDES_PATH + "/{override_id}/reject",
    response_model=OverrideRejectResponse,
)
async def reject_derivative_override(
    override_id: int,
    body: OverrideRejectRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> OverrideRejectResponse:
    """Terminate a pending override; no revision is ever materialized."""
    try:
        row = await reject_override(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            override_id=override_id,
            rejection_reason=body.rejection_reason,
            actor_id=current_user.id,
        )
    except OverrideError as exc:
        raise _map_error(exc) from exc
    return OverrideRejectResponse(
        override=_to_override_view(row),
        message="override rejected; no derivative revision was materialized",
    )


__all__ = ["router"]
