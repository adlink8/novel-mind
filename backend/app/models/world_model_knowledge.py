"""Immutable durable character epistemic history (Phase 27-02).

Table ``world_model_knowledge`` stores append-only character state / goal /
motivation / knowledge claims (REQ-WM-02). Design conventions follow
``world_model_events`` (27-01):

- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key, so re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable projection; replay
  recomputes it and fails closed on drift (byte-equivalent restart proof).
- No active-pointer / promotion / current-revision column (D-02) and no UPDATE
  path anywhere. owner/novel FKs cascade; version_id follows the analysis
  version convention.
- Denormalized filter columns (subject, aspect, known_at, disclosure_cutoff,
  pov, pov_kind, source_kind, authority, epistemic_status, gate_status) make the
  cutoff/POV -> disclosure/authority query path index-friendly while
  ``canonical_payload`` remains the checksum-anchored source of truth.
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


class WorldModelKnowledge(TimestampMixin, Base):
    __tablename__ = "world_model_knowledge"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_world_model_knowledge_idempotency"
        ),
        Index(
            "idx_world_model_knowledge_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_world_model_knowledge_visibility",
            "owner_id",
            "novel_id",
            "subject",
            "known_at",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_knowledge_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_knowledge_idempotency_key",
        ),
        CheckConstraint(
            "aspect IN ('state','goal','motivation','knowledge')",
            name="ck_world_model_knowledge_aspect",
        ),
        CheckConstraint(
            "pov_kind IN ('character','omniscient')",
            name="ck_world_model_knowledge_pov_kind",
        ),
        CheckConstraint(
            "source_kind IN ('canon_source','reader_chat',"
            "'user_conversation','human_override')",
            name="ck_world_model_knowledge_source_kind",
        ),
        CheckConstraint(
            "authority IN ('canon_fact','probable_inference',"
            "'literary_interpretation','user_interpretation')",
            name="ck_world_model_knowledge_authority",
        ),
        CheckConstraint(
            "epistemic_status IN ('asserted','mistaken_belief',"
            "'hidden_knowledge','retracted','contradiction','candidate')",
            name="ck_world_model_knowledge_epistemic_status",
        ),
        CheckConstraint(
            "gate_status IN ('pending','passed','rejected')",
            name="ck_world_model_knowledge_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    knowledge_key: Mapped[str] = mapped_column(String(180), nullable=False)
    subject: Mapped[str] = mapped_column(String(180), nullable=False)
    aspect: Mapped[str] = mapped_column(String(24), nullable=False)
    known_at: Mapped[int] = mapped_column(Integer, nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    pov: Mapped[str] = mapped_column(String(180), nullable=False)
    pov_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_from: Mapped[str | None] = mapped_column(String(180), nullable=True)
    lineage: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
