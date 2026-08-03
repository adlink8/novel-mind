"""Owner-scoped PromptRevision candidate API (Phase 32-03, REQ-VIS-03).

Candidate-only, provider-neutral → provider-specific compiled prompt endpoints:

- ``GET  /api/novels/{novel_id}/prompt-revisions`` — list compiled prompt
  candidates for the owned novel.
- ``GET  /api/novels/{novel_id}/prompt-revisions/{revision_id}`` — one prompt
  candidate plus a ``stale`` flag: when the SceneSpec's Visual Bible revision
  or source snapshot no longer matches the novel's current approved state, the
  prompt is marked stale and cannot be silently reused.
- ``POST /api/novels/{novel_id}/prompt-revisions/preview`` — compile a prompt
  preview from an owner-scoped SceneSpec through a configured provider adapter.
  Server-side scope/lineage revalidation runs first; nothing is persisted and
  no provider/network call is made (D-32-04).
- ``POST /api/novels/{novel_id}/prompt-revisions`` — persist the compiled
  prompt as an immutable candidate (append-only, idempotent replay; a
  conflicting prompt_key retry fails closed).
- ``POST /api/novels/{novel_id}/prompt-revisions/{revision_id}/edit`` — apply a
  human edit to a ``user_interpretation`` detail and produce an explicit new
  candidate revision (new prompt_key, parent link, retained diff). Unsupported
  edits fail closed; no image provider is called.
- ``GET  /api/novels/{novel_id}/prompt-revisions/{revision_id}/diff`` —
  deterministic diff against the revision's parent (auditable edit lineage).
- ``POST /api/novels/{novel_id}/prompt-revisions/{revision_id}/review`` —
  append one explicit, idempotent review action (approve/reject/supersede/
  needs_relink). The server re-verifies owner/novel/revision scope, the
  ``from_review_state``, the legal transition and (for approvals) the stale/
  hash approval gate; approval only marks the PromptRevision as an approved
  Phase 33 input (D-32-04).
- ``GET  /api/novels/{novel_id}/prompt-revisions/{revision_id}/history`` —
  the append-only review event history plus the current state, staleness
  marker and approval-gate reason codes.

Every route uses ``require_owned_novel``; a revision/spec outside the caller's
owner/novel scope is indistinguishable from "not found". No route invokes an
image provider (D-32-01/D-32-04).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.scene_spec import (
    PromptArtifactLineage,
    PromptRevisionView,
    SceneSpecGateError,
    SpecActorSource,
    SpecDetailKind,
    SpecReviewAction,
    SpecReviewEventInput,
    SpecReviewState,
    StrictSceneSpecModel,
)
from app.services.prompt_compiler.adapters import (
    MOCK_PROMPT_ADAPTER_ID,
    PromptCompileError,
    PromptCompileRequest as PromptCompileServiceRequest,
    PromptEditInput,
    PromptRevisionConflict,
    PromptRevisionNotFound,
    PromptRevisionService,
    PromptRevisionServiceError,
)
from app.services.prompt_compiler.revisions import (
    PromptRevisionReviewEnvelope,
    PromptRevisionReviewService,
    PromptReviewConflict,
    PromptReviewNotFound,
    build_review_envelope,
)

router = APIRouter(dependencies=[Depends(require_user)])


class StrictWireModel(StrictSceneSpecModel):
    model_config = ConfigDict(extra="forbid")


class PromptCompileRequest(StrictWireModel):
    """Explicit compile request; scope comes from the path, never the body."""

    spec_id: int = Field(gt=0)
    prompt_key: str = Field(min_length=1, max_length=120)
    adapter_id: str = Field(default=MOCK_PROMPT_ADAPTER_ID, min_length=1, max_length=120)
    revision_number: int = Field(default=1, ge=1)
    parent_prompt_revision_id: int | None = Field(default=None, gt=0)


class PromptEditRequest(StrictWireModel):
    """Human edit: only user_interpretation details are editable through the
    prompt seam; the edited prompt_key is an explicit new candidate key."""

    prompt_key: str = Field(min_length=1, max_length=120)
    detail_key: str = Field(min_length=1, max_length=180)
    kind: SpecDetailKind = SpecDetailKind.STYLE
    text: str = Field(min_length=1, max_length=4000)
    author: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)


class PromptListResponse(StrictWireModel):
    items: list[PromptRevisionView]
    total: int


class PromptArtifactView(StrictWireModel):
    """Compiled prompt + deterministic lineage envelope (D-32-03)."""

    revision: PromptRevisionView
    lineage: PromptArtifactLineage
    provider_calls: int = 0


class PromptCreateResponse(StrictWireModel):
    revision: PromptRevisionView
    lineage: PromptArtifactLineage
    replayed: bool = False


class PromptDetailResponse(StrictWireModel):
    revision: PromptRevisionView
    stale: bool = False


class PromptDiffSectionView(StrictWireModel):
    section_key: str
    original: str | None = None
    current: str | None = None


class PromptListDiffItemView(StrictWireModel):
    item: str
    original_count: int | None = None
    current_count: int | None = None


class PromptDiffResponse(StrictWireModel):
    original_prompt_hash: str
    current_prompt_hash: str
    parent_prompt_revision_id: int | None = None
    revision_number: int
    same: bool = False
    changed_sections: list[PromptDiffSectionView] = Field(default_factory=list)
    changed_negative_constraints: list[PromptListDiffItemView] = Field(
        default_factory=list
    )
    changed_uncertainties: list[PromptListDiffItemView] = Field(
        default_factory=list
    )
    prompt_text_changed: bool = False


class PromptEditResponse(StrictWireModel):
    revision: PromptRevisionView
    diff: PromptDiffResponse


class PromptReviewRequest(StrictWireModel):
    """One explicit review action; scope and legality are decided server-side."""

    action: SpecReviewAction
    actor_source: SpecActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    event_key: str = Field(min_length=1, max_length=160)
    from_review_state: SpecReviewState


def _not_found() -> HTTPException:
    # Identical to a missing novel so cross-owner probes cannot learn anything.
    return HTTPException(status_code=404, detail="小说不存在")


def _service_request(payload: PromptCompileRequest) -> PromptCompileServiceRequest:
    return PromptCompileServiceRequest(
        spec_id=payload.spec_id,
        prompt_key=payload.prompt_key,
        adapter_id=payload.adapter_id,
        revision_number=payload.revision_number,
        parent_prompt_revision_id=payload.parent_prompt_revision_id,
    )


def _artifact_view(artifact) -> PromptArtifactView:
    return PromptArtifactView(
        revision=artifact.revision,
        lineage=artifact.lineage,
        provider_calls=artifact.provider_calls,
    )


def _diff_view(diff) -> PromptDiffResponse:
    return PromptDiffResponse(
        original_prompt_hash=diff.original_prompt_hash,
        current_prompt_hash=diff.current_prompt_hash,
        parent_prompt_revision_id=diff.parent_prompt_revision_id,
        revision_number=diff.revision_number,
        same=diff.same,
        changed_sections=[
            PromptDiffSectionView(
                section_key=section.section_key,
                original=section.original,
                current=section.current,
            )
            for section in diff.changed_sections
        ],
        changed_negative_constraints=[
            PromptListDiffItemView(
                item=item,
                original_count=original_count,
                current_count=current_count,
            )
            for item, original_count, current_count in diff.changed_negative_constraints
        ],
        changed_uncertainties=[
            PromptListDiffItemView(
                item=item,
                original_count=original_count,
                current_count=current_count,
            )
            for item, original_count, current_count in diff.changed_uncertainties
        ],
        prompt_text_changed=diff.prompt_text_changed,
    )


# ---------------------------------------------------------------------------
# Read routes (candidate-only, owner-scoped)
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/prompt-revisions", response_model=PromptListResponse)
async def get_prompt_revisions(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List every compiled prompt candidate for the owned novel (oldest first)."""
    items = await PromptRevisionService(db).list(
        owner_id=current_user.id, novel_id=novel.id
    )
    return PromptListResponse(items=items, total=len(items))


