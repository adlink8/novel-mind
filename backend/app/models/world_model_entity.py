"""Immutable durable world entity/rule/faction/place/item projection (27-03).

Tables (REQ-WM-03):
- ``world_model_entities``: append-only typed entity/faction/place/item rows
  carrying aliases and version lineage.
- ``world_model_rules``: append-only world rule rows.
- ``world_model_rule_exceptions``: first-class rule exception records — never
  folded into the rule statement, never dropped by normalization.
- ``world_model_entity_links``: append-only membership/ownership/spatial/
  item-state links between entities.
- ``world_model_alias_reviews``: review-only alias collision candidates that
  never silently merge entities.

Design conventions (following ``world_model_events`` / ``world_model_knowledge``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key, so re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable projection; replay
  recomputes it and fails closed on drift (byte-equivalent restart proof).
- No active-pointer / promotion / current-revision column (D-02) and no UPDATE
  path anywhere. owner/novel FKs cascade; version_id follows the analysis
  version convention.
- Denormalized filter columns (entity_type / link_kind / disclosure_cutoff /
  authority / gate_status / source_kind) keep the cutoff query path
  index-friendly while ``canonical_payload`` is the checksum-anchored truth.
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

ENTITY_TYPE_VALUES = "'entity','faction','place','item'"
LINK_KIND_VALUES = (
    "'member_of','allegiance','controls','owns','located_in','carried_by'"
)
SOURCE_KIND_VALUES = "'canon_source','reader_chat','user_conversation','human_override'"
AUTHORITY_VALUES = (
    "'canon_fact','probable_inference','literary_interpretation','user_interpretation'"
)
GATE_STATUS_VALUES = "'pending','passed','rejected'"
ALIAS_REVIEW_STATUS_VALUES = "'review','resolved','rejected'"


class WorldModelEntity(TimestampMixin, Base):
    __tablename__ = "world_model_entities"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_world_model_entities_idempotency"),
        Index(
            "idx_world_model_entities_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_world_model_entities_type",
            "owner_id",
            "novel_id",
            "entity_type",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_entities_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_entities_idempotency_key",
        ),
        CheckConstraint(
            f"entity_type IN ({ENTITY_TYPE_VALUES})",
            name="ck_world_model_entities_entity_type",
        ),
        CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_entities_source_kind",
        ),
        CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_entities_authority",
        ),
        CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_entities_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_key: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    lineage: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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


class WorldModelRule(TimestampMixin, Base):
    __tablename__ = "world_model_rules"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_world_model_rules_idempotency"),
        Index(
            "idx_world_model_rules_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_rules_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_rules_idempotency_key",
        ),
        CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_rules_source_kind",
        ),
        CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_rules_authority",
        ),
        CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_rules_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_key: Mapped[str] = mapped_column(String(180), nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    lineage: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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


class WorldModelRuleException(TimestampMixin, Base):
    """First-class rule exception record (D-04): bound to a rule, never dropped."""

    __tablename__ = "world_model_rule_exceptions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_world_model_exceptions_idempotency"
        ),
        Index(
            "idx_world_model_exceptions_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_world_model_exceptions_rule",
            "owner_id",
            "novel_id",
            "rule_key",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_exceptions_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_exceptions_idempotency_key",
        ),
        CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_exceptions_source_kind",
        ),
        CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_exceptions_authority",
        ),
        CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_exceptions_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exception_key: Mapped[str] = mapped_column(String(180), nullable=False)
    rule_key: Mapped[str] = mapped_column(String(180), nullable=False)
    applies_to: Mapped[str | None] = mapped_column(String(180), nullable=True)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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


class WorldModelEntityLink(TimestampMixin, Base):
    """Membership / ownership / spatial / item-state link between entities."""

    __tablename__ = "world_model_entity_links"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_world_model_links_idempotency"),
        Index(
            "idx_world_model_links_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_world_model_links_kind",
            "owner_id",
            "novel_id",
            "link_kind",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_links_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_links_idempotency_key",
        ),
        CheckConstraint(
            f"link_kind IN ({LINK_KIND_VALUES})",
            name="ck_world_model_links_link_kind",
        ),
        CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_links_source_kind",
        ),
        CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_links_authority",
        ),
        CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_links_gate_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_key: Mapped[str] = mapped_column(String(180), nullable=False)
    link_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_key: Mapped[str] = mapped_column(String(180), nullable=False)
    target_key: Mapped[str] = mapped_column(String(180), nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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


class WorldModelAliasReview(TimestampMixin, Base):
    """Review-only alias collision candidate; never silently merges entities."""

    __tablename__ = "world_model_alias_reviews"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_world_model_alias_reviews_idempotency"
        ),
        Index(
            "idx_world_model_alias_reviews_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_alias_reviews_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_alias_reviews_idempotency_key",
        ),
        CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_alias_reviews_source_kind",
        ),
        CheckConstraint(
            "review_status IN ('review','resolved','rejected')",
            name="ck_world_model_alias_reviews_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_key: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_key_a: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_key_b: Mapped[str] = mapped_column(String(180), nullable=False)
    matched_alias: Mapped[str] = mapped_column(String(180), nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
