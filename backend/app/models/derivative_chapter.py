"""Owner-scoped derivative chapter plan row (Phase 36-02, D-36-02/D-36-03).

A Derivative Chapter is the planning + Markdown editing unit of a derivative
project (REQ-FORK-02 / REQ-CRE-03). Every row is bound to its project through
the ``project_id`` FK (CASCADE) and carries the owner/novel scope denormalized
so a chapter can never be queried outside the owner/novel scope.

The row is an **ordered chapter plan**: ``position`` is unique per project and
forms the stable reading order. ``markdown`` holds the canonical Markdown
draft; ``markdown_checksum`` is the SHA-256 of the deterministic-canonicalized
content (re-playable, D-36-02) and ``revision`` is the optimistic-concurrency
token the client echoes back as ``base_revision`` so a stale write fails closed
with the current revision/checksum instead of overwriting newer content.

Per D-36-03 every write only forms a **Fanfiction Canon draft**: the chapter
never carries a write target of its own — its project is database-sealed to
``fanfiction_canon`` (Phase 36-01) and no Original / Interpretation write
endpoint exists in this surface. A chapter's status is ``draft`` or
``archived`` only; there is no client-controlled ``published`` state (Phase 39
owns publication via the immutable revision service).
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# D-36-02: chapter plan lifecycle; publication is never a chapter status here.
DERIVATIVE_CHAPTER_STATUSES = ("draft", "archived")
DERIVATIVE_CHAPTER_MAX_TITLE_LENGTH = 200


class DerivativeChapter(TimestampMixin, Base):
    """One ordered plan/draft row inside an owner-scoped derivative project."""

    __tablename__ = "derivative_chapters"
    __table_args__ = (
        # Stable order: one chapter per project/position (reordering rewrites
        # the whole set in one transaction).
        UniqueConstraint(
            "project_id",
            "position",
            name="uq_derivative_chapters_position",
        ),
        CheckConstraint(
            "status IN ('draft','archived')",
            name="ck_derivative_chapters_status",
        ),
        CheckConstraint(
            "length(markdown_checksum) = 64",
            name="ck_derivative_chapters_checksum",
        ),
        CheckConstraint(
            "position >= 0",
            name="ck_derivative_chapters_position_nonneg",
        ),
        CheckConstraint(
            "revision > 0",
            name="ck_derivative_chapters_revision",
        ),
        Index("ix_derivative_chapters_scope", "owner_id", "novel_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # D-36-02: a chapter cannot exist outside its derivative project.
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derivative_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # Canonical Markdown is the editing representation; checksum seals it.
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    markdown_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    # Optimistic concurrency token (client echoes it as base_revision).
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


__all__ = [
    "DERIVATIVE_CHAPTER_MAX_TITLE_LENGTH",
    "DERIVATIVE_CHAPTER_STATUSES",
    "DerivativeChapter",
]
