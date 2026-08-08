"""Fail-closed error vocabulary for the provider prompt adapter stack.

Leaf module (no project-internal imports): every gate that refuses to produce
or persist a prompt candidate raises one of these instead of degrading.

- ``PromptCompileError`` — fail-closed adapter/compile gate violation; no prompt
  is ever produced (unknown adapter, derived-revision contract gate failure).
- ``PromptRevisionServiceError`` — base class for the owner-scoped service
  seam's fail-closed errors.
- ``PromptRevisionNotFound`` — a spec/revision is outside the explicit
  owner/novel scope.
- ``PromptRevisionConflict`` — a conflicting retry of an existing immutable
  ``prompt_key``.

Split note: extracted from ``adapters.py`` so the pure compile core
(``_adapter_core``) and the DB-backed service seam (``_adapter_service``) share
one error vocabulary without importing each other.
"""


class PromptCompileError(ValueError):
    """Fail-closed adapter/compile gate violation; no prompt is ever produced."""


class PromptRevisionServiceError(ValueError):
    """Base class for fail-closed prompt-revision service errors."""


class PromptRevisionNotFound(PromptRevisionServiceError):
    """A spec/revision is outside the explicit owner/novel scope."""


class PromptRevisionConflict(PromptRevisionServiceError):
    """A conflicting retry of an existing immutable prompt_key."""
