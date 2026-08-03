"""Owner-scoped SceneSpec candidate API (Phase 32-02, REQ-VIS-03).

Candidate-only, evidence-bounded, deterministic compiler endpoints:

- ``GET  /api/novels/{novel_id}/scene-specs`` — list compiled candidate specs.
- ``POST /api/novels/{novel_id}/scene-specs/preview`` — compile a preview from
  a frozen SceneCandidate plus the approved Visual Bible revision it was
  frozen against. Server-side scope/snapshot/cutoff/Visual Bible revalidation
  runs first; nothing is persisted and no provider/network call is made.
- ``POST /api/novels/{novel_id}/scene-specs`` — persist the compiled spec as
  an immutable candidate (append-only, idempotent replay; a conflicting
  retry of the same spec_key fails closed).
- ``GET  /api/novels/{novel_id}/scene-specs/{spec_id}`` — one candidate spec
  plus a ``stale`` flag: when the Visual Bible revision or source snapshot the
  spec was compiled against no longer matches the novel's current approved
  revision/snapshot, the spec is marked stale and cannot be silently reused.
- ``GET  /api/novels/{novel_id}/scene-specs/{spec_id}/diff`` — deterministic
  recompile diff against the current approved revision; every changed section
  is returned so a stale spec shows exactly what drifted.

Every route uses ``require_owned_novel``; a spec/candidate/version outside the
caller's owner/novel scope is indistinguishable from "not found". No route
promotes anything to Canon and no route invokes an image provider
(D-32-01/D-32-04).
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
    SceneSpecView,
    SceneUncertaintyView,
    StrictSceneSpecModel,
)
from app.services.scene_spec.compiler import (
    SceneSpecCompileError,
    SceneSpecConflict,
    SceneSpecNotFound,
    SceneSpecPreviewRequest as SceneSpecServiceRequest,
    SceneSpecService,
    SceneSpecServiceError,
)

router = APIRouter(dependencies=[Depends(require_user)])


class StrictWireModel(StrictSceneSpecModel):
    model_config = ConfigDict(extra="forbid")


class SceneSpecPreviewRequest(StrictWireModel):
    """Explicit compile request; scope comes from the path, never the body."""

    spec_key: str = Field(min_length=1, max_length=120)
    candidate_set_id: int = Field(gt=0)
    candidate_key: str = Field(min_length=1, max_length=180)
    visual_bible_version_id: int = Field(gt=0)
    source_snapshot_id: str = Field(min_length=1, max_length=160)
    revision_number: int = Field(default=1, ge=1)
    policy_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SceneSpecListResponse(StrictWireModel):
    items: list[SceneSpecView]
    total: int


class SceneSpecPreviewResponse(StrictWireModel):
    """Preview outcome: explicit no-persistence / no-provider marker."""

    spec: SceneSpecView
    uncertainties: list[SceneUncertaintyView] = Field(default_factory=list)
    provider_calls: int = 0
    persisted: bool = False


class SceneSpecCreateResponse(StrictWireModel):
    spec: SceneSpecView
    replayed: bool = False


class SceneSpecDetailResponse(StrictWireModel):
    spec: SceneSpecView
    stale: bool = False


class SceneSpecDiffSectionView(StrictWireModel):
    section_key: str
    original: str | None = None
    current: str | None = None


class SceneSpecDiffResponse(StrictWireModel):
    original_spec_hash: str
    current_spec_hash: str
    stale: bool = False
    same: bool = False
    changed_sections: list[SceneSpecDiffSectionView] = Field(default_factory=list)


def _not_found() -> HTTPException:
    # Identical to a missing novel so cross-owner probes cannot learn anything.
    return HTTPException(status_code=404, detail="小说不存在")


def _service_request(payload: SceneSpecPreviewRequest) -> SceneSpecServiceRequest:
    from app.services.scene_spec.compiler import (
        SCENE_SPEC_DEFAULT_POLICY_HASH,
    )

    return SceneSpecServiceRequest(
        spec_key=payload.spec_key,
        candidate_set_id=payload.candidate_set_id,
        candidate_key=payload.candidate_key,
        visual_bible_version_id=payload.visual_bible_version_id,
        source_snapshot_id=payload.source_snapshot_id,
        revision_number=payload.revision_number,
        policy_hash=payload.policy_hash or SCENE_SPEC_DEFAULT_POLICY_HASH,
        config_hash=payload.config_hash,
    )


# ---------------------------------------------------------------------------
# Read routes (candidate-only, owner-scoped)
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/scene-specs", response_model=SceneSpecListResponse)
async def get_scene_specs(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List every compiled candidate spec for the owned novel (oldest first)."""
    items = await SceneSpecService(db).list(
        owner_id=current_user.id, novel_id=novel.id
    )
    return SceneSpecListResponse(items=items, total=len(items))


