"""Immutable durable world-model event/causal-edge projection (Phase 27-01).

Tables:
- ``world_model_events``: append-only event fact candidates.
- ``world_model_causal_edges``: append-only evidence-gated causal edge candidates.
- ``world_model_conflicts``: preserved conflicts (temporal / assertion) that are
  never resolved by overwrite.

Design conventions (following ``query_plan_traces``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key, so re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable projection; replay
  recomputes it and fails closed on drift (byte-equivalent restart proof).
- No active-pointer / promotion / current-revision column (D-02) and no UPDATE
  path anywhere (D-14). owner/novel FKs cascade; version_id is an ordinary
  INTEGER following the analysis_versions version convention.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin


class WorldModelEvent(TimestampMixin, Base):
    __tablename__ = "world_model_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_world_model_events_idempotency"),
        Index(
            "idx_world_model_events_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_events_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_events_idempotency_key",
        ),
        CheckConstraint(
            "authority IN ('canon_fact','probable_inference',"
            "'literary_interpretation','user_interpretation')",
            name="ck_world_model_events_authority",
        ),
        CheckConstraint(
            "gate_status IN ('pending','passed','rejected')",
            name="ck_world_model_events_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    effective_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class WorldModelCausalEdge(TimestampMixin, Base):
    __tablename__ = "world_model_causal_edges"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_world_model_edges_idempotency"),
        Index(
            "idx_world_model_edges_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_edges_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_edges_idempotency_key",
        ),
        CheckConstraint(
            "edge_type IN ('caused','triggered','responded','blocked')",
            name="ck_world_model_edges_edge_type",
        ),
        CheckConstraint(
            "authority IN ('canon_fact','probable_inference',"
            "'literary_interpretation','user_interpretation')",
            name="ck_world_model_edges_authority",
        ),
        CheckConstraint(
            "gate_status IN ('pending','passed','rejected')",
            name="ck_world_model_edges_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edge_key: Mapped[str] = mapped_column(String(180), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    target_event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(24), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class WorldModelConflict(TimestampMixin, Base):
    __tablename__ = "world_model_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_world_model_conflicts_idempotency"
        ),
        Index(
            "idx_world_model_conflicts_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_conflicts_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_conflicts_idempotency_key",
        ),
        CheckConstraint(
            "kind IN ('temporal_conflict','assertion_conflict')",
            name="ck_world_model_conflicts_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conflict_key: Mapped[str] = mapped_column(String(180), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    involved_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str] = mapped_column(String(400), nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
