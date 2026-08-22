"""Owner-scoped derivative visual review seam API (Phase 38-04, D-38-03).

Routes hang under ``/api/novels/{novel_id}/derivative-visual/review`` (a path
prefix that never collides with the 38-03 ``.../assets/...`` router):

- ``GET /review`` — list the candidate review queue (any review state,
  owner-scoped; optional ``project_id``/``fork_id``/``review_state`` filters)
  for the review panel;
- ``GET /review/{candidate_id}`` — one candidate review detail (source refs,
  per-chapter identity/style scores, divergence manifest + declaration,
  namespace, append-only review events);
- ``POST /review/{candidate_id}/action`` — apply one explicit approve/reject/
  supersede action (idempotent ``event_key``, ``from_review_state`` validated
  server-side). An approval returns the same ``PublishedDerivativeVisualAsset``
  the 38-03 published query exposes; a ``blocked`` candidate (identity drift /
  undeclared divergence) can never be approved (409).

Every route starts from ``require_owned_novel`` (a mismatched owner/novel is an
identical 404) and the DTOs are strict ``extra="forbid"`` — the client can never
inject owner/novel/fork/project/namespace/approval/path. All scope comes from
the path/DB.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_visual_asset import (
    DerivativeAssetReviewEventInput,
    DerivativeVisualAssetView,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetReviewError,
    DerivativeCandidateScopeError,
)
from app.services.derivative_visual.review import (
    DerivativeReviewCandidateNotFound,
    DerivativeReviewSeamError,
    list_review_candidates,
    load_review_candidate,
    review_candidate_asset,
)

router = APIRouter(dependencies=[Depends(require_user)])

REVIEW_PATH = "/{novel_id}/derivative-visual/review"

_REVIEW_STATE_PATTERN = (
    "^(candidate|needs_review|approved|rejected|superseded|blocked)$"
)


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeReviewListResponse(StrictWireModel):
    items: list[DerivativeVisualAssetView]
    total: int


class DerivativeReviewActionRequest(StrictWireModel):
    """One explicit review action; scope comes from the path, never the body."""

    event_key: str = Field(min_length=1, max_length=160)
    action: str = Field(pattern=r"^(approve|reject|supersede)$")
    actor_source: str = Field(pattern=r"^(human|machine)$")
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_review_state: str = Field(pattern=_REVIEW_STATE_PATTERN)


class DerivativeReviewActionResponse(StrictWireModel):
    asset: DerivativeVisualAssetView


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail="derivative review candidate not found in scope"
    )


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


@router.get(REVIEW_PATH, response_model=DerivativeReviewListResponse)
async def list_derivative_visual_review_candidates(
    project_id: int | None = Query(default=None, gt=0),
    fork_id: int | None = Query(default=None, gt=0),
    review_state: str | None = Query(default=None, pattern=_REVIEW_STATE_PATTERN),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeReviewListResponse:
    """List every candidate visible in the owner/novel scope (any state)."""
    try:
        items = await list_review_candidates(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            fork_id=fork_id,
            review_state=review_state,
        )
    except DerivativeReviewSeamError as exc:
        raise _bad_request(str(exc)) from exc
    return DerivativeReviewListResponse(items=items, total=len(items))


@router.get(REVIEW_PATH + "/{candidate_id}", response_model=DerivativeVisualAssetView)
async def derivative_visual_review_candidate_detail(
    candidate_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeVisualAssetView:
    """One candidate review detail (source refs / scores / divergence / events)."""
    try:
        return await load_review_candidate(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            candidate_id=candidate_id,
        )
    except (DerivativeCandidateScopeError, DerivativeReviewCandidateNotFound):
        raise _not_found() from None


@router.post(
    REVIEW_PATH + "/{candidate_id}/action",
    response_model=DerivativeReviewActionResponse,
)
async def derivative_visual_review_candidate_action(
    candidate_id: int,
    body: DerivativeReviewActionRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeReviewActionResponse:
    """Apply one explicit review action (idempotent; blocked can never approve).

    ``approved`` returns the same ``PublishedDerivativeVisualAsset`` projection
    the 38-03 published query exposes; every other resulting state returns the
    candidate ``DerivativeVisualAssetView`` envelope.
    """
    event = DerivativeAssetReviewEventInput(
        owner_id=current_user.id,
        novel_id=novel.id,
        candidate_id=candidate_id,
        action=body.action,
        actor_source=body.actor_source,
        actor=body.actor,
        reason=body.reason,
        event_key=body.event_key,
        from_review_state=body.from_review_state,
    )
    try:
        asset = await review_candidate_asset(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            event=event,
        )
    except (DerivativeCandidateScopeError, DerivativeReviewCandidateNotFound):
        raise _not_found() from None
    except (DerivativeReviewSeamError, DerivativeAssetReviewError) as exc:
        raise _conflict(str(exc)) from exc
    return DerivativeReviewActionResponse(asset=asset)


__all__ = ["router"]