@router.get(
    "/{novel_id}/scene-specs/{spec_id}",
    response_model=SceneSpecDetailResponse,
)
async def get_scene_spec(
    spec_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """One candidate spec plus its staleness marker (D-32-03)."""
    service = SceneSpecService(db)
    try:
        view, stale = await service.load(
            owner_id=current_user.id,
            novel_id=novel.id,
            spec_id=spec_id,
        )
    except SceneSpecNotFound:
        raise _not_found() from None
    return SceneSpecDetailResponse(spec=view, stale=stale)


@router.get(
    "/{novel_id}/scene-specs/{spec_id}/diff",
    response_model=SceneSpecDiffResponse,
)
async def get_scene_spec_diff(
    spec_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Deterministic recompile diff against the current approved revision."""
    service = SceneSpecService(db)
    try:
        result = await service.diff(
            owner_id=current_user.id,
            novel_id=novel.id,
            spec_id=spec_id,
        )
    except SceneSpecNotFound:
        raise _not_found() from None
    return SceneSpecDiffResponse(
        original_spec_hash=result.original_spec_hash,
        current_spec_hash=result.current_spec_hash,
        stale=result.stale,
        same=result.same,
        changed_sections=[
            SceneSpecDiffSectionView(
                section_key=section.section_key,
                original=section.original,
                current=section.current,
            )
            for section in result.changed_sections
        ],
    )


# ---------------------------------------------------------------------------
# Write routes (server-compiled, candidate-only, no provider)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/scene-specs/preview",
    response_model=SceneSpecPreviewResponse,
)
async def preview_scene_spec(
    payload: SceneSpecPreviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Compile a preview from a frozen candidate + approved Visual Bible.

    Server-side revalidation only; nothing is persisted and no provider/
    network call happens (D-32-04). ``provider_calls=0`` is explicit so the
    absence of provider work is never silently implied.
    """
    service = SceneSpecService(db)
    try:
        result = await service.preview(
            owner_id=current_user.id,
            novel_id=novel.id,
            request=_service_request(payload),
        )
    except SceneSpecNotFound:
        raise _not_found() from None
    except (SceneSpecServiceError, SceneSpecCompileError, SceneSpecConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SceneSpecPreviewResponse(
        spec=result.view,
        uncertainties=[
            SceneUncertaintyView(
                uncertainty_key=item.uncertainty_key,
                reason=item.reason,
                detail=item.detail,
            )
            for item in result.unresolved
        ],
        provider_calls=result.provider_calls,
        persisted=False,
    )


@router.post(
    "/{novel_id}/scene-specs",
    response_model=SceneSpecCreateResponse,
    status_code=201,
)
async def create_scene_spec(
    payload: SceneSpecPreviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Persist one compiled candidate spec (append-only, idempotent replay)."""
    # Capture scope as plain ints before the write seam runs: an idempotent
    # replay rolls the session back internally.
    owner_id = current_user.id
    novel_id = novel.id
    service = SceneSpecService(db)
    try:
        persisted = await service.create(
            owner_id=owner_id,
            novel_id=novel_id,
            request=_service_request(payload),
        )
    except SceneSpecNotFound:
        raise _not_found() from None
    except (SceneSpecServiceError, SceneSpecCompileError, SceneSpecConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SceneSpecCreateResponse(spec=persisted.view, replayed=persisted.replayed)
