"""Owner-scoped derivative asset candidate API (Phase 38-03, D-38-03).

Routes hang under ``/api/novels/{novel_id}/derivative-visual``:

- ``POST /assets`` — store one write-only derivative asset candidate. The
  frozen canonical derivative Scene Spec is revalidated (replays its content
  hash), the approved fork version is required in scope, the content checksum /
  divergence manifest / identity / source lineage are verified and the
  deterministic cross-chapter consistency signal is persisted. A duplicate
  ``asset_key`` with identical content replays; a conflicting retry fails
  closed (409). Only candidate/review states are ever produced — nothing here
  publishes and nothing writes to the Original Visual Bible tables.
- ``GET /assets`` / ``GET /assets/{asset_id}`` — published derivative assets
  only (``review_state == approved``, owner/project/fork visible). Original or
  unapproved assets are blocked (404-equivalent).
- ``GET /assets/{asset_id}/bytes`` — owner-scoped published asset bytes; raw
  storage paths are never exposed.
- ``GET /assets/{asset_id}/consistency`` — the deterministic cross-chapter
  consistency report + reasons (review lineage).
- ``POST /assets/{asset_id}/review`` — append one explicit approve/reject/
  supersede action (idempotent event_key). A ``blocked`` candidate can never be
  approved.

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404, and the client can never supply owner/novel/fork/project/
namespace/approval/path (strict ``extra="forbid"`` DTOs).
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_visual import DerivativeSceneSpecContract
from app.schemas.derivative_visual_asset import (
    DERIVATIVE_ASSET_NAMESPACE,
    DerivativeAssetCandidateWrite,
    DerivativeAssetReviewEventInput,
    DerivativeVisualAssetView,
    PublishedDerivativeVisualAsset,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetReviewError,
    DerivativeAssetStorage,
    DerivativeCandidateConflict,
    DerivativeCandidateScopeError,
    apply_derivative_asset_review,
    store_derivative_candidate_asset,
)
from app.services.derivative_visual.published_assets import (
    PublishedAssetNotFound,
    PublishedAssetScopeError,
    derivative_asset_view,
    list_published_assets,
    load_published_asset,
)

router = APIRouter(dependencies=[Depends(require_user)])

DERIVATIVE_ASSET_PATH = "/{novel_id}/derivative-visual"


class StrictWireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeAssetStoreRequest(StrictWireModel):
    """Store one derivative asset candidate (D-38-03).

    ``spec`` is the frozen canonical derivative Scene Spec (the only provider
    input); the server revalidates it and replays its content hash before any
    byte is written. ``payload_base64`` is the candidate bytes; the server
    always replays the checksum from them.
    """

    spec: dict[str, Any]
    candidate: DerivativeAssetCandidateWrite
    payload_base64: str = Field(min_length=1)


class DerivativeAssetStoreResponse(StrictWireModel):
    asset: DerivativeVisualAssetView
    replayed: bool = False


class DerivativeAssetListResponse(StrictWireModel):
    items: list[PublishedDerivativeVisualAsset]
    total: int


class DerivativeAssetReviewRequest(StrictWireModel):
    """One explicit review action; scope comes from the path, never the body."""

    event_key: str = Field(min_length=1, max_length=160)
    action: str = Field(pattern=r"^(approve|reject|supersede)$")
    actor_source: str = Field(pattern=r"^(human|machine)$")
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_review_state: str = Field(
        pattern=r"^(candidate|needs_review|approved|rejected|superseded|blocked)$"
    )


class DerivativeAssetReviewResponse(StrictWireModel):
    asset: DerivativeVisualAssetView


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="derivative asset not found in scope")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


# ---------------------------------------------------------------------------
# Storage seam (integration tests override the bytes backend)
# ---------------------------------------------------------------------------

_asset_storage: DerivativeAssetStorage | None = None


def set_derivative_asset_storage(storage: DerivativeAssetStorage | None) -> None:
    """Override the candidate bytes backend (used by integration tests)."""
    global _asset_storage
    _asset_storage = storage


def _storage() -> DerivativeAssetStorage:
    if _asset_storage is not None:
        return _asset_storage
    return DerivativeAssetStorage(DerivativeAssetStorage.default_storage_root())


# ---------------------------------------------------------------------------
# Read routes (published-only, owner-scoped)
# ---------------------------------------------------------------------------


@router.get(
    DERIVATIVE_ASSET_PATH + "/assets",
    response_model=DerivativeAssetListResponse,
)
async def list_derivative_visual_assets(
    project_id: int | None = Query(default=None, gt=0),
    fork_id: int | None = Query(default=None, gt=0),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeAssetListResponse:
    """List every published derivative asset visible in the owner/novel scope."""
    try:
        items = await list_published_assets(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            project_id=project_id,
            fork_id=fork_id,
        )
    except PublishedAssetScopeError as exc:
        raise _bad_request(str(exc)) from exc
    return DerivativeAssetListResponse(items=items, total=len(items))


@router.get(
    DERIVATIVE_ASSET_PATH + "/assets/{asset_id}",
    response_model=PublishedDerivativeVisualAsset,
)
async def load_derivative_visual_asset(
    asset_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> PublishedDerivativeVisualAsset:
    """Load one published derivative asset; unapproved/out-of-scope is 404."""
    try:
        return await load_published_asset(
            db, owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except PublishedAssetNotFound:
        raise _not_found() from None


@router.get(DERIVATIVE_ASSET_PATH + "/assets/{asset_id}/bytes")
async def read_derivative_visual_asset_bytes(
    asset_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    """Owner-scoped published asset bytes; raw storage paths are never exposed."""
    try:
        asset = await load_published_asset(
            db, owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except PublishedAssetNotFound:
        raise _not_found() from None
    try:
        payload = _storage().read(
            owner_id=current_user.id,
            novel_id=novel.id,
            visual_version_id=asset.visual_version.version_id,
            asset_id=asset.asset_id,
            mime_type=asset.mime_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail="asset bytes missing") from exc
    return Response(content=payload, media_type=asset.mime_type)


@router.get(
    DERIVATIVE_ASSET_PATH + "/assets/{asset_id}/consistency",
    response_model=DerivativeVisualAssetView,
)
async def read_derivative_visual_asset_consistency(
    asset_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeVisualAssetView:
    """Consistency review lineage for one candidate (owner-scoped, any state)."""
    try:
        candidate = await load_candidate_by_asset_id(
            db, owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
        return await derivative_asset_view(
            db,
            candidate,
        )
    except DerivativeCandidateScopeError:
        raise _not_found() from None


# ---------------------------------------------------------------------------
# Write routes (server-gated, idempotent, candidate-only)
# ---------------------------------------------------------------------------


@router.post(
    DERIVATIVE_ASSET_PATH + "/assets",
    response_model=DerivativeAssetStoreResponse,
    status_code=201,
)
async def store_derivative_visual_candidate(
    body: DerivativeAssetStoreRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeAssetStoreResponse:
    """Store one write-only derivative asset candidate (D-38-03).

    Replays the frozen Scene Spec and the content checksum, verifies the
    divergence manifest and the identity/source lineage against the spec, and
    persists the deterministic cross-chapter consistency signal. The response
    is a candidate/review envelope — never a publication.
    """
    spec = body.spec
    try:
        spec_contract = DerivativeSceneSpecContract.model_validate(spec)
    except Exception as exc:
        raise _conflict(f"scene_spec_invalid: {exc}") from exc
    try:
        payload = base64.b64decode(body.payload_base64, validate=True)
    except Exception as exc:
        raise _bad_request(f"payload_base64 is not valid base64: {exc}") from exc
    try:
        row, replayed = await store_derivative_candidate_asset(
            db,
            _storage(),
            owner_id=current_user.id,
            novel_id=novel.id,
            spec=spec_contract,
            candidate=body.candidate,
            payload=payload,
        )
    except DerivativeCandidateScopeError as exc:
        raise _not_found() from exc
    except DerivativeCandidateConflict as exc:
        raise _conflict(str(exc)) from exc
    return DerivativeAssetStoreResponse(
        asset=await derivative_asset_view(db, row),
        replayed=replayed,
    )


@router.post(
    DERIVATIVE_ASSET_PATH + "/assets/{asset_id}/review",
    response_model=DerivativeAssetReviewResponse,
)
async def review_derivative_visual_candidate(
    asset_id: str,
    body: DerivativeAssetReviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeAssetReviewResponse:
    """Append one explicit review action to a candidate (idempotent).

    A ``blocked`` candidate (identity drift / undeclared divergence) has an
    empty legal transition set, so approval always fails closed (409).
    """
    try:
        candidate = await load_candidate_by_asset_id(
            db, owner_id=current_user.id, novel_id=novel.id, asset_id=asset_id
        )
    except DerivativeCandidateScopeError:
        raise _not_found() from None
    event = DerivativeAssetReviewEventInput(
        owner_id=current_user.id,
        novel_id=novel.id,
        candidate_id=candidate.id,
        action=body.action,
        actor_source=body.actor_source,
        actor=body.actor,
        reason=body.reason,
        event_key=body.event_key,
        from_review_state=body.from_review_state,
    )
    try:
        row = await apply_derivative_asset_review(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            event=event,
        )
    except DerivativeCandidateScopeError:
        raise _not_found() from None
    except DerivativeAssetReviewError as exc:
        raise _conflict(str(exc)) from exc
    return DerivativeAssetReviewResponse(asset=await derivative_asset_view(db, row))


async def load_candidate_by_asset_id(
    db: AsyncSession, *, owner_id: int, novel_id: int, asset_id: str
):
    from app.models.derivative_visual import DerivativeVisualCandidateAsset
    from sqlalchemy import select

    candidate = await db.scalar(
        select(DerivativeVisualCandidateAsset).where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
            DerivativeVisualCandidateAsset.asset_id == asset_id,
        )
    )
    if candidate is None:
        raise DerivativeCandidateScopeError(
            "derivative candidate asset not found in the owner/novel scope"
        )
    return candidate


__all__ = [
    "DERIVATIVE_ASSET_NAMESPACE",
    "router",
    "set_derivative_asset_storage",
]
