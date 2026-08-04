"""Phase 38-01 forked Visual Bible schema and lineage (D-38-01/D-38-02).

The derivative Visual Bible is an **explicit, immutable fork** of an Original
Visual Bible snapshot (``visual_bible_versions``), living in its own sealed
namespace/version/owner/provenance (REQ-FORK-04 / REQ-CRE-06):

- ``derivative_visual_versions``: immutable derivative revision. It carries the
  exact Original snapshot reference (``source_version_id`` +
  ``source_snapshot_hash`` + ``source_manifest_hash``), the sealed derivative
  namespace (``visual_namespace = 'fanfiction_visual'``), the explicit
  ``divergence`` declaration and the owner/project/fork provenance. Only the
  ``review_state`` projection may change; every other column is frozen
  (a new fork/revision must be created instead).
- ``derivative_visual_entities`` / ``derivative_visual_assets``: append-only
  identity/style and reference-asset rows, each pinning the exact Original row
  it derives from (``source_entity_ref`` / ``source_asset_ref`` with hashes).
- ``derivative_visual_review_events``: append-only human/machine review actions
  with idempotent event keys.

Fail-closed boundaries:
- The source Original Visual Bible rows are referenced with ``RESTRICT`` and
  read-only: no derivative write path can mutate an Original row (REQ-FORK-04)
  and an Original snapshot cannot be deleted while a derivative references it.
- ``visual_namespace`` is sealed to ``'fanfiction_visual'`` at the database
  level; ``original_canon``/``user_interpretation`` namespaces can never be a
  derivative write target.
- Content rows (entities/assets/review events) reject UPDATE/DELETE; a version
  rejects any in-place mutation other than the review-state projection.
- No active pointer / promotion / current-revision column (mirrors D-30-01);
  approval is an append-only review action applied by ``lineage.py``.
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

# D-38-01: sealed derivative Visual Bible namespace.
DERIVATIVE_VISUAL_NAMESPACE = "fanfiction_visual"
DERIVATIVE_VISUAL_SCHEMA_VERSION = "derivative-visual.v1"
DERIVATIVE_VISUAL_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)
DERIVATIVE_VISUAL_ACTIONS = ("approve", "reject", "edit", "supersede", "needs_relink")
DERIVATIVE_VISUAL_ENTITY_TYPES = ("character", "place", "item", "faction", "style")
DERIVATIVE_VISUAL_RIGHTS_STATUSES = ("unreviewed", "cleared", "pending", "denied")
DERIVATIVE_VISUAL_ACTOR_SOURCES = ("human", "machine")
DERIVATIVE_VISUAL_AUTHORITY_LABELS = (
    "canon_fact",
    "probable_inference",
    "literary_interpretation",
    "user_interpretation",
)

# Phase 38-03: candidate asset review/consistency vocabularies (D-38-03).
# ``blocked`` = deterministic identity drift / undeclared divergence — the
# asset can never be published. ``needs_review`` = concern/unavailable signal;
# only an explicit human ``approved`` state ever becomes reader-visible.
DERIVATIVE_ASSET_STATES = (
    "candidate",
    "needs_review",
    "approved",
    "rejected",
    "superseded",
    "blocked",
)
DERIVATIVE_ASSET_ACTIONS = ("approve", "reject", "supersede")
DERIVATIVE_CONSISTENCY_VERDICTS = ("pass", "concern", "fail", "unavailable")
# D-38-01: candidate bytes share the sealed derivative Visual Bible namespace.
DERIVATIVE_ASSET_NAMESPACE = DERIVATIVE_VISUAL_NAMESPACE


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


# Immutable lineage on a derivative version: only ``review_state`` may move.
_FROZEN_VERSION_LINEAGE = frozenset(
    {
        "visual_namespace",
        "project_id",
        "fork_id",
        "version_key",
        "revision_number",
        "parent_version_id",
        "source_version_id",
        "source_snapshot_id",
        "source_snapshot_hash",
        "source_manifest_hash",
        "cutoff_chapter",
        "divergence",
        "provenance",
        "schema_version",
        "schema_hash",
        "policy_hash",
        "prompt_hash",
        "model_hash",
        "config_hash",
        "manifest_hash",
        "style_profile",
        "constraints",
        "canonical_payload",
        "canonical_payload_hash",
        "idempotency_key",
        "projection_hash",
    }
)


class DerivativeVisualVersion(TimestampMixin, Base):
    """Immutable derivative Visual Bible revision forked from an Original snapshot."""

    __tablename__ = "derivative_visual_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_derivative_visual_versions_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_key",
            name="uq_derivative_visual_versions_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_derivative_visual_versions_idempotency",
        ),
        Index(
            "idx_derivative_visual_versions_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        Index(
            "idx_derivative_visual_versions_fork",
            "owner_id",
            "novel_id",
            "fork_id",
            "visual_namespace",
        ),
        Index(
            "idx_derivative_visual_versions_source",
            "owner_id",
            "novel_id",
            "source_version_id",
        ),
        # REQ-FORK-04: the source is an Original Visual Bible snapshot inside
        # the same owner/novel scope and cannot be deleted while referenced.
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "source_version_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_derivative_visual_versions_source_scope",
        ),
        # D-38-01: only the sealed derivative namespace is a legal write target.
        CheckConstraint(
            "visual_namespace = 'fanfiction_visual'",
            name="ck_derivative_visual_versions_namespace",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_derivative_visual_versions_revision",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_derivative_visual_versions_cutoff",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_visual_versions_snapshot_hash",
        ),
        CheckConstraint(
            "length(source_manifest_hash) = 64",
            name="ck_derivative_visual_versions_source_manifest_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_derivative_visual_versions_schema_hash",
        ),
        CheckConstraint(
            "length(policy_hash) = 64",
            name="ck_derivative_visual_versions_policy_hash",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_derivative_visual_versions_manifest_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_derivative_visual_versions_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_derivative_visual_versions_idempotency_key",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(DERIVATIVE_VISUAL_STATES)})",
            name="ck_derivative_visual_versions_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("derivative_projects.id", ondelete="CASCADE"), nullable=False
    )
    fork_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("canon_forks.id", ondelete="CASCADE"), nullable=False
    )
    # D-38-01: sealed derivative namespace (never the Original Canon namespace).
    visual_namespace: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DERIVATIVE_VISUAL_NAMESPACE,
        server_default=DERIVATIVE_VISUAL_NAMESPACE,
    )
    version_key: Mapped[str] = mapped_column(String(160), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("derivative_visual_versions.id", ondelete="SET NULL")
    )
    # Immutable Original Visual Bible snapshot this derivative is forked from.
    source_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    # D-38-02: explicit divergence declaration; empty divergence is a gate error.
    divergence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # D-38-01: owner/novel/branch/project provenance, sealed at create.
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=DERIVATIVE_VISUAL_SCHEMA_VERSION,
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    model_hash: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    style_profile: Mapped[dict | None] = mapped_column(JSONB)
    constraints: Mapped[list | None] = mapped_column(JSONB)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class DerivativeVisualEntity(TimestampMixin, Base):
    """Append-only identity/style row pinned to one Original Visual Bible entity."""

    __tablename__ = "derivative_visual_entities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_derivative_visual_entities_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "stable_id",
            name="uq_derivative_visual_entities_stable_id",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "entity_key",
            name="uq_derivative_visual_entities_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_derivative_visual_entities_idempotency",
        ),
        Index(
            "idx_derivative_visual_entities_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "derivative_visual_versions.owner_id",
                "derivative_visual_versions.novel_id",
                "derivative_visual_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_derivative_visual_entities_version_scope",
        ),
        CheckConstraint(
            f"entity_type IN ({_sql_values(DERIVATIVE_VISUAL_ENTITY_TYPES)})",
            name="ck_derivative_visual_entities_entity_type",
        ),
        CheckConstraint(
            f"authority IN ({_sql_values(DERIVATIVE_VISUAL_AUTHORITY_LABELS)})",
            name="ck_derivative_visual_entities_authority",
        ),
        CheckConstraint(
            "disclosure_cutoff >= 1",
            name="ck_derivative_visual_entities_disclosure_cutoff",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_derivative_visual_entities_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_derivative_visual_entities_idempotency_key",
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
    divergence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Exact Original Visual Bible entity this row derives from (REQ-FORK-04).
    source_entity_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class DerivativeVisualAsset(TimestampMixin, Base):
    """Append-only derivative reference-asset metadata (never silently canon)."""

    __tablename__ = "derivative_visual_assets"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_derivative_visual_assets_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "asset_key",
            name="uq_derivative_visual_assets_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_derivative_visual_assets_idempotency",
        ),
        Index(
            "idx_derivative_visual_assets_scope",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "derivative_visual_versions.owner_id",
                "derivative_visual_versions.novel_id",
                "derivative_visual_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_derivative_visual_assets_version_scope",
        ),
        CheckConstraint(
            f"rights_status IN ({_sql_values(DERIVATIVE_VISUAL_RIGHTS_STATUSES)})",
            name="ck_derivative_visual_assets_rights_status",
        ),
        CheckConstraint(
            "length(bytes_hash) = 64",
            name="ck_derivative_visual_assets_bytes_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_derivative_visual_assets_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_derivative_visual_assets_idempotency_key",
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
    # Exact Original Visual Bible asset this derivative reuses/derives from.
    source_asset_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    approved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class DerivativeVisualReviewEvent(TimestampMixin, Base):
    """Append-only human/machine review action with idempotent event key."""

    __tablename__ = "derivative_visual_review_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_id",
            "event_key",
            name="uq_derivative_visual_review_events_key",
        ),
        Index(
            "idx_derivative_visual_review_events_version",
            "owner_id",
            "novel_id",
            "version_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "version_id"],
            [
                "derivative_visual_versions.owner_id",
                "derivative_visual_versions.novel_id",
                "derivative_visual_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_derivative_visual_review_events_version_scope",
        ),
        CheckConstraint(
            f"action IN ({_sql_values(DERIVATIVE_VISUAL_ACTIONS)})",
            name="ck_derivative_visual_review_events_action",
        ),
        CheckConstraint(
            f"actor_source IN ({_sql_values(DERIVATIVE_VISUAL_ACTOR_SOURCES)})",
            name="ck_derivative_visual_review_events_actor_source",
        ),
        CheckConstraint(
            f"from_review_state IN ({_sql_values(DERIVATIVE_VISUAL_STATES)})",
            name="ck_derivative_visual_review_events_from_state",
        ),
        CheckConstraint(
            f"to_review_state IN ({_sql_values(DERIVATIVE_VISUAL_STATES)})",
            name="ck_derivative_visual_review_events_to_state",
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


class DerivativeVisualCandidateAsset(TimestampMixin, Base):
    """Immutable derivative asset candidate (D-38-03, REQ-FORK-04/REQ-CRE-06).

    One provider output stored as write-only candidate bytes under the
    allowlisted derivative storage root. The row carries a generated
    ``asset_id``, the replayed content checksum, the frozen canonical
    ``scene_spec_hash`` the asset is bound to, the full identity/source/
    generator lineage and the deterministic cross-chapter consistency review
    signal. Only the ``review_state`` projection may move (candidate /
    needs_review / approved / rejected / superseded / blocked); every other
    column is frozen and content rows are append-only, so an Original Visual
    Bible asset can never be overwritten and a candidate can never silently
    become canon.
    """

    __tablename__ = "derivative_visual_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_derivative_visual_candidates_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "visual_version_id",
            "asset_key",
            name="uq_derivative_visual_candidates_key",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "visual_version_id",
            "asset_id",
            name="uq_derivative_visual_candidates_asset_id",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_derivative_visual_candidates_idempotency",
        ),
        Index(
            "idx_derivative_visual_candidates_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        Index(
            "idx_derivative_visual_candidates_identity",
            "owner_id",
            "novel_id",
            "visual_version_id",
            "identity_key",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "visual_version_id"],
            [
                "derivative_visual_versions.owner_id",
                "derivative_visual_versions.novel_id",
                "derivative_visual_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_derivative_visual_candidates_version_scope",
        ),
        # D-38-01: the candidate bytes always live in the sealed derivative
        # namespace; an Original Canon asset can never be a write target.
        CheckConstraint(
            "visual_namespace = 'fanfiction_visual'",
            name="ck_derivative_visual_candidates_namespace",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(DERIVATIVE_ASSET_STATES)})",
            name="ck_derivative_visual_candidates_review_state",
        ),
        CheckConstraint(
            f"consistency_verdict IN ({_sql_values(DERIVATIVE_CONSISTENCY_VERDICTS)})",
            name="ck_derivative_visual_candidates_verdict",
        ),
        CheckConstraint("size_bytes > 0", name="ck_derivative_visual_candidates_size"),
        CheckConstraint("chapter_number >= 1", name="ck_derivative_visual_candidates_chapter"),
        CheckConstraint("cutoff_chapter >= 1", name="ck_derivative_visual_candidates_cutoff"),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_derivative_visual_candidates_content_hash",
        ),
        CheckConstraint(
            "length(scene_spec_hash) = 64",
            name="ck_derivative_visual_candidates_scene_spec_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_derivative_visual_candidates_snapshot_hash",
        ),
        CheckConstraint(
            "length(source_manifest_hash) = 64",
            name="ck_derivative_visual_candidates_source_manifest_hash",
        ),
        CheckConstraint(
            "length(divergence_manifest_hash) = 64",
            name="ck_derivative_visual_candidates_divergence_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_derivative_visual_candidates_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_derivative_visual_candidates_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fork_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Approved derivative visual fork version this candidate is bound to.
    visual_version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    visual_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version_key: Mapped[str] = mapped_column(String(160), nullable=False)
    # D-38-03: idempotent asset_key + generated asset_id (never a client path).
    asset_key: Mapped[str] = mapped_column(String(180), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(320), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Sealed derivative namespace + frozen source snapshot lineage.
    visual_namespace: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DERIVATIVE_VISUAL_NAMESPACE,
        server_default=DERIVATIVE_VISUAL_NAMESPACE,
    )
    scene_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    # D-38-03: full identity/source/generator lineage (hash-pinned, no paths).
    identity_key: Mapped[str] = mapped_column(String(180), nullable=False)
    identity_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    generator_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    divergence_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Deterministic cross-chapter consistency review signal (D-38-03).
    consistency_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    consistency_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    consistency_verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class DerivativeVisualCandidateReviewEvent(TimestampMixin, Base):
    """Append-only human/machine review action on a candidate asset.

    Approval moves only the candidate ``review_state`` projection; the
    Original Visual Bible rows and the candidate bytes/lineage are never
    touched (REQ-FORK-04 / D-38-03). Repeated ``event_key`` replays without a
    second event.
    """

    __tablename__ = "derivative_visual_candidate_review_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "candidate_id",
            "event_key",
            name="uq_derivative_visual_candidate_review_key",
        ),
        Index(
            "idx_derivative_visual_candidate_review_candidate",
            "owner_id",
            "novel_id",
            "candidate_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "candidate_id"],
            [
                "derivative_visual_candidates.owner_id",
                "derivative_visual_candidates.novel_id",
                "derivative_visual_candidates.id",
            ],
            ondelete="CASCADE",
            name="fk_derivative_visual_candidate_review_scope",
        ),
        CheckConstraint(
            f"action IN ({_sql_values(DERIVATIVE_ASSET_ACTIONS)})",
            name="ck_derivative_visual_candidate_review_action",
        ),
        CheckConstraint(
            f"actor_source IN ({_sql_values(DERIVATIVE_VISUAL_ACTOR_SOURCES)})",
            name="ck_derivative_visual_candidate_review_actor_source",
        ),
        CheckConstraint(
            f"from_review_state IN ({_sql_values(DERIVATIVE_ASSET_STATES)})",
            name="ck_derivative_visual_candidate_review_from_state",
        ),
        CheckConstraint(
            f"to_review_state IN ({_sql_values(DERIVATIVE_ASSET_STATES)})",
            name="ck_derivative_visual_candidate_review_to_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    from_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


def _reject_version_lineage_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """A derivative version is immutable; only the review-state projection moves.

    D-38-01/D-38-02: the source snapshot ref, namespace, project/fork lineage,
    divergence, provenance and hashes are frozen — any attempt to repoint the
    fork or edit the divergence/source lineage fails closed; a new fork or a
    new derivative revision must be created instead.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    forbidden = changed - {"review_state"}
    if forbidden:
        raise ValueError(
            f"{type(target).__name__} derivative lineage is immutable; only the "
            "review_state projection may change — create a new fork/revision "
            f"instead of mutating: {sorted(forbidden)}"
        )


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Content rows are append-only; a version keeps only its review-state projection
# mutable so an explicit review action can be applied (approval is never an
# in-place promotion of a derivative candidate).
event.listen(DerivativeVisualVersion, "before_update", _reject_version_lineage_mutation)
event.listen(DerivativeVisualEntity, "before_update", _reject_content_mutation)
event.listen(DerivativeVisualEntity, "before_delete", _reject_content_mutation)
event.listen(DerivativeVisualAsset, "before_update", _reject_content_mutation)
event.listen(DerivativeVisualAsset, "before_delete", _reject_content_mutation)
event.listen(DerivativeVisualReviewEvent, "before_update", _reject_content_mutation)
event.listen(DerivativeVisualReviewEvent, "before_delete", _reject_content_mutation)


def _reject_candidate_lineage_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """A candidate asset is immutable; only the ``review_state`` projection moves.

    The storage key, content hash, scene spec hash, source snapshot lineage,
    identity/source/generator lineage, divergence manifest and the frozen
    consistency review signal can never be edited in place — a changed
    candidate must be stored as a new candidate (REQ-FORK-04 / D-38-03).
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    forbidden = changed - {"review_state"}
    if forbidden:
        raise ValueError(
            f"{type(target).__name__} candidate lineage is immutable; only the "
            "review_state projection may change — store a new candidate "
            f"instead of mutating: {sorted(forbidden)}"
        )


# Candidate rows and their review events are append-only; approval/rejection is
# an explicit review event that moves only the candidate review_state.
event.listen(
    DerivativeVisualCandidateAsset,
    "before_update",
    _reject_candidate_lineage_mutation,
)
event.listen(
    DerivativeVisualCandidateAsset,
    "before_delete",
    _reject_content_mutation,
)
event.listen(
    DerivativeVisualCandidateReviewEvent,
    "before_update",
    _reject_content_mutation,
)
event.listen(
    DerivativeVisualCandidateReviewEvent,
    "before_delete",
    _reject_content_mutation,
)
