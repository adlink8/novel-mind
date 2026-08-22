"""Scene-spec shared error types (compiler + service seam, Phase 32-02).

The compiler and the owner-scoped ``SceneSpecService`` share the same
fail-closed error surface; this module keeps the hierarchy dependency-free so
``compiler.py``, ``prompt_builder.py`` and ``service.py`` can import it
without circular imports. ``compiler.py`` re-exports these names to preserve
the historical public import surface.
"""


class SceneSpecCompileError(ValueError):
    """Fail-closed compiler gate violation; no spec is ever produced."""


class SceneSpecServiceError(ValueError):
    """Base class for fail-closed scene-spec service errors."""


class SceneSpecNotFound(SceneSpecServiceError):
    """A spec/candidate/version is outside the explicit owner/novel scope."""


class SceneSpecConflict(SceneSpecServiceError):
    """A conflicting retry of an existing immutable spec_key."""
