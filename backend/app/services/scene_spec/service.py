"""Owner-scoped SceneSpec service seam (preview / create / read / diff).

Extracted from the scene-spec compiler (Phase 32-02, REQ-VIS-03): this module
owns the server-side gates — candidate-only frozen sets, approved Visual Bible
revision revalidation, snapshot/cutoff lineage, append-only persistence with
idempotent replay, stale-spec detection when the Visual Bible or source
snapshot drifts, and deterministic recompile diffs. preview never writes and
never calls a provider (Phase 32-04 boundary).

Refactor split: the DB seam is composed from three mixins that never import
this facade — ``CompileMixin`` (``_service_compile``), ``MutationMixin``
(``_service_mutations``), ``ReadQueryMixin`` (``_service_queries``). Shared
DTOs live in ``service_models`` and pure session-free helpers in
``service_primitives`` (both leaves). Every top-level symbol of the pre-split
module is re-exported here so the public import surface is unchanged.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ._service_compile import CompileMixin
from ._service_mutations import MutationMixin
from ._service_queries import ReadQueryMixin
from .service_models import (
    PersistedSceneSpec,
    SceneSpecDiffResult,
    SceneSpecDiffSection,
    SceneSpecPreviewRequest,
    SceneSpecPreviewResult,
)
from .service_primitives import _reconstruct_candidate

__all__ = [
    # DTOs re-exported from the service_models leaf (unchanged import surface).
    "PersistedSceneSpec",
    "SceneSpecDiffResult",
    "SceneSpecDiffSection",
    "SceneSpecPreviewRequest",
    "SceneSpecPreviewResult",
    # Pure helper re-exported from the service_primitives leaf.
    "_reconstruct_candidate",
    "SceneSpecService",
]


class SceneSpecService(CompileMixin, MutationMixin, ReadQueryMixin):
    """Owner-scoped SceneSpec read/preview/create/diff seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
