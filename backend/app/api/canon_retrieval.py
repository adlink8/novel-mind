"""Branch-aware isolated retrieval and citation revalidation API (Phase 35-03).

REQ-FORK-01 / REQ-CRE-01 / D-35-01..D-35-03: every route is owner-scoped
through ``require_owned_novel`` (a mismatched owner/novel is an identical 404)
and builds a server-derived frozen ``CanonScope`` *before* any retrieval; the
client can never widen the scope. Blocked and absent outcomes are explicit —
never a fake successful empty array.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.services.canon_fork.citations import (
    CanonCitationRef,
    CanonCitationService,
    CitationVerdict,
)
from app.services.canon_fork.contracts import CanonSpace
from app.services.canon_fork.retrieval import (
    CanonRetrievalCandidate,
    CanonRetrievalService,
    RetrievalStatus,
    resolve_canon_scope,
)
from app.services.canon_fork.snapshot import CanonForkScopeError

router = APIRouter(dependencies=[Depends(require_user)])

PositiveIntQuery = Annotated[int, Query(gt=0, le=1000000)]
Hash64Query = Annotated[
    str,
    Query(
        pattern=r"^[0-9a-f]{64}$",
    ),
]


class CanonRetrievalResponse(BaseModel):
    status: Literal["completed", "blocked", "absent"]
    blocked_reason: str | None = None
    scope_hash: str
    trace: dict
    candidates: list[dict]
    message: str | None = None


class CanonCitationRevalidationRequest(BaseModel):
    """One frozen-scope citation revalidation request.

    The client supplies only the branch identity and the citation refs; owner/
    novel/version/cutoff/snapshot are server-derived from the frozen scope.
    """

    model_config = ConfigDict(extra="forbid")

    space: CanonSpace
    namespace: str | None = None
    version_key: str | None = None
    fork_id: int | None = Field(default=None, gt=0)
    through_chapter: int | None = Field(default=None, gt=0)
    full_book: bool = False
    expected_source_snapshot_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    citations: list[CanonCitationRef] = Field(min_length=1)


class CanonCitationRevalidationResponse(BaseModel):
    scope_hash: str
    allowed_count: int
    blocked_count: int
    verdicts: list[CitationVerdict]


def _map_error(exc: CanonForkScopeError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail=f"{exc.code}: {exc.detail}"
    )


def _candidate_view(candidate: CanonRetrievalCandidate) -> dict:
    return {
        "candidate_key": candidate.candidate_key,
        "space": candidate.space.value,
        "namespace": candidate.namespace,
        "version_key": candidate.version_key,
        "authority": candidate.authority.value,
        "citation_policy": candidate.citation_policy.value,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "chapter_number": candidate.chapter_number,
        "content_hash": candidate.content_hash,
        "artifact_id": candidate.artifact_id,
        "evidence_ref": candidate.evidence_ref,
    }


def _trace_view(trace) -> dict:
    return {
        "scope_hash": trace.scope_hash,
        "space": trace.space.value,
        "namespace": trace.namespace,
        "version_key": trace.version_key,
        "through_chapter": trace.through_chapter,
        "loaded_scoped_count": trace.loaded_scoped_count,
        "beyond_cutoff_count": trace.beyond_cutoff_count,
        "stale_snapshot_count": trace.stale_snapshot_count,
        "ranked_count": trace.ranked_count,
        "status": trace.status.value,
        "block_reason": trace.block_reason.value if trace.block_reason else None,
    }


@router.get(
    "/{novel_id}/canon-retrieval",
    response_model=CanonRetrievalResponse,
)
async def retrieve_canon(
    space: CanonSpace,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    namespace: str | None = Query(default=None, min_length=1, max_length=128),
    version_key: str | None = Query(default=None, min_length=1, max_length=128),
    fork_id: PositiveIntQuery | None = None,
    through_chapter: PositiveIntQuery | None = None,
    full_book: bool = False,
    expected_source_snapshot_hash: Hash64Query | None = None,
    limit: PositiveIntQuery = 32,
):
    """Branch-aware retrieval inside one frozen knowledge-space scope.

    The scope (owner/novel/version/cutoff/snapshot) is derived by the server
    from the owned novel and the requested branch; the client can only shrink,
    never expand it. Blocked and absent outcomes carry an explicit status and
    reason — never a fake successful empty array.
    """

    try:
        scope = await resolve_canon_scope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            user=current_user,
            space=space,
            namespace=namespace,
            version_key=version_key,
            fork_id=fork_id,
            through_chapter=through_chapter,
            full_book=full_book,
            expected_source_snapshot_hash=expected_source_snapshot_hash,
        )
        result = await CanonRetrievalService(db).retrieve(scope)
    except CanonForkScopeError as exc:
        raise _map_error(exc) from exc

    candidates = [_candidate_view(c) for c in result.candidates[:limit]]
    if result.trace.status is RetrievalStatus.COMPLETED:
        message = f"{len(candidates)} branch-scoped candidates retrieved"
    elif result.trace.status is RetrievalStatus.ABSENT:
        message = "no rows exist in the requested namespace (absent, not fake success)"
    else:
        message = "all namespace rows were inadmissible (blocked, not fake success)"
    return CanonRetrievalResponse(
        status=result.trace.status.value,
        blocked_reason=(
            result.trace.block_reason.value if result.trace.block_reason else None
        ),
        scope_hash=result.trace.scope_hash,
        trace=_trace_view(result.trace),
        candidates=candidates,
        message=message,
    )


@router.post(
    "/{novel_id}/canon-retrieval/revalidate",
    response_model=CanonCitationRevalidationResponse,
)
async def revalidate_canon_citations(
    body: CanonCitationRevalidationRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Revalidate citation refs against the frozen branch scope.

    Every citation is revalidated independently (owner / fork-version / cutoff /
    offset-hash). A citation that fails returns an auditable ``blocked_reason``;
    it is never resolved to a fake empty success.
    """

    try:
        scope = await resolve_canon_scope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            user=current_user,
            space=body.space,
            namespace=body.namespace,
            version_key=body.version_key,
            fork_id=body.fork_id,
            through_chapter=body.through_chapter,
            full_book=body.full_book,
            expected_source_snapshot_hash=body.expected_source_snapshot_hash,
        )
        verdicts = await CanonCitationService(db).revalidate_many(
            body.citations, scope=scope
        )
    except CanonForkScopeError as exc:
        raise _map_error(exc) from exc

    allowed = sum(1 for v in verdicts if v.allowed)
    return CanonCitationRevalidationResponse(
        scope_hash=scope.scope_hash(),
        allowed_count=allowed,
        blocked_count=len(verdicts) - allowed,
        verdicts=list(verdicts),
    )


__all__ = ["router"]
