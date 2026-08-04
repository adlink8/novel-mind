"""Owner-scoped illustration anchor API (Phase 34-05, REQ-VIS-05).

Hash-verified anchor candidates and the deterministic publish surface:

- ``POST /{novel_id}/illustration-anchors/proposals`` — create one candidate
  IllustrationAnchorProposal + pending Web ApprovalRequest. The server-side
  proposal gate only accepts a proposal-ready AssetRevision (Phase 33 handoff)
  with cleared rights and an exact source hash/range/version (D-34-01); a
  duplicate idempotency key replays the existing proposal. The proposal is
  candidate-only — nothing here publishes.
- ``GET .../proposals`` / ``.../proposals/{proposal_id}`` — candidate proposal
  read envelopes (never reader/export visible).
- ``POST .../proposals/{proposal_id}/publish`` — the deterministic publisher:
  atomically verifies the approved Web approval (action + payload hash),
  proposal-ready asset, exact source hash/range/version against the current
  chapter and the owner/novel/branch/fork scope, then creates the published
  ``valid`` anchor + frozen publish manifest (D-34-04). Forged/expired approval,
  stale revision, wrong branch/fork or schema drift fail closed with no
  authoritative write (D-34-01).
- ``GET .../proposals/{proposal_id}/manifest`` — the frozen publish manifest for
  a published anchor (reader/export read exactly this).
- ``GET /{novel_id}/illustration-anchors`` / ``.../{anchor_id}`` — published
  reader/export-visible anchor read envelopes.

Every route uses ``require_owned_novel``; a proposal/anchor outside the caller's
owner/novel scope is indistinguishable from "not found". FastAPI owns state and
the deterministic publisher owns approved publication (REQ-AGENT-03/04/07).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.illustration_anchor import IllustrationAnchor, IllustrationAnchorProposal
from app.schemas.agent_approvals import ApprovalRequestView
from app.schemas.agent_tools import StrictAgentToolModel
from app.schemas.illustration_anchor import (
    AnchorProposalView,
    AnchorStatus,
    AnchorView,
)
from app.services.illustration_anchors.publish import (
    ANCHOR_APPROVAL_ACTIONS,
    AnchorProposalError,
    AnchorPublishError,
    build_anchor_manifest,
    create_anchor_proposal,
    publish_anchor,
)

router = APIRouter(dependencies=[Depends(require_user)])


class AnchorProposalCreateRequest(StrictAgentToolModel):
    """Server-side candidate proposal creation (scope comes from the path).

    Mirrors the ``publish_illustration`` / ``attach_illustration_to_text`` action
    tool request plus the explicit Phase 34 action. The server derives
    authority_space from branch/fork; the Agent/UI can never widen scope.
    """

    action: Literal["publish_illustration", "attach_illustration_to_text"]
    branch: str | None = Field(default=None, max_length=80)
    fork: str | None = Field(default=None, max_length=80)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    proposal_key: str = Field(min_length=1, max_length=160)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    paragraph_start: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)
    excerpt: str = Field(min_length=1, max_length=20000)
    anchor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_revision_id: int = Field(gt=0)
    caption: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(min_length=1, max_length=500)
    citation: str = Field(min_length=1, max_length=1000)


class AnchorProposalCreateResponse(StrictAgentToolModel):
    proposal: AnchorProposalView
    approval_request: ApprovalRequestView
    replayed: bool = False


class AnchorProposalListResponse(StrictAgentToolModel):
    items: list[AnchorProposalView]
    total: int


class AnchorListResponse(StrictAgentToolModel):
    items: list[AnchorView]
    total: int


class AnchorPublishResponse(StrictAgentToolModel):
    anchor: AnchorView
    manifest: dict
    manifest_hash: str


def _proposal_view(row: IllustrationAnchorProposal) -> AnchorProposalView:
    return AnchorProposalView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        chapter_id=row.chapter_id,
        chapter_number=row.chapter_number,
        proposal_key=row.proposal_key,
        source_snapshot_id=row.source_snapshot_id,
        source_snapshot_hash=row.source_snapshot_hash,
        paragraph_start=row.paragraph_start,
        paragraph_end=row.paragraph_end,
        source_start=row.source_start,
        source_end=row.source_end,
        excerpt=row.excerpt,
        anchor_hash=row.anchor_hash,
        chapter_content_hash=row.chapter_content_hash,
        proposal_asset_revision_id=row.proposal_asset_revision_id,
        approval_request_id=row.approval_request_id,
        published_asset_revision_id=row.published_asset_revision_id,
        publish_manifest_hash=row.publish_manifest_hash,
        status=AnchorStatus(row.status),
        caption=row.caption,
        alt_text=row.alt_text,
        citation=row.citation,
    )


def _anchor_view(row: IllustrationAnchor) -> AnchorView:
    return AnchorView(
        id=row.id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        chapter_id=row.chapter_id,
        chapter_number=row.chapter_number,
        anchor_key=row.anchor_key,
        proposal_id=row.proposal_id,
        source_snapshot_id=row.source_snapshot_id,
        source_snapshot_hash=row.source_snapshot_hash,
        paragraph_start=row.paragraph_start,
        paragraph_end=row.paragraph_end,
        source_start=row.source_start,
        source_end=row.source_end,
        excerpt=row.excerpt,
        anchor_hash=row.anchor_hash,
        chapter_content_hash=row.chapter_content_hash,
        published_asset_revision_id=row.published_asset_revision_id,
        publish_manifest_hash=row.publish_manifest_hash,
        approval_request_id=row.approval_request_id,
        status=AnchorStatus(row.status),
        caption=row.caption,
        alt_text=row.alt_text,
        citation=row.citation,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="小说不存在")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


# ---------------------------------------------------------------------------
# Candidate proposal routes (candidate-only, never reader/export visible)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/illustration-anchors/proposals",
    response_model=AnchorProposalCreateResponse,
    status_code=201,
)
async def create_illustration_anchor_proposal(
    payload: AnchorProposalCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Create one candidate anchor proposal + pending Web ApprovalRequest.

    The server-side proposal gate only accepts a proposal-ready AssetRevision
    with cleared rights and an exact source hash/range/version (D-34-01). The
    proposal is candidate-only; the deterministic publisher owns publication.
    """
    owner_id = current_user.id
    novel_id = novel.id
    request = payload.model_dump()
    if payload.action not in ANCHOR_APPROVAL_ACTIONS:
        raise _conflict(
            f"unknown Phase 34 action {payload.action!r}; allowed: "
            f"{sorted(ANCHOR_APPROVAL_ACTIONS)}"
        )
    try:
        result = await create_anchor_proposal(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            request=request,
            action=payload.action,
        )
    except AnchorProposalError as exc:
        raise _conflict(str(exc)) from exc
    await db.commit()
    approval = result.approval_request
    return AnchorProposalCreateResponse(
        proposal=_proposal_view(result.proposal),
        approval_request=ApprovalRequestView(
            id=approval.id,
            owner_id=approval.owner_id,
            run_id=approval.run_id,
            action=approval.action,
            payload_summary=dict(approval.payload_summary or {}),
            status=approval.status,
            created_at=approval.created_at,
            decided_at=approval.decided_at,
            expires_at=approval.expires_at,
        ),
        replayed=result.replayed,
    )


