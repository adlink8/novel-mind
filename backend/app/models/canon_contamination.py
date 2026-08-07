"""Durable contamination block audit for the three knowledge spaces (Phase 35-04).

REQ-CRE-02 / D-35-02: every failed derivative-write attempt into an Original
pipeline is recorded here *after* the contaminated transaction has rolled back,
so the blocked reason survives even though the write never landed.  The table
carries the real PostgreSQL composite unique / FK / check constraints that back
the shared derivative-write guard adapter (``canon_fork/contamination.py``):

- composite unique ``(owner_id, novel_id, space, pipeline, attempt_hash)`` —
  an identical repeated blocked attempt is idempotent, never double-recorded;
- FK to ``users`` / ``novels`` (the attempt must resolve to a real owner/novel);
- check constraints pin ``space`` to the derivative vocabulary, ``pipeline`` to
  the Original consumer pipelines, and require a non-empty ``blocked_reason``.
"""

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

# Must mirror the vocabulary in canon_fork/contracts.ORIGINAL_PIPELINES and the
# derivative spaces of canon_fork/contamination.py.
CANON_CONTAMINATION_PIPELINES = (
    "original_analysis",
    "original_retrieval",
    "facet",
    "evaluation",
    "candidate_builder",
)
CANON_DERIVATIVE_SPACES = ("user_interpretation", "fanfiction_canon")


class CanonContaminationBlock(TimestampMixin, Base):
    """One durable record of a blocked derivative write into an Original chain."""

    __tablename__ = "canon_contamination_blocks"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "space",
            "pipeline",
            "attempt_hash",
            name="uq_canon_contamination_block_attempt",
        ),
        CheckConstraint(
            "space IN ('user_interpretation','fanfiction_canon')",
            name="ck_canon_contamination_block_space",
        ),
        CheckConstraint(
            "pipeline IN ('original_analysis','original_retrieval','facet',"
            "'evaluation','candidate_builder')",
            name="ck_canon_contamination_block_pipeline",
        ),
        CheckConstraint(
            "length(blocked_reason) > 0", name="ck_canon_contamination_block_reason"
        ),
        Index(
            "ix_canon_contamination_blocks_scope",
            "owner_id",
            "novel_id",
            "pipeline",
        ),
        Index("ix_canon_contamination_blocks_attempt", "attempt_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The derivative space that attempted to enter an Original chain.
    space: Mapped[str] = mapped_column(String(32), nullable=False)
    # The Original consumer pipeline that was attacked (index/eval/facet/...).
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    blocked_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Idempotency key: same (owner, novel, space, pipeline, attempt) = one record.
    attempt_hash: Mapped[str] = mapped_column(String(64), nullable=False)


__all__ = [
    "CANON_CONTAMINATION_PIPELINES",
    "CANON_DERIVATIVE_SPACES",
    "CanonContaminationBlock",
]
