"""Visual Bible candidate Artifact contract tables (Phase 30-01).

REQ-VIS-01 / D-30-01..D-30-04: the Visual Bible is a candidate-only,
evidence-linked, versioned Artifact. New append-only tables:
  - ``visual_bible_versions``: immutable versioned candidate revision with
    source snapshot, schema/prompt/model/config/policy hashes, parent
    revision, spoiler cutoff and candidate-only review state.
  - ``visual_bible_entities``: typed character/place/item/faction/style
    visual description rows with reusable stable IDs.
  - ``visual_bible_claims``: typed visual claims with the four-label
    authority tag.
  - ``visual_bible_evidence_refs``: source-linked evidence with source
    snapshot, chapter/range, offsets, content hash and a DB-level
    ``chapter_number <= cutoff_chapter`` spoiler gate.
  - ``visual_bible_reference_assets``: immutable binary reference metadata
    with rights/provenance; approval is required before canon exposure.
  - ``visual_bible_review_events``: append-only review actions
    (approve/reject/edit/supersede/needs_relink) with idempotent event keys.

Design conventions (matching 20260801_2703/2801): idempotent inspector
guards, symmetric downgrade, JSONB via ``JSONB().with_variant(JSON(),
"sqlite")``, composite owner/novel/version scope FKs and unique reusable ID
constraints. No existing cover/upload rows are touched.

Revision ID: 20260801_visual_bible
Revises: 20260801_2801
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_visual_bible"
down_revision = "20260801_2801"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_ENTITY_TYPES = "'character','place','item','faction','style'"
_AUTHORITY_LABELS = (
    "'canon_fact','probable_inference',"
    "'literary_interpretation','user_interpretation'"
)
_REVIEW_ACTIONS = "'approve','reject','edit','supersede','needs_relink'"
_REVIEW_STATES = (
    "'candidate','approved','rejected','superseded','needs_relink'"
)
_RIGHTS_STATUSES = "'unreviewed','cleared','pending','denied'"
_ACTOR_SOURCES = "'human','machine'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""

    # ---- visual_bible_versions --------------------------------
    if not _has_table("visual_bible_versions"):
        op.create_table(
            "visual_bible_versions",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "owner_id",
                sa.Integer,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "novel_id",
                sa.Integer,
                sa.ForeignKey("novels.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_key", sa.String(120), nullable=False),
            sa.Column("revision_number", sa.Integer, nullable=False),
            sa.Column(
                "parent_version_id",
                sa.Integer,
                sa.ForeignKey("visual_bible_versions.id", ondelete="SET NULL"),
            ),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column("cutoff_chapter", sa.Integer, nullable=False),
            sa.Column(
                "review_state",
                sa.String(16),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column("schema_version", sa.String(64), nullable=False),
            sa.Column("schema_hash", sa.String(64), nullable=False),
            sa.Column("policy_hash", sa.String(64), nullable=False),
            sa.Column("prompt_hash", sa.String(64), nullable=True),
            sa.Column("model_hash", sa.String(64), nullable=True),
            sa.Column("config_hash", sa.String(64), nullable=True),
            sa.Column("manifest_hash", sa.String(64), nullable=False),
            sa.Column("style_profile", JSONB, nullable=True),
            sa.Column("constraints", JSONB, nullable=True),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("projection_hash", sa.String(64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "id",
                name="uq_visual_bible_versions_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_key",
                name="uq_visual_bible_versions_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_visual_bible_versions_idempotency",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_visual_bible_versions_revision",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_visual_bible_versions_cutoff",
            ),
            sa.CheckConstraint(
                "length(manifest_hash) = 64",
                name="ck_visual_bible_versions_manifest_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_visual_bible_versions_schema_hash",
            ),
            sa.CheckConstraint(
                "length(policy_hash) = 64",
                name="ck_visual_bible_versions_policy_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_visual_bible_versions_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_visual_bible_versions_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_visual_bible_versions_idempotency_key",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_REVIEW_STATES})",
                name="ck_visual_bible_versions_review_state",
            ),
        )
        op.create_index(
            "idx_visual_bible_versions_scope",
            "visual_bible_versions",
            ["owner_id", "novel_id", "review_state"],
        )

    # ---- visual_bible_entities --------------------------------
    if not _has_table("visual_bible_entities"):
        op.create_table(
            "visual_bible_entities",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("version_id", sa.Integer, nullable=False),
            sa.Column("entity_key", sa.String(180), nullable=False),
            sa.Column("stable_id", sa.String(180), nullable=False),
            sa.Column("entity_type", sa.String(24), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("projection_hash", sa.String(64), nullable=False),
            sa.Column("schema_version", sa.String(64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "id",
                name="uq_visual_bible_entities_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "stable_id",
                name="uq_visual_bible_entities_stable_id",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "entity_key",
                name="uq_visual_bible_entities_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_visual_bible_entities_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_entities_version_scope",
            ),
            sa.CheckConstraint(
                f"entity_type IN ({_ENTITY_TYPES})",
                name="ck_visual_bible_entities_entity_type",
            ),
            sa.CheckConstraint(
                f"authority IN ({_AUTHORITY_LABELS})",
                name="ck_visual_bible_entities_authority",
            ),
            sa.CheckConstraint(
                "disclosure_cutoff >= 1",
                name="ck_visual_bible_entities_disclosure_cutoff",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_visual_bible_entities_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_visual_bible_entities_idempotency_key",
            ),
        )
        op.create_index(
            "idx_visual_bible_entities_scope",
            "visual_bible_entities",
            ["owner_id", "novel_id", "version_id"],
        )
        op.create_index(
            "idx_visual_bible_entities_type",
            "visual_bible_entities",
            ["owner_id", "novel_id", "entity_type"],
        )

    # ---- visual_bible_claims --------------------------------
    if not _has_table("visual_bible_claims"):
        op.create_table(
            "visual_bible_claims",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("version_id", sa.Integer, nullable=False),
            sa.Column("claim_key", sa.String(180), nullable=False),
            sa.Column("entity_id", sa.Integer, nullable=False),
            sa.Column("entity_stable_id", sa.String(180), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("author", sa.String(200), nullable=True),
            sa.Column("rationale", sa.Text, nullable=True),
            sa.Column("cutoff_chapter", sa.Integer, nullable=False),
            sa.Column("claim_hash", sa.String(64), nullable=False),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("projection_hash", sa.String(64), nullable=False),
            sa.Column("schema_version", sa.String(64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "id",
                name="uq_visual_bible_claims_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "claim_key",
                name="uq_visual_bible_claims_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_visual_bible_claims_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_claims_version_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "entity_id"],
                [
                    "visual_bible_entities.owner_id",
                    "visual_bible_entities.novel_id",
                    "visual_bible_entities.id",
                ],
                ondelete="RESTRICT",
                name="fk_visual_bible_claims_entity_scope",
            ),
            sa.CheckConstraint(
                f"authority IN ({_AUTHORITY_LABELS})",
                name="ck_visual_bible_claims_authority",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_visual_bible_claims_cutoff",
            ),
            sa.CheckConstraint(
                "length(claim_hash) = 64",
                name="ck_visual_bible_claims_claim_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_visual_bible_claims_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_visual_bible_claims_idempotency_key",
            ),
        )
        op.create_index(
            "idx_visual_bible_claims_scope",
            "visual_bible_claims",
            ["owner_id", "novel_id", "version_id"],
        )
        op.create_index(
            "idx_visual_bible_claims_entity",
            "visual_bible_claims",
            ["owner_id", "novel_id", "entity_stable_id"],
        )
        op.create_index(
            "idx_visual_bible_claims_authority",
            "visual_bible_claims",
            ["owner_id", "novel_id", "authority"],
        )

    # ---- visual_bible_evidence_refs --------------------------------
    if not _has_table("visual_bible_evidence_refs"):
        op.create_table(
            "visual_bible_evidence_refs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("version_id", sa.Integer, nullable=False),
            sa.Column("claim_id", sa.Integer, nullable=False),
            sa.Column("evidence_key", sa.String(180), nullable=False),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column(
                "chapter_id",
                sa.Integer,
                sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            ),
            sa.Column("chapter_number", sa.Integer, nullable=False),
            sa.Column("source_start", sa.Integer, nullable=False),
            sa.Column("source_end", sa.Integer, nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("excerpt", sa.Text, nullable=True),
            sa.Column("cutoff_chapter", sa.Integer, nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "id",
                name="uq_visual_bible_evidence_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "claim_id",
                "evidence_key",
                name="uq_visual_bible_evidence_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_visual_bible_evidence_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_evidence_version_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "claim_id"],
                [
                    "visual_bible_claims.owner_id",
                    "visual_bible_claims.novel_id",
                    "visual_bible_claims.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_evidence_claim_scope",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_visual_bible_evidence_offsets",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_visual_bible_evidence_chapter",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_visual_bible_evidence_cutoff",
            ),
            sa.CheckConstraint(
                "chapter_number <= cutoff_chapter",
                name="ck_visual_bible_evidence_spoiler_cutoff",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_visual_bible_evidence_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_visual_bible_evidence_content_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_visual_bible_evidence_idempotency_key",
            ),
        )
        op.create_index(
            "idx_visual_bible_evidence_scope",
            "visual_bible_evidence_refs",
            ["owner_id", "novel_id", "version_id"],
        )
        op.create_index(
            "idx_visual_bible_evidence_claim",
            "visual_bible_evidence_refs",
            ["claim_id"],
        )

    # ---- visual_bible_reference_assets --------------------------------
    if not _has_table("visual_bible_reference_assets"):
        op.create_table(
            "visual_bible_reference_assets",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("version_id", sa.Integer, nullable=False),
            sa.Column("asset_key", sa.String(180), nullable=False),
            sa.Column("asset_id", sa.String(200), nullable=False),
            sa.Column("mime_type", sa.String(100), nullable=False),
            sa.Column("bytes_hash", sa.String(64), nullable=False),
            sa.Column(
                "rights_status",
                sa.String(32),
                nullable=False,
                server_default="unreviewed",
            ),
            sa.Column("provenance", JSONB, nullable=False),
            sa.Column(
                "approved",
                sa.Boolean,
                nullable=False,
                server_default="false",
            ),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column("projection_hash", sa.String(64), nullable=False),
            sa.Column("schema_version", sa.String(64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "id",
                name="uq_visual_bible_assets_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "asset_key",
                name="uq_visual_bible_assets_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_visual_bible_assets_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_assets_version_scope",
            ),
            sa.CheckConstraint(
                f"rights_status IN ({_RIGHTS_STATUSES})",
                name="ck_visual_bible_assets_rights_status",
            ),
            sa.CheckConstraint(
                "length(bytes_hash) = 64",
                name="ck_visual_bible_assets_bytes_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_visual_bible_assets_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_visual_bible_assets_idempotency_key",
            ),
        )
        op.create_index(
            "idx_visual_bible_assets_scope",
            "visual_bible_reference_assets",
            ["owner_id", "novel_id", "version_id"],
        )

    # ---- visual_bible_review_events --------------------------------
    if not _has_table("visual_bible_review_events"):
        op.create_table(
            "visual_bible_review_events",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("version_id", sa.Integer, nullable=False),
            sa.Column("action", sa.String(24), nullable=False),
            sa.Column("actor_source", sa.String(16), nullable=False),
            sa.Column("actor", sa.String(200), nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("event_key", sa.String(160), nullable=False),
            sa.Column("from_review_state", sa.String(16), nullable=False),
            sa.Column("to_review_state", sa.String(16), nullable=False),
            sa.Column("details", JSONB, nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "event_key",
                name="uq_visual_bible_review_events_key",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_visual_bible_review_events_version_scope",
            ),
            sa.CheckConstraint(
                f"action IN ({_REVIEW_ACTIONS})",
                name="ck_visual_bible_review_events_action",
            ),
            sa.CheckConstraint(
                f"actor_source IN ({_ACTOR_SOURCES})",
                name="ck_visual_bible_review_events_actor_source",
            ),
            sa.CheckConstraint(
                f"from_review_state IN ({_REVIEW_STATES})",
                name="ck_visual_bible_review_events_from_state",
            ),
            sa.CheckConstraint(
                f"to_review_state IN ({_REVIEW_STATES})",
                name="ck_visual_bible_review_events_to_state",
            ),
        )
        op.create_index(
            "idx_visual_bible_review_events_version",
            "visual_bible_review_events",
            ["owner_id", "novel_id", "version_id"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched by logic."""
    if _has_table("visual_bible_review_events"):
        op.drop_index(
            "idx_visual_bible_review_events_version",
            table_name="visual_bible_review_events",
        )
        op.drop_table("visual_bible_review_events")

    if _has_table("visual_bible_reference_assets"):
        op.drop_index(
            "idx_visual_bible_assets_scope",
            table_name="visual_bible_reference_assets",
        )
        op.drop_table("visual_bible_reference_assets")

    if _has_table("visual_bible_evidence_refs"):
        op.drop_index(
            "idx_visual_bible_evidence_claim",
            table_name="visual_bible_evidence_refs",
        )
        op.drop_index(
            "idx_visual_bible_evidence_scope",
            table_name="visual_bible_evidence_refs",
        )
        op.drop_table("visual_bible_evidence_refs")

    if _has_table("visual_bible_claims"):
        op.drop_index(
            "idx_visual_bible_claims_authority",
            table_name="visual_bible_claims",
        )
        op.drop_index(
            "idx_visual_bible_claims_entity",
            table_name="visual_bible_claims",
        )
        op.drop_index(
            "idx_visual_bible_claims_scope",
            table_name="visual_bible_claims",
        )
        op.drop_table("visual_bible_claims")

    if _has_table("visual_bible_entities"):
        op.drop_index(
            "idx_visual_bible_entities_type",
            table_name="visual_bible_entities",
        )
        op.drop_index(
            "idx_visual_bible_entities_scope",
            table_name="visual_bible_entities",
        )
        op.drop_table("visual_bible_entities")

    if _has_table("visual_bible_versions"):
        op.drop_index(
            "idx_visual_bible_versions_scope",
            table_name="visual_bible_versions",
        )
        op.drop_table("visual_bible_versions")
