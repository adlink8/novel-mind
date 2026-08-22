"""Phase 36-02 strict wire contracts for the derivative chapter plan (D-36-02).

The client can never widen the authority/lineage scope of a chapter: create and
patch requests carry only the editable plan fields (title/markdown/status) plus
the ``base_revision`` concurrency token, and every view echoes the server-sealed
``markdown_checksum`` / ``revision`` and the project's frozen fork scope
(fork/version/cutoff) so all responses are auditable on the wire.

No client-supplied ``owner_id`` / ``novel_id`` / ``project_id`` / ``revision`` /
``markdown_checksum`` / fork-lineage field is accepted (``extra="forbid"``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.derivative_project import DERIVATIVE_PROJECT_SPACE

# Stripped, non-empty title; Markdown budget limit guards autosave exhaustion
# (T-36-02-01 / security domain V5).
ChapterTitleStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
MarkdownStr = Annotated[str, StringConstraints(max_length=100_000)]


class StrictDerivativeChapterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeChapterStatus(StrEnum):
    DRAFT = "draft"
    ARCHIVED = "archived"


class DerivativeChapterCreate(StrictDerivativeChapterModel):
    """Client intent: a new plan/draft row appended at the end of the plan.

    ``position``/``revision``/``markdown_checksum`` and every authority/lineage
    field are derived and sealed by the server from the scoped project.
    """

    title: ChapterTitleStr
    markdown: MarkdownStr = ""
    status: DerivativeChapterStatus | None = None


class DerivativeChapterPatch(StrictDerivativeChapterModel):
    """Allowlisted patch: editable plan fields + optimistic-concurrency token.

    ``base_revision`` is required so a stale client (e.g. an old autosave) is
    rejected with the current revision/checksum instead of overwriting newer
    content. ``position`` is never patchable — reorder is a dedicated full-set
    endpoint with its own conflict gate.
    """

    title: ChapterTitleStr | None = None
    markdown: MarkdownStr | None = None
    status: DerivativeChapterStatus | None = None
    base_revision: int = Field(gt=0)

    @model_validator(mode="after")
    def require_at_least_one_field(self) -> DerivativeChapterPatch:
        if self.title is None and self.markdown is None and self.status is None:
            raise ValueError(
                "at least one of title, markdown or status is required with base_revision"
            )
        return self


class DerivativeChapterReorderRequest(StrictDerivativeChapterModel):
    """Full-set reorder intent: the project's chapter ids in the desired order.

    The server requires the list to be an exact permutation of the project's
    current chapter ids (missing/extras/duplicates/foreign ids all fail closed).
    """

    chapter_ids: list[int] = Field(min_length=1)


class DerivativeChapterView(StrictDerivativeChapterModel):
    """Owner-scoped chapter plan row with the sealed revision/checksum echoed."""

    id: int
    project_id: int
    owner_id: int
    novel_id: int
    position: int
    title: str
    markdown: str
    markdown_checksum: str
    status: DerivativeChapterStatus
    revision: int
    created_at: datetime
    updated_at: datetime


class DerivativeChapterScope(StrictDerivativeChapterModel):
    """The project's frozen fork/version/cutoff scope echoed on every list/detail.

    The chapter itself never widens the scope; it is always the project's sealed
    Fanfiction Canon fork lineage (D-36-01/D-36-03).
    """

    project_id: int
    owner_id: int
    novel_id: int
    fork_id: int
    space: str = DERIVATIVE_PROJECT_SPACE
    fork_key: str
    source_version_key: str
    through_chapter: int
    full_book_authorized: bool
    cutoff_snapshot_hash: str


class DerivativeChapterCreateResponse(StrictDerivativeChapterModel):
    chapter: DerivativeChapterView
    scope: DerivativeChapterScope
    message: str | None = None


class DerivativeChapterListResponse(StrictDerivativeChapterModel):
    project_id: int
    scope: DerivativeChapterScope
    total: int
    items: list[DerivativeChapterView] = Field(default_factory=list)
