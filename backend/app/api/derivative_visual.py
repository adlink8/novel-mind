"""Owner-scoped derivative visual API (Phase 38-01 + 38-02, D-38-01/02/03).

Routes hang under ``/api/novels/{novel_id}/derivative-visual``:

- ``GET /versions`` / ``GET /versions/{version_id}`` — read the approved/candidate
  derivative Visual Bible fork lineage (candidate-only, owner-scoped; never an
  Original Visual Bible row).
- ``POST /scene-specs/compile`` — compile the canonical derivative Scene Spec
  for an approved fork version + an approved sealed SceneSpec. The response is
  either the frozen spec (the only provider input) or an auditable blocked
  report with every deterministic gate check (D-38-03).
- ``GET /scene-specs/{version_id}`` — deterministic read seam: recompile the
  same spec and replay its content hash (a drifted upstream contract fails
  closed instead of returning a stale spec).

Every route starts from ``require_owned_novel`` so a mismatched owner/novel is
an identical 404, and the client can never supply owner/novel/fork/project/
namespace/approval (strict ``extra="forbid"`` DTOs). Nothing here calls a
provider and nothing writes to the Original Visual Bible tables.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.derivative_visual import (
    DerivativeSceneSpecCompileRequest,
    DerivativeSceneSpecCompileResponse,
    DerivativeSceneSpecGateCheckView,
    DerivativeVisualVersionView,
)
from app.services.derivative_visual.gates import (
    DerivativeSceneSpecBlockedError,
    DerivativeSceneSpecGateError,
    DerivativeSceneSpecScopeError,
    GateCheck,
)
from app.services.derivative_visual.lineage import (
    DerivativeVisualLineageError,
    DerivativeVisualScopeMismatchError,
    DerivativeVisualVersionNotFoundError,
    list_versions,
    load_version_view,
)
from app.services.derivative_visual.scene_spec import (
    CompiledDerivativeSceneSpec,
    DerivativeSceneSpecService,
)

router = APIRouter(dependencies=[Depends(require_user)])

DERIVATIVE_VISUAL_PATH = "/{novel_id}/derivative-visual"


def _to_check_view(check: GateCheck) -> DerivativeSceneSpecGateCheckView:
    return DerivativeSceneSpecGateCheckView(
        gate=check.gate,
        code=check.code,
        ok=check.ok,
        detail=check.detail,
    )


def _blocked_detail(exc: DerivativeSceneSpecGateError) -> dict:
    return {
        "code": exc.code,
        "message": exc.detail,
        "gate_checks": [_to_check_view(c).model_dump() for c in exc.checks],
    }


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DerivativeSceneSpecScopeError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (DerivativeSceneSpecBlockedError, DerivativeSceneSpecGateError)):
        return HTTPException(status_code=409, detail=_blocked_detail(exc))
    if isinstance(exc, DerivativeVisualVersionNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DerivativeVisualScopeMismatchError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, DerivativeVisualLineageError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _compile_response(result: CompiledDerivativeSceneSpec) -> DerivativeSceneSpecCompileResponse:
    return DerivativeSceneSpecCompileResponse(
        spec=result.spec,
        content_hash=result.spec.content_hash,
        gate_checks=[_to_check_view(check) for check in result.gate_checks],
        blocked=False,
        block_reason=None,
    )


# ---------------------------------------------------------------------------
# Derivative visual fork lineage (read-only, candidate-only)
# ---------------------------------------------------------------------------


@router.get(
    DERIVATIVE_VISUAL_PATH + "/versions",
    response_model=list[DerivativeVisualVersionView],
)
async def list_derivative_visual_versions(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> list[DerivativeVisualVersionView]:
    """List every derivative visual fork version for the owned novel."""
    try:
        return await list_versions(
            db, owner_id=current_user.id, novel_id=novel.id
        )
    except DerivativeVisualLineageError as exc:
        raise _map_error(exc) from exc


@router.get(
    DERIVATIVE_VISUAL_PATH + "/versions/{version_id}",
    response_model=DerivativeVisualVersionView,
)
async def load_derivative_visual_version(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeVisualVersionView:
    """Load one derivative visual fork version (404-equivalent on scope)."""
    try:
        return await load_version_view(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            version_id=version_id,
        )
    except DerivativeVisualLineageError as exc:
        raise _map_error(exc) from exc


# ---------------------------------------------------------------------------
# Derivative Scene Spec compile (the only provider input, D-38-03)
# ---------------------------------------------------------------------------


@router.post(
    DERIVATIVE_VISUAL_PATH + "/scene-specs/compile",
    response_model=DerivativeSceneSpecCompileResponse,
)
async def compile_derivative_scene_spec(
    body: DerivativeSceneSpecCompileRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeSceneSpecCompileResponse:
    """Compile the canonical derivative Scene Spec for an approved fork + spec.

    Returns the frozen spec (the only provider input) on success; a missing or
    unapproved upstream contract or a failing gate returns 409 with the
    auditable ``gate_checks`` report and never compiles or calls a provider.
    """
    service = DerivativeSceneSpecService(db)
    try:
        result = await service.compile(
            owner_id=current_user.id,
            novel_id=novel.id,
            request=body,
        )
    except DerivativeSceneSpecGateError as exc:
        raise _map_error(exc) from exc
    return _compile_response(result)


@router.get(
    DERIVATIVE_VISUAL_PATH + "/scene-specs/{version_id}",
    response_model=DerivativeSceneSpecCompileResponse,
)
async def read_derivative_scene_spec(
    version_id: int,
    scene_spec_id: int = Query(gt=0),
    spec_key: str = Query(min_length=1, max_length=160),
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> DerivativeSceneSpecCompileResponse:
    """Deterministic read seam: recompile and replay the canonical spec hash.

    The same frozen inputs always produce the same spec; a drifted upstream
    contract fails closed (409) instead of returning a stale spec.
    """
    service = DerivativeSceneSpecService(db)
    request = DerivativeSceneSpecCompileRequest(
        version_id=version_id,
        scene_spec_id=scene_spec_id,
        spec_key=spec_key,
    )
    try:
        result = await service.compile(
            owner_id=current_user.id,
            novel_id=novel.id,
            request=request,
        )
    except DerivativeSceneSpecGateError as exc:
        raise _map_error(exc) from exc
    return _compile_response(result)


__all__ = ["router"]
