"""Scene-spec service seam DTOs (preview / create / read / diff).

Shared request/result types for the ``SceneSpecService`` mixin seams. Moved to
a dependency-free leaf so ``_service_compile`` / ``_service_mutations`` /
``_service_queries`` can construct them without a cycle back to ``service.py``.
The facade re-exports every name here unchanged; ``compiler.py`` lazy
re-exports the same names from ``.service``, so the historical public import
surface is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.scene_spec import SceneSpecVersion as SceneSpecVersionRow
from app.schemas.scene_spec import SceneSpecContract, SceneSpecView

from .compiler import SCENE_SPEC_DEFAULT_POLICY_HASH, CompileUnresolved


@dataclass(frozen=True)
class SceneSpecPreviewRequest:
    """Server-side preview/create request; scope comes from the caller path."""

    spec_key: str
    candidate_set_id: int
    candidate_key: str
    visual_bible_version_id: int
    source_snapshot_id: str
    revision_number: int = 1
    policy_hash: str = SCENE_SPEC_DEFAULT_POLICY_HASH
    config_hash: str | None = None


@dataclass(frozen=True)
class SceneSpecPreviewResult:
    """Preview outcome: no persistence and no provider call (Phase 32-04)."""

    spec: SceneSpecContract
    view: SceneSpecView
    unresolved: tuple[CompileUnresolved, ...] = ()
    provider_calls: int = 0


@dataclass(frozen=True)
class PersistedSceneSpec:
    """Create outcome: the persisted version row plus replay flag."""

    version: SceneSpecVersionRow
    view: SceneSpecView
    replayed: bool = False


@dataclass(frozen=True)
class SceneSpecDiffSection:
    """One canonical section whose rendering changed between two compiles."""

    section_key: str
    original: str | None = None
    current: str | None = None


@dataclass(frozen=True)
class SceneSpecDiffResult:
    """Deterministic recompile diff + stale marker (D-32-03)."""

    original_spec_hash: str
    current_spec_hash: str
    stale: bool
    same: bool
    changed_sections: tuple[SceneSpecDiffSection, ...] = ()
