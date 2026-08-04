"""Immutable owner-scoped derivative chapter revision row (Phase 36-03, D-36-02).

A ``DerivativeRevision`` is one append-only snapshot of a chapter's canonical
Markdown draft (REQ-FORK-02 / REQ-CRE-04). Every write — the initial chapter
row, an autosave, a plan patch that changes Markdown, or a rollback — appends a
**new** row sealed with the SHA-256 ``content_checksum`` and a
``parent_revision_id`` pointer to its predecessor; existing rows are never
mutated or deleted (a ``before_update`` event listener fails closed), so the
full history, deterministic diff targets and rollback destinations are always
recoverable and auditable (T-36-03-01).

The row is bound to its chapter through ``chapter_id`` (NOT NULL CASCADE) and
carries the owner/novel/project scope denormalized exactly like the chapter row
so a revision can never be queried outside the owner scope. ``revision_number``
is the chapter's optimistic-concurrency token value at the time of the write
and is unique per chapter, so history ordering is deterministic and a client's
``base_revision`` maps 1:1 to a row.

``kind`` records why the row exists (``create`` for the initial chapter row,
``autosave`` for draft writes, ``rollback`` for a rollback restored as a new
child). ``actor_id`` / ``reason`` / ``approval_state`` form the auditable
rollback journal (T-36-03-02 repudiation gate): a rollback row is always
``approved`` (an explicit owner action) and carries the owner's stated reason;
autosave/create rows are ``not_required``.

Per D-36-03 every revision is a **Fanfiction Canon draft**: the row inherits the
project's sealed ``fanfiction_canon`` space and no Original / Interpretation
write surface exists anywhere in this module.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# D-36-02: why a revision row exists; release is never a row kind here.
DERIVATIVE_REVISION_KINDS = ("create", "autosave", "rollback")
# Rollback journal: explicit owner actions are approved; draft writes are not.
DERIVATIVE_REVISION_APPROVAL_STATES = ("not_required", "approved")


class DerivativeRevision(TimestampMixin, Base):
    """One immutable snapshot of a chapter's canonical Markdown draft."""

    __tablename__ = "derivative_revisions"
    __table_args__ = (
        # One snapshot per chapter revision token: the client's base_revision
        # resolves deterministically to exactly one row.
        UniqueConstraint(
            "chapter_id",
            "revision_number",
            name="uq_derivative_revisions_chapter_number",
        ),
        CheckConstraint(
            "revision_number > 0",
            name="ck_derivative_revisions_number",
        ),
        CheckConstraint(
            "length(content_checksum) = 64",
            name="ck_derivative_revisions_checksum",
        ),
        CheckConstraint(
            "kind IN ('create','autosave','rollback')",
            name="ck_derivative_revisions_kind",
        ),
        CheckConstraint(
            "approval_state IN ('not_required','approved')",
            name="ck_derivative_revisions_approval",
        ),
        Index(
            "ix_derivative_revisions_scope",
            "owner_id",
            "novel_id",
            "project_id",
            "chapter_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # D-36-02: a revision cannot exist outside its chapter (CASCADE on delete).
    chapter_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The chapter's optimistic-concurrency token value at the time of the write.
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Lineage pointer; NULL only for the chapter's root (create) revision.
    parent_revision_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("derivative_revisions.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Canonical Markdown snapshot; the checksum seals it (replayable, D-36-02).
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    # ---- auditable rollback journal (T-36-03-02) ----
    actor_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(Text)
    approval_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="not_required"
    )


def _reject_revision_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """Revision rows are immutable: any in-place mutation fails closed.

    History is append-only (T-36-03-01). A rollback must create a **new** child
    revision; rewriting ``content`` / ``content_checksum`` / ``parent`` /
    ``revision_number`` of an existing row is a hard error, so no process can
    silently rewrite the audit trail.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.history.has_changes()
    }
    if changed:
        raise ValueError(
            f"{type(target).__name__} rows are immutable; append a new revision "
            f"instead of mutating: {sorted(changed)}"
        )


event.listen(DerivativeRevision, "before_update", _reject_revision_mutation)


__all__ = [
    "DERIVATIVE_REVISION_APPROVAL_STATES",
    "DERIVATIVE_REVISION_KINDS",
    "DerivativeRevision",
]