@router.get(
    "/{novel_id}/prompt-revisions/{revision_id}",
    response_model=PromptDetailResponse,
)
async def get_prompt_revision(
    revision_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """One compiled prompt candidate plus its staleness marker (D-32-03)."""
    service = PromptRevisionService(db)
    try:
        view, stale = await service.load(
            owner_id=current_user.id,
            novel_id=novel.id,
            revision_id=revision_id,
        )
    except PromptRevisionNotFound:
        raise _not_found() from None
    return PromptDetailResponse(revision=view, stale=stale)


@router.get(
    "/{novel_id}/prompt-revisions/{revision_id}/diff",
    response_model=PromptDiffResponse,
)
async def get_prompt_revision_diff(
    revision_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Deterministic edit diff against the revision's parent (D-32-04)."""
    service = PromptRevisionService(db)
    try:
        diff = await service.diff(
            owner_id=current_user.id,
            novel_id=novel.id,
            revision_id=revision_id,
        )
    except PromptRevisionNotFound:
        raise _not_found() from None
    except PromptRevisionServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _diff_view(diff)


# ---------------------------------------------------------------------------
# Write routes (server-compiled, candidate-only, no provider)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/prompt-revisions/preview",
    response_model=PromptArtifactView,
)
async def preview_prompt_revision(
    payload: PromptCompileRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Compile a prompt preview from an owner-scoped SceneSpec.

    Server-side revalidation only; nothing is persisted and no provider/network
    call happens (D-32-04). ``provider_calls=0`` is explicit so the absence of
    provider work is never silently implied.
    """
    service = PromptRevisionService(db)
    try:
        artifact = await service.preview(
            owner_id=current_user.id,
            novel_id=novel.id,
            request=_service_request(payload),
        )
    except PromptRevisionNotFound:
        raise _not_found() from None
    except (PromptRevisionServiceError, PromptCompileError, PromptRevisionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _artifact_view(artifact)


@router.post(
    "/{novel_id}/prompt-revisions",
    response_model=PromptCreateResponse,
    status_code=201,
)
async def create_prompt_revision(
    payload: PromptCompileRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Persist one compiled prompt candidate (append-only, idempotent replay)."""
    # Capture scope as plain ints before the write seam runs: an idempotent
    # replay rolls the session back internally.
    owner_id = current_user.id
    novel_id = novel.id
    service = PromptRevisionService(db)
    try:
        persisted = await service.create(
            owner_id=owner_id,
            novel_id=novel_id,
            request=_service_request(payload),
        )
    except PromptRevisionNotFound:
        raise _not_found() from None
    except (PromptRevisionServiceError, PromptCompileError, PromptRevisionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PromptCreateResponse(
        revision=persisted.view,
        lineage=PromptArtifactLineage(
            scene_spec_hash=persisted.revision.scene_spec_hash,
            visual_bible_revision_hash=persisted.revision.visual_bible_revision_hash,
            source_snapshot_id=persisted.revision.source_snapshot_id,
            source_snapshot_hash=persisted.revision.source_snapshot_hash,
            cutoff_chapter=persisted.revision.cutoff_chapter,
            schema_hash=persisted.revision.schema_hash,
            prompt_schema_hash=persisted.revision.prompt_schema_hash,
            compiler_version=persisted.revision.compiler_version,
            adapter_id=persisted.revision.adapter_id,
            adapter_version=persisted.revision.adapter_version,
            config_hash=persisted.revision.config_hash,
            input_hash=persisted.revision.input_hash,
            prompt_hash=persisted.revision.prompt_hash,
        ),
        replayed=persisted.replayed,
    )


@router.post(
    "/{novel_id}/prompt-revisions/{revision_id}/edit",
    response_model=PromptEditResponse,
    status_code=201,
)
async def edit_prompt_revision(
    revision_id: int,
    payload: PromptEditRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Apply a human edit and produce an explicit new candidate revision.

    Only ``user_interpretation`` details can change through the prompt seam; the
    edited prompt gets a new prompt_key, ``revision_number = parent + 1`` and a
    ``parent_prompt_revision_id`` link so the diff is fully auditable. No image
    provider is invoked (D-32-04).
    """
    # Capture scope as plain ints before the write seam runs.
    owner_id = current_user.id
    novel_id = novel.id
    service = PromptRevisionService(db)
    try:
        result = await service.edit(
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=revision_id,
            prompt_key=payload.prompt_key,
            edit=PromptEditInput(
                detail_key=payload.detail_key,
                kind=payload.kind,
                text=payload.text,
                author=payload.author,
                rationale=payload.rationale,
            ),
        )
    except PromptRevisionNotFound:
        raise _not_found() from None
    except (PromptRevisionServiceError, PromptCompileError, PromptRevisionConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PromptEditResponse(revision=result.view, diff=_diff_view(result.diff))


# ---------------------------------------------------------------------------
# Review routes (append-only, explicit, owner/version/hash gated)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/prompt-revisions/{revision_id}/review",
    response_model=PromptRevisionReviewEnvelope,
)
async def review_prompt_revision(
    revision_id: int,
    payload: PromptReviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Append one explicit, idempotent review action (approve/reject/supersede/
    needs_relink) on a compiled prompt candidate.

    The server re-verifies owner/novel/revision scope, the decision's
    ``from_review_state``, the legal transition and (for approvals) the stale/
    hash approval gate. A repeated ``event_key`` replays the existing event and
    never appends a second one. Approval only marks the PromptRevision as an
    approved Phase 33 input; the SceneSpec and the original source are never
    rewritten and no image provider is called (D-32-04).
    """
    owner_id = current_user.id
    novel_id = novel.id
    event = SpecReviewEventInput(
        owner_id=owner_id,
        novel_id=novel_id,
        revision_id=revision_id,
        event_key=payload.event_key,
        action=payload.action,
        actor_source=payload.actor_source,
        actor=payload.actor,
        reason=payload.reason,
        from_review_state=payload.from_review_state,
    )
    review = PromptRevisionReviewService(db)
    try:
        return await review.append_event(
            owner_id=owner_id, novel_id=novel_id, event=event
        )
    except PromptReviewNotFound:
        raise _not_found() from None
    except (PromptReviewConflict, SceneSpecGateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/{novel_id}/prompt-revisions/{revision_id}/history",
    response_model=PromptRevisionReviewEnvelope,
)
async def get_prompt_revision_history(
    revision_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Append-only review event history plus current state, staleness marker
    and (for an approvable candidate) the approval-gate reason codes."""
    try:
        return await build_review_envelope(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            revision_id=revision_id,
        )
    except PromptReviewNotFound:
        raise _not_found() from None
