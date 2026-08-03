"""Owner-scoped Visual Bible candidate API (Phase 30-02, REQ-VIS-01).

Candidate-only, evidence-linked, versioned Artifact endpoints:

- ``GET  /api/novels/{novel_id}/visual-bible`` — list candidate revisions.
- ``GET  /api/novels/{novel_id}/visual-bible/{version_id}`` — one full
  candidate envelope (authority labels + evidence + review state + rights).
- ``GET  /api/novels/{novel_id}/visual-bible/{version_id}/review-envelope`` —
  review/versioning envelope (history events, approval-gate reason codes,
  parent revision and an immutable revision ref for Scene Candidate use).
- ``POST /api/novels/{novel_id}/visual-bible`` — explicitly create one
  candidate revision; canon evidence is re-verified server-side against the
  owning novel before anything is persisted. Missing/conflicting evidence is
  returned reason-coded (fail closed) and never promotes a candidate.
- ``POST /api/novels/{novel_id}/visual-bible/{version_id}/review`` — apply one
  append-only, idempotent review action (approve/reject/edit/supersede/
  needs_relink); legality is decided server-side and approval is gated on
  persisted evidence + cleared rights before it is appended.

Every route uses ``require_owned_novel``; a version/event outside the caller's
owner/novel scope is indistinguishable from "not found" (no owner leak). No
route promotes anything to an active pointer, and no route calls an image
provider (Phase 32-33).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.visual_bible import (
    StrictVisualBibleModel,
    VisualActorSource,
    VisualBibleVersionContract,
    VisualBibleVersionView,
    VisualReviewAction,
    VisualReviewEventInput,
    VisualReviewState,
)
from app.services.visual_bible.authority import (
    CandidateConflictError,
    CandidateNotFoundError,
    GateViolationError,
    ScopeMismatchError,
    VisualBibleAuthorityService,
    list_versions,
    load_version_view,
)
from app.services.visual_bible.evidence import (
    VisualBibleEvidenceService,
)
from app.services.visual_bible.review import (
    VisualBibleReviewEnvelope,
    VisualBibleReviewService,
    build_review_envelope,
)

router = APIRouter(dependencies=[Depends(require_user)])


# ---------------------------------------------------------------------------
# Wire contracts (strict, extra="forbid")
# ---------------------------------------------------------------------------


class StrictWireModel(StrictVisualBibleModel):
    model_config = ConfigDict(extra="forbid")


class VisualBibleCreateRequest(StrictWireModel):
    """Explicit candidate revision creation payload (full immutable contract)."""

    version: VisualBibleVersionContract


class VisualBibleReviewRequest(StrictWireModel):
    """One explicit review action; scope is derived from the path, never client."""

    action: VisualReviewAction
    actor_source: VisualActorSource
    actor: str
    reason: str
    event_key: str
    from_review_state: VisualReviewState


class VisualBibleVersionListResponse(StrictWireModel):
    items: list[VisualBibleVersionView]
    total: int


class VisualBibleCreateResponse(StrictWireModel):
    version: VisualBibleVersionView
    replayed: bool = False


class ClaimUnresolvedView(StrictWireModel):
    claim_key: str
    reason_code: str
    detail: str


class VisualBibleConflictResponse(StrictWireModel):
    unresolved: list[ClaimUnresolvedView]
    kind: Literal["visual_bible_unresolved"] = "visual_bible_unresolved"


def _not_found() -> HTTPException:
    # Identical to a missing novel so cross-owner probes cannot learn anything.
    return HTTPException(status_code=404, detail="小说不存在")


# ---------------------------------------------------------------------------
# Read routes (candidate-only, no unauthorized version exposure)
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/visual-bible", response_model=VisualBibleVersionListResponse)
async def get_visual_bible_versions(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List every candidate revision for the owned novel (oldest first)."""

    items = await list_versions(
        db, owner_id=current_user.id, novel_id=novel.id
    )
    return VisualBibleVersionListResponse(items=items, total=len(items))


@router.get(
    "/{novel_id}/visual-bible/{version_id}",
    response_model=VisualBibleVersionView,
)
async def get_visual_bible_version(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Full candidate envelope: authority labels, evidence, review, rights."""

    try:
        return await load_version_view(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
        )
    except CandidateNotFoundError:
        raise _not_found() from None


# ---------------------------------------------------------------------------
# Write routes (explicit, server-gated, candidate-only)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/visual-bible",
    response_model=VisualBibleCreateResponse,
    status_code=201,
)
async def create_visual_bible_version(
    payload: VisualBibleCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Explicitly create one candidate revision after server-side evidence gates."""

    version = payload.version
    if version.owner_id != current_user.id or version.novel_id != novel.id:
        # Reject client-supplied scope instead of trusting it.
        raise _not_found()

    evidence_service = VisualBibleEvidenceService(db)
    outcome = await evidence_service.materialize_version_claims(
        owner_id=current_user.id,
        novel_id=novel.id,
        source_snapshot_id=version.source_snapshot_id,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff_chapter=version.cutoff_chapter,
        claims=version.claims,
    )
    if outcome.blocked:
        return JSONResponse(
            status_code=409,
            content=VisualBibleConflictResponse(
                unresolved=[
                    ClaimUnresolvedView(
                        claim_key=item.claim_key,
                        reason_code=item.reason_code,
                        detail=item.detail,
                    )
                    for item in outcome.unresolved
                ]
            ).model_dump(mode="json"),
        )

    verified = {
        item.claim.claim_key: item.verified_evidence for item in outcome.resolved
    }
    authority = VisualBibleAuthorityService(db)
    try:
        persisted = await authority.create_revision(
            owner_id=current_user.id,
            novel_id=novel.id,
            version=version,
            verified_evidence=verified,
        )
    except (GateViolationError, CandidateConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ScopeMismatchError:
        raise _not_found() from None

    view = await load_version_view(
        db,
        owner_id=current_user.id,
        novel_id=novel.id,
        version_id=persisted.version.id,
    )
    return VisualBibleCreateResponse(version=view, replayed=persisted.replayed)


@router.get(
    "/{novel_id}/visual-bible/{version_id}/review-envelope",
    response_model=VisualBibleReviewEnvelope,
)
async def get_visual_bible_review_envelope(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Review/versioning envelope: history, reason codes, lineage, revision ref."""

    try:
        return await build_review_envelope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
        )
    except CandidateNotFoundError:
        raise _not_found() from None


@router.post(
    "/{novel_id}/visual-bible/{version_id}/review",
    response_model=VisualBibleReviewEnvelope,
)
async def review_visual_bible_version(
    version_id: int,
    payload: VisualBibleReviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Apply one append-only, idempotent review action (server-decided legality).

    Approval runs the server-side gate first: a canon_fact claim without
    persisted evidence or a non-rights-cleared reference asset blocks with a
    stable reason code (fail closed). The response is the review envelope so
    downstream consumers get an immutable revision ref plus history.
    """

    event = VisualReviewEventInput(
        owner_id=current_user.id,
        novel_id=novel.id,
        version_id=version_id,
        action=payload.action,
        actor_source=payload.actor_source,
        actor=payload.actor,
        reason=payload.reason,
        event_key=payload.event_key,
        from_review_state=payload.from_review_state,
    )
    review = VisualBibleReviewService(db)
    try:
        await review.append_event(
            owner_id=current_user.id,
            novel_id=novel.id,
            event=event,
        )
    except CandidateNotFoundError:
        raise _not_found() from None
    except (GateViolationError, ScopeMismatchError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        return await build_review_envelope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
        )
    except CandidateNotFoundError:
        raise _not_found() from None
