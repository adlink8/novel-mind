"""Phase 07 durable chunk build + hierarchy tables (PostgreSQL truth)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
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


class ChunkBuild(TimestampMixin, Base):
    """Immutable candidate/full chunker build record."""

    __tablename__ = "chunk_builds"
    __table_args__ = (
        UniqueConstraint("build_id", name="uq_chunk_builds_build_id"),
        Index("idx_chunk_builds_novel", "novel_id"),
        Index("idx_chunk_builds_novel_status", "novel_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="built")
    parent_build_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_name: Mapped[str] = mapped_column(String(128), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunker_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    collection_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    changed_chapter_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    journal: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    vector_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ChunkActivePointer(TimestampMixin, Base):
    """Per-novel active hierarchical build pointer."""

    __tablename__ = "chunk_active_pointers"
    __table_args__ = (
        UniqueConstraint("novel_id", name="uq_chunk_active_pointers_novel"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ChunkHierarchyNode(TimestampMixin, Base):
    """Persisted chapter/scene/evidence node for an immutable build."""

    __tablename__ = "chunk_hierarchy_nodes"
    __table_args__ = (
        UniqueConstraint(
            "build_id", "node_id", name="uq_chunk_hierarchy_nodes_build_node"
        ),
        Index("idx_chunk_hierarchy_novel_build", "novel_id", "build_id"),
        Index("idx_chunk_hierarchy_chapter", "build_id", "chapter_id"),
        Index("idx_chunk_hierarchy_parent", "build_id", "parent_id"),
        Index("idx_chunk_hierarchy_level", "build_id", "level"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    build_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)  # chapter|scene|evidence
    chapter_id: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False, default="paragraph")
    decision_lineage: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Optional link to raw text_chunks row when projected
    text_chunk_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("text_chunks.id", ondelete="SET NULL"), nullable=True
    )
