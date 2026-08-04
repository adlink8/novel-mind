"""Canon Fork contracts API (Phase 35-02, REQ-FORK-01 / REQ-CRE-01).

POST/GET create and read *candidate* fork contracts. Every route is owner-scoped
through ``require_owned_novel`` (a mismatched owner/novel is an identical 404),
the cutoff is always derived by the server from owner/novel and explicit
authorization, and no mutation creates or switches a production ``active``
pointer (``active`` stays ``false``; publication status is always ``candidate``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.canon_fork import CanonFork
from app.services.canon_fork.contracts import Hash64, PositiveInt
from app.services.canon_fork.snapshot import (
    CanonForkScopeError,
    CanonForkSnapshotService,
)

router = APIRouter(dependencies=[Depends(require_user)])


# ---------------------------------------------------------------------------
# Wire contracts (strict, frozen; the client can never widen the scope)
# ---------------------------------------------------------------------------


class CanonForkCreateRequest(BaseModel):
    """Client intent for one candidate fork.

    The client supplies only the fork identity and its *request*; owner/novel,
    the Original Canon version, the source snapshot/hash and the effective
    cutoff are always derived and sealed by the server (D-35-03).
    """

    model_config = ConfigDict(extra="forbid")

    fork_key: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]
    requested_cutoff_chapter: PositiveInt | None = None
    full_book_requested: bool = False
    expected_source_snapshot_hash: Hash64 | None = None


class CanonForkView(BaseModel):
    """Frozen fork manifest as returned to the owner."""

    id: int
    fork_key: str
    owner_id: int
    novel_id: int
    space: str
    status: str
    source_version_key: str
    source_snapshot_id: str
    source_snapshot_hash: str
    through_chapter: int
    full_book_authorized: bool
    cutoff_snapshot_hash: str
    scope_hash: str
    manifest_hash: str
    citation_lineage: list[dict]
    authorization: dict
    active: bool
    created_at: str | None = None


class CanonForkResponse(BaseModel):
    fork: CanonForkView
    publication_status: str = "candidate"
    replayed: bool = False
    message: str | None = None


class CanonForkListResponse(BaseModel):
    novel_id: int
    forks: list[CanonForkView]
    publication_status: str = "candidate"
    message: str | None = None


def _map_error(exc: CanonForkScopeError) -> HTTPException:
    # Keep the machine-readable code in the response detail so a fail-closed
    # rejection stays auditable on the wire (mirrors the error code convention
    # used by the agent-tools facade).
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


def _to_view(row: CanonFork) -> CanonForkView:
    created = None
    if getattr(row, "created_at", None) is not None:
        created = row.created_at.isoformat()
    return CanonForkView(
        id=row.id,
        fork_key=row.fork_key,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        space=row.space,
        status=row.status,
        source_version_key=row.source_version_key,
        source_snapshot_id=row.source_snapshot_id,
        source_snapshot_hash=row.source_snapshot_hash,
        through_chapter=row.through_chapter,
        full_book_authorized=row.full_book_authorized,
        cutoff_snapshot_hash=row.cutoff_snapshot_hash,
        scope_hash=row.scope_hash,
        manifest_hash=row.manifest_hash,
        citation_lineage=list(row.citation_lineage or []),
        authorization=dict(row.authorization or {}),
        active=bool(row.active),
        created_at=created,
    )


# ---------------------------------------------------------------------------
# Candidate fork create / read (owner-scoped, candidate-only)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/canon-fork",
    response_model=CanonForkResponse,
    status_code=201,
)
async def create_canon_fork(
    body: CanonForkCreateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Create one immutable candidate fork (no active pointer is created)."""

    service = CanonForkSnapshotService(db)
    try:
        manifest = await service.freeze_manifest(
            owner_id=current_user.id,
            novel_id=novel.id,
            user=current_user,
            fork_key=body.fork_key,
            requested_cutoff_chapter=body.requested_cutoff_chapter,
            full_book_requested=body.full_book_requested,
            expected_source_snapshot_hash=body.expected_source_snapshot_hash,
        )
        row, replayed = await service.persist_fork(manifest=manifest)
    except CanonForkScopeError as exc:
        raise _map_error(exc) from exc

    if replayed:
        message = "identical fork intent replayed the sealed candidate"
    else:
        message = "candidate fork sealed (candidate_only)"
    return CanonForkResponse(
        fork=_to_view(row),
        publication_status="candidate",
        replayed=replayed,
        message=message,
    )


@router.get(
    "/{novel_id}/canon-fork/{fork_id}",
    response_model=CanonForkResponse,
)
async def get_canon_fork(
    fork_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Read one sealed candidate fork within the explicit owner/novel scope."""

    row = await db.scalar(
        select(CanonFork).where(
            CanonFork.owner_id == current_user.id,
            CanonFork.novel_id == novel.id,
            CanonFork.id == fork_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="canon fork not found")
    return CanonForkResponse(
        fork=_to_view(row),
        publication_status="candidate",
        replayed=False,
        message="frozen candidate fork manifest",
    )


@router.get(
    "/{novel_id}/canon-fork",
    response_model=CanonForkListResponse,
)
async def list_canon_forks(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List candidate forks for the owned novel (no default active pick)."""

    rows = list(
        (
            await db.scalars(
                select(CanonFork)
                .where(
                    CanonFork.owner_id == current_user.id,
                    CanonFork.novel_id == novel.id,
                )
                .order_by(CanonFork.id.desc())
            )
        ).all()
    )
    forks = [_to_view(row) for row in rows]
    message = None if forks else "no canon fork candidates"
    return CanonForkListResponse(
        novel_id=novel.id,
        forks=forks,
        publication_status="candidate",
        message=message,
    )


__all__ = ["router"]
