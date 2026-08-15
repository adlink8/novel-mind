"""Derivative chapter/revision view builders (leaf).

Extracted from ``revisions.py`` (refactor split): the ORM-row → Pydantic-view
mappers are pure functions shared by the revision service (autosave/rollback
return values) and the API error mapping (``current_revision`` serialization).
Leaf by construction — imports only models + schemas, never ``revisions.py``.
The revision facade re-exports these names so the
``app.services.derivative_editor.revisions`` import surface is unchanged.
"""

from __future__ import annotations

from app.models.derivative_chapter import DerivativeChapter
from app.models.derivative_revision import DerivativeRevision
from app.schemas.derivative_chapter import (
    DerivativeChapterStatus,
    DerivativeChapterView,
)
from app.schemas.derivative_revision import (
    DerivativeRevisionApproval,
    DerivativeRevisionKind,
    DerivativeRevisionSummary,
    DerivativeRevisionView,
)


def to_chapter_view(row: DerivativeChapter) -> DerivativeChapterView:
    return DerivativeChapterView(
        id=row.id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        position=row.position,
        title=row.title,
        markdown=row.markdown,
        markdown_checksum=row.markdown_checksum,
        status=DerivativeChapterStatus(row.status),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_revision_view(row: DerivativeRevision) -> DerivativeRevisionView:
    return DerivativeRevisionView(
        id=row.id,
        chapter_id=row.chapter_id,
        project_id=row.project_id,
        owner_id=row.owner_id,
        novel_id=row.novel_id,
        revision_number=row.revision_number,
        parent_revision_id=row.parent_revision_id,
        kind=DerivativeRevisionKind(row.kind),
        content=row.content,
        content_checksum=row.content_checksum,
        actor_id=row.actor_id,
        reason=row.reason,
        approval_state=DerivativeRevisionApproval(row.approval_state),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_revision_summary(row: DerivativeRevision) -> DerivativeRevisionSummary:
    return DerivativeRevisionSummary(
        id=row.id,
        chapter_id=row.chapter_id,
        project_id=row.project_id,
        revision_number=row.revision_number,
        parent_revision_id=row.parent_revision_id,
        kind=DerivativeRevisionKind(row.kind),
        content_checksum=row.content_checksum,
        actor_id=row.actor_id,
        reason=row.reason,
        approval_state=DerivativeRevisionApproval(row.approval_state),
        created_at=row.created_at,
    )


__all__ = ["to_chapter_view", "to_revision_summary", "to_revision_view"]
