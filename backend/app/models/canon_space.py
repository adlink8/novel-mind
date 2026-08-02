"""Persistent three-space knowledge contract for v1.3 authoring boundaries."""

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

CANON_SPACES = ("original_canon", "user_interpretation", "fanfiction_canon")
CANON_AUTHORITIES = ("source_text", "user_assertion", "creative_draft")
CANON_CITATION_POLICIES = (
    "original_leaf",
    "interpretation_with_original_refs",
    "fanfiction_only",
)
CANON_ARTIFACT_STATUSES = ("draft", "accepted", "rejected", "archived")


class CanonSpaceArtifact(TimestampMixin, Base):
    """Owner/novel-scoped, versioned artifact in one knowledge space.

    This table is intentionally not consumed by raw chunk, unit, facet, or NM
    retrieval. Consumers must pass through ``canon_space_policy`` first.
    """

    __tablename__ = "canon_space_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "space",
            "namespace",
            "version_key",
            name="uq_canon_space_artifact_version",
        ),
        CheckConstraint(
            "space IN ('original_canon','user_interpretation','fanfiction_canon')",
            name="ck_canon_space_artifact_space",
        ),
        CheckConstraint(
            "authority IN ('source_text','user_assertion','creative_draft')",
            name="ck_canon_space_artifact_authority",
        ),
        CheckConstraint(
            "citation_policy IN ('original_leaf','interpretation_with_original_refs','fanfiction_only')",
            name="ck_canon_space_artifact_citation",
        ),
        CheckConstraint(
            "status IN ('draft','accepted','rejected','archived')",
            name="ck_canon_space_artifact_status",
        ),
        Index("ix_canon_space_artifacts_scope", "owner_id", "novel_id", "space"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    space: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    version_key: Mapped[str] = mapped_column(String(128), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    citation_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True
    )
    source_text_chunk_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSON, default=dict)
