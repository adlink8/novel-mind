"""Phase 36-03 strict wire contracts for the revision/autosave surface (D-36-02).

The client can never widen the authority/lineage scope of a revision: autosave
and rollback requests carry only the editable draft content plus the
optimistic-concurrency token (``base_revision``) and — for rollback — the
target revision id and an optional human reason. Every view echoes the
server-sealed ``revision_number``, ``content_checksum``, ``kind``,
``actor_id``, ``reason`` and ``approval_state`` so history/diff/rollback stays
auditable on the wire.

No client-supplied ``owner_id`` / ``novel_id`` / ``project_id`` / ``chapter_id``
/ ``revision_number`` / ``content_checksum`` / ``kind`` / ``approval_state`` is
accepted (``extra="forbid"``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.models.derivative_revision import (
    DERIVATIVE_REVISION_APPROVAL_STATES,
    DERIVATIVE_REVISION_KINDS,
)
from app.schemas.derivative_chapter import DerivativeChapterView, MarkdownStr

# Rollback reason budget guards the audit journal (security domain V5).
RevisionReasonStr = Annotated[str, StringConstraints(max_length=2_000)]


class StrictDerivativeRevisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeRevisionKind(StrEnum):
    CREATE = "create"
    AUTOSAVE = "autosave"
    ROLLBACK = "rollback"


class DerivativeRevisionApproval(StrEnum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"


class DerivativeAutosaveRequest(StrictDerivativeRevisionModel):
    """Client intent: save the current Markdown draft under a CAS token.

    ``base_revision`` is required so a stale client (e.g. an old autosave tab)
    is rejected with the current revision/checksum instead of overwriting newer
    content. The server returns 200 (``saved``/``noop``) or 409 with the latest
    revision — never last-write-wins.
    """

    content: MarkdownStr
    base_revision: int = Field(gt=0)


class DerivativeRollbackRequest(StrictDerivativeRevisionModel):
    """Client intent: restore a historical revision as a NEW child snapshot.

    The rollback is itself an immutable ``rollback`` row whose parent is the
    current head, so history is never overwritten. ``base_revision`` guards the
    write like any other draft write; ``target_revision_id`` selects the exact
    historical row to restore and ``reason`` feeds the audit journal.
    """

    target_revision_id: int = Field(gt=0)
    reason: RevisionReasonStr | None = None
    base_revision: int = Field(gt=0)


class DerivativeRevisionView(StrictDerivativeRevisionModel):
    """Full immutable revision row, including the canonical Markdown snapshot."""

    id: int
    chapter_id: int
    project_id: int
    owner_id: int
    novel_id: int
    revision_number: int
    parent_revision_id: int | None
    kind: DerivativeRevisionKind
    content: str
    content_checksum: str
    actor_id: int | None
    reason: str | None
    approval_state: DerivativeRevisionApproval
    created_at: datetime
    updated_at: datetime


class DerivativeRevisionSummary(StrictDerivativeRevisionModel):
    """History row without the full content (keeps the listing lean)."""

    id: int
    chapter_id: int
    project_id: int
    revision_number: int
    parent_revision_id: int | None
    kind: DerivativeRevisionKind
    content_checksum: str
    actor_id: int | None
    reason: str | None
    approval_state: DerivativeRevisionApproval
    created_at: datetime


class DerivativeRevisionHistoryResponse(StrictDerivativeRevisionModel):
    """Newest-first append-only history for one chapter."""

    chapter_id: int
    project_id: int
    total: int
    items: list[DerivativeRevisionSummary] = Field(default_factory=list)


class DerivativeAutosaveResponse(StrictDerivativeRevisionModel):
    """Autosave acknowledgement with the resulting head state.

    ``status`` is ``saved`` (a new immutable row was appended) or ``noop`` (the
    submitted content was already the head — a duplicate/retry resolves
    idempotently without a new row).
    """

    status: Literal["saved", "noop"]
    chapter: DerivativeChapterView
    revision: DerivativeRevisionView
    message: str | None = None


class DerivativeRollbackResponse(StrictDerivativeRevisionModel):
    """Rollback acknowledgement: a new child revision now holds the target."""

    chapter: DerivativeChapterView
    revision: DerivativeRevisionView
    target_revision_id: int
    message: str | None = None


class DerivativeDiffLine(StrictDerivativeRevisionModel):
    """One deterministic diff line inside a hunk."""

    op: Literal["context", "add", "delete"]
    text: str


class DerivativeDiffHunk(StrictDerivativeRevisionModel):
    """One contiguous changed region (unified-diff style, 1-based line numbers)."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DerivativeDiffLine] = Field(default_factory=list)


class DerivativeDiffResponse(StrictDerivativeRevisionModel):
    """Deterministic canonical-Markdown diff between two revision rows."""

    base_revision_id: int
    base_revision_number: int
    target_revision_id: int
    target_revision_number: int
    additions: int
    deletions: int
    hunks: list[DerivativeDiffHunk] = Field(default_factory=list)


__all__ = [
    "DERIVATIVE_REVISION_APPROVAL_STATES",
    "DERIVATIVE_REVISION_KINDS",
    "DerivativeAutosaveRequest",
    "DerivativeAutosaveResponse",
    "DerivativeDiffHunk",
    "DerivativeDiffLine",
    "DerivativeDiffResponse",
    "DerivativeRevisionApproval",
    "DerivativeRevisionHistoryResponse",
    "DerivativeRevisionKind",
    "DerivativeRevisionSummary",
    "DerivativeRevisionView",
    "DerivativeRollbackRequest",
    "DerivativeRollbackResponse",
]
