"""Visual Bible candidate Artifact contract (Phase 30-01, REQ-VIS-01).

Tables:
- ``visual_bible_versions``: immutable, versioned candidate revision for one
  owning novel. Records source snapshot, schema/prompt/model/config/policy
  hashes, parent revision, spoiler cutoff and a candidate-only review state.
- ``visual_bible_entities``: typed character/place/item/faction/style visual
  description rows with reusable stable IDs scoped by owner+novel+version.
- ``visual_bible_claims``: typed visual claims carrying the four-label
  authority tag (canon_fact / probable_inference / literary_interpretation /
  user_interpretation) and the spoiler cutoff.
- ``visual_bible_evidence_refs``: source-linked evidence with source snapshot,
  chapter/range, offsets, content hash and a DB-level ``chapter_number <=
  cutoff_chapter`` spoiler gate.
- ``visual_bible_reference_assets``: immutable binary reference metadata
  (asset id, MIME, bytes hash, rights/provenance). A generated/reference
  asset never becomes canon without an explicit approval.
- ``visual_bible_review_events``: append-only human/machine review actions
  (approve/reject/edit/supersede/needs_relink) with idempotent event keys.

Design conventions (following ``world_model_entity.py`` / ``knowledge_unit.py``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload
  hash and a unique idempotency key; re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable version;
  replay recomputes it and fails closed on drift.
- No active-pointer / promotion / current-revision column (D-30-01) and no
  mutation path for content rows; review state is an append-only projection.
- Content rows (claim/evidence/asset/review event) are append-only — SQLAlchemy
  events reject UPDATE/DELETE so no silent in-place canon promotion is possible.
- ``Novel.cover_url`` and ``backend/storage/images/`` remain unrelated cover /
  upload artifacts; the Visual Bible contract never reuses them (D-30-01/02).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

VISUAL_SCHEMA_VERSION = "visual-bible.v1"
VISUAL_ENTITY_TYPES = ("character", "place", "item", "faction", "style")
VISUAL_AUTHORITY_LABELS = (
    "canon_fact",
    "probable_inference",
    "literary_interpretation",
    "user_interpretation",
)
VISUAL_REVIEW_ACTIONS = ("approve", "reject", "edit", "supersede", "needs_relink")
VISUAL_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)
VISUAL_RIGHTS_STATUSES = ("unreviewed", "cleared", "pending", "denied")
VISUAL_ACTOR_SOURCES = ("human", "machine")


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class VisualBibleVersion(TimestampMixin, Base):
    """Immutable versioned candidate revision for one owning novel (D-30-01/03)."""

    __tablename__ = "visual_bible_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_visual_bible_versions_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_key",
            name="uq_visual_bible_versions_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_visual_bible_versions_idempotency",
        ),
        Index(
            "idx_visual_bible_versions_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_visual_bible_versions_revision",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_visual_bible_versions_cutoff",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_visual_bible_versions_manifest_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_visual_bible_versions_schema_hash",
        ),
        CheckConstraint(
            "length(policy_hash) = 64",
            name="ck_visual_bible_versions_policy_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_visual_bible_versions_snapshot_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_visual_bible_versions_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_visual_bible_versions_idempotency_key",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(VISUAL_REVIEW_STATES)})",
            name="ck_visual_bible_versions_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("visual_bible_versions.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=VISUAL_SCHEMA_VERSION
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    model_hash: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style_profile: Mapped[dict | None] = mapped_column(JSONB)
    constraints: Mapped[list | None] = mapped_column(JSONB)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class VisualEntity(TimestampMixin, Base):
    """Typed visual description row with reusable stable ID (D-30-03)."""

    __tablename__ = "visual_bible_entities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_visual_bible_entities_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "stable_id",
            name="uq_visual_bible_entities_stable_id",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "entity_key",
            name="uq_visual_bible_entities_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_visual_bible_entities_idempotency",
        ),
        Index(
            "idx_visual_bible_entities_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_visual_bible_entities_type",
            "owner_id",
            "novel_id",
            "entity_type",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_entities_version_scope",
        ),
        CheckConstraint(
            f"entity_type IN ({_sql_values(VISUAL_ENTITY_TYPES)})",
            name="ck_visual_bible_entities_entity_type",
        ),
        CheckConstraint(
            f"authority IN ({_sql_values(VISUAL_AUTHORITY_LABELS)})",
            name="ck_visual_bible_entities_authority",
        ),
        CheckConstraint(
            "disclosure_cutoff >= 1",
            name="ck_visual_bible_entities_disclosure_cutoff",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_visual_bible_entities_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_visual_bible_entities_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_key: Mapped[str] = mapped_column(String(180), nullable=False)
    stable_id: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    disclosure_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class VisualClaim(TimestampMixin, Base):
    """Typed visual claim with a four-label authority tag (D-30-02)."""

    __tablename__ = "visual_bible_claims"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_visual_bible_claims_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "claim_key",
            name="uq_visual_bible_claims_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_visual_bible_claims_idempotency",
        ),
        Index(
            "idx_visual_bible_claims_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_visual_bible_claims_entity",
            "owner_id",
            "novel_id",
            "entity_stable_id",
        ),
        Index(
            "idx_visual_bible_claims_authority",
            "owner_id",
            "novel_id",
            "authority",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_claims_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "entity_id"],
            [
                "visual_bible_entities.owner_id",
                "visual_bible_entities.novel_id",
                "visual_bible_entities.id",
            ],
            ondelete="RESTRICT",
            name="fk_visual_bible_claims_entity_scope",
        ),
        CheckConstraint(
            f"authority IN ({_sql_values(VISUAL_AUTHORITY_LABELS)})",
            name="ck_visual_bible_claims_authority",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_visual_bible_claims_cutoff",
        ),
        CheckConstraint(
            "length(claim_hash) = 64",
            name="ck_visual_bible_claims_claim_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_visual_bible_claims_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_visual_bible_claims_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_key: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_stable_id: Mapped[str] = mapped_column(String(180), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str | None] = mapped_column(Text)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class VisualEvidenceRef(TimestampMixin, Base):
    """Source-linked evidence for a visual claim (D-30-02 spoiler gate)."""

    __tablename__ = "visual_bible_evidence_refs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_visual_bible_evidence_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "claim_id",
            "evidence_key",
            name="uq_visual_bible_evidence_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_visual_bible_evidence_idempotency",
        ),
        Index(
            "idx_visual_bible_evidence_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        Index(
            "idx_visual_bible_evidence_claim",
            "claim_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_evidence_version_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "claim_id"],
            [
                "visual_bible_claims.owner_id",
                "visual_bible_claims.novel_id",
                "visual_bible_claims.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_evidence_claim_scope",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_visual_bible_evidence_offsets",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_visual_bible_evidence_chapter",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_visual_bible_evidence_cutoff",
        ),
        CheckConstraint(
            "chapter_number <= cutoff_chapter",
            name="ck_visual_bible_evidence_spoiler_cutoff",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_visual_bible_evidence_snapshot_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_visual_bible_evidence_content_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_visual_bible_evidence_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(180), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class VisualReferenceAsset(TimestampMixin, Base):
    """Immutable binary reference metadata; never silently canon (D-30-01/03)."""

    __tablename__ = "visual_bible_reference_assets"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_visual_bible_assets_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "asset_key",
            name="uq_visual_bible_assets_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_visual_bible_assets_idempotency",
        ),
        Index(
            "idx_visual_bible_assets_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_assets_version_scope",
        ),
        CheckConstraint(
            f"rights_status IN ({_sql_values(VISUAL_RIGHTS_STATUSES)})",
            name="ck_visual_bible_assets_rights_status",
        ),
        CheckConstraint(
            "length(bytes_hash) = 64",
            name="ck_visual_bible_assets_bytes_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_visual_bible_assets_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_visual_bible_assets_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_key: Mapped[str] = mapped_column(String(180), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rights_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unreviewed", server_default="unreviewed"
    )
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class VisualBibleReviewEvent(TimestampMixin, Base):
    """Append-only human/machine review action with idempotent event key (D-30-04)."""

    __tablename__ = "visual_bible_review_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "event_key",
            name="uq_visual_bible_review_events_key",
        ),
        Index(
            "idx_visual_bible_review_events_version",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_visual_bible_review_events_version_scope",
        ),
        CheckConstraint(
            f"action IN ({_sql_values(VISUAL_REVIEW_ACTIONS)})",
            name="ck_visual_bible_review_events_action",
        ),
        CheckConstraint(
            f"actor_source IN ({_sql_values(VISUAL_ACTOR_SOURCES)})",
            name="ck_visual_bible_review_events_actor_source",
        ),
        CheckConstraint(
            f"from_review_state IN ({_sql_values(VISUAL_REVIEW_STATES)})",
            name="ck_visual_bible_review_events_from_state",
        ),
        CheckConstraint(
            f"to_review_state IN ({_sql_values(VISUAL_REVIEW_STATES)})",
            name="ck_visual_bible_review_events_to_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    from_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Content rows are append-only: no in-place edit of claims/evidence/assets or
# replay of a review event. Review state is projected separately on the version.
event.listen(VisualClaim, "before_update", _reject_content_mutation)
event.listen(VisualClaim, "before_delete", _reject_content_mutation)
event.listen(VisualEvidenceRef, "before_update", _reject_content_mutation)
event.listen(VisualEvidenceRef, "before_delete", _reject_content_mutation)
event.listen(VisualReferenceAsset, "before_update", _reject_content_mutation)
event.listen(VisualReferenceAsset, "before_delete", _reject_content_mutation)
event.listen(VisualBibleReviewEvent, "before_update", _reject_content_mutation)
event.listen(VisualBibleReviewEvent, "before_delete", _reject_content_mutation)