@router.get(
    "/{novel_id}/illustration-anchors/proposals",
    response_model=AnchorProposalListResponse,
)
async def list_illustration_anchor_proposals(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List candidate anchor proposals for the caller's novel."""
    where = (
        IllustrationAnchorProposal.owner_id == current_user.id,
        IllustrationAnchorProposal.novel_id == novel.id,
    )
    total = await db.scalar(
        select(func.count()).select_from(IllustrationAnchorProposal).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(IllustrationAnchorProposal)
                .where(*where)
                .order_by(IllustrationAnchorProposal.id.desc())
            )
        ).all()
    )
    return AnchorProposalListResponse(
        items=[_proposal_view(row) for row in rows], total=int(total or 0)
    )


@router.get(
    "/{novel_id}/illustration-anchors/proposals/{proposal_id}",
    response_model=AnchorProposalView,
)
async def get_illustration_anchor_proposal(
    proposal_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    row = await db.scalar(
        select(IllustrationAnchorProposal).where(
            IllustrationAnchorProposal.id == proposal_id,
            IllustrationAnchorProposal.owner_id == current_user.id,
            IllustrationAnchorProposal.novel_id == novel.id,
        )
    )
    if row is None:
        raise _not_found()
    return _proposal_view(row)


# ---------------------------------------------------------------------------
# Deterministic publish route (approved-only, fail closed)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/illustration-anchors/proposals/{proposal_id}/publish",
    response_model=AnchorPublishResponse,
)
async def publish_illustration_anchor(
    proposal_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Deterministic publisher: create the valid anchor from an approved proposal.

    Atomically verifies the approved Web approval (action + replay payload hash),
    the proposal-ready asset, the exact source hash/range/version against the
    current chapter and the owner/novel/branch/fork scope; then creates the
    published ``valid`` anchor + frozen publish manifest (D-34-04). Forged/
    expired approval, stale revision, wrong branch/fork or schema drift fail
    closed with no authoritative write (D-34-01).
    """
    owner_id = current_user.id
    novel_id = novel.id
    try:
        anchor = await publish_anchor(
            db, owner_id=owner_id, novel_id=novel_id, proposal_id=proposal_id
        )
    except AnchorPublishError as exc:
        raise _conflict(str(exc)) from exc
    manifest = await build_anchor_manifest(
        db, owner_id=owner_id, novel_id=novel_id, anchor_id=anchor.id
    )
    await db.commit()
    return AnchorPublishResponse(
        anchor=_anchor_view(anchor),
        manifest=manifest.model_dump(mode="json"),
        manifest_hash=anchor.publish_manifest_hash,
    )


@router.get("/{novel_id}/illustration-anchors/proposals/{proposal_id}/manifest")
async def get_illustration_anchor_manifest(
    proposal_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """The frozen publish manifest (D-34-04) for a published proposal."""
    proposal = await db.scalar(
        select(IllustrationAnchorProposal).where(
            IllustrationAnchorProposal.id == proposal_id,
            IllustrationAnchorProposal.owner_id == current_user.id,
            IllustrationAnchorProposal.novel_id == novel.id,
        )
    )
    if proposal is None:
        raise _not_found()
    if proposal.status != AnchorStatus.VALID.value or proposal.published_asset_revision_id is None:
        raise _conflict("proposal has not been published yet")
    anchor = await db.scalar(
        select(IllustrationAnchor).where(
            IllustrationAnchor.proposal_id == proposal.id,
            IllustrationAnchor.owner_id == current_user.id,
            IllustrationAnchor.novel_id == novel.id,
        )
    )
    if anchor is None:
        raise _not_found()
    try:
        manifest = await build_anchor_manifest(
            db, owner_id=current_user.id, novel_id=novel.id, anchor_id=anchor.id
        )
    except AnchorPublishError as exc:
        raise _conflict(str(exc)) from exc
    return {"manifest": manifest.model_dump(mode="json"), "manifest_hash": anchor.publish_manifest_hash}


# ---------------------------------------------------------------------------
# Published anchor routes (reader/export-visible, approved-only)
# ---------------------------------------------------------------------------


@router.get(
    "/{novel_id}/illustration-anchors",
    response_model=AnchorListResponse,
)
async def list_illustration_anchors(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List published reader/export-visible anchors for the caller's novel."""
    where = (
        IllustrationAnchor.owner_id == current_user.id,
        IllustrationAnchor.novel_id == novel.id,
    )
    total = await db.scalar(
        select(func.count()).select_from(IllustrationAnchor).where(*where)
    )
    rows = list(
        (
            await db.scalars(
                select(IllustrationAnchor)
                .where(*where)
                .order_by(IllustrationAnchor.id.desc())
            )
        ).all()
    )
    return AnchorListResponse(
        items=[_anchor_view(row) for row in rows], total=int(total or 0)
    )


@router.get(
    "/{novel_id}/illustration-anchors/{anchor_id}",
    response_model=AnchorView,
)
async def get_illustration_anchor(
    anchor_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    row = await db.scalar(
        select(IllustrationAnchor).where(
            IllustrationAnchor.id == anchor_id,
            IllustrationAnchor.owner_id == current_user.id,
            IllustrationAnchor.novel_id == novel.id,
        )
    )
    if row is None:
        raise _not_found()
    return _anchor_view(row)
