"""Phase 38-01: forked Visual Bible schema and lineage (D-38-01/D-38-02).

Creates the derivative Visual Bible tables behind the REQ-FORK-04 / REQ-CRE-06
contract:

- ``derivative_visual_versions``: immutable derivative revision with a sealed
  ``visual_namespace = 'fanfiction_visual'`` (D-38-01), an immutable Original
  Visual Bible snapshot reference (``source_version_id`` composite RESTRICT FK
  into ``visual_bible_versions`` + ``source_snapshot_hash`` +
  ``source_manifest_hash``), an explicit ``divergence`` declaration (D-38-02)
  and owner/project/fork provenance. Only the ``review_state`` projection may
  change; every other column is frozen.
- ``derivative_visual_entities`` / ``derivative_visual_assets``: append-only
  identity/style and reference-asset rows, each pinning the exact Original row
  it derives from (``source_entity_ref`` / ``source_asset_ref``).
- ``derivative_visual_review_events``: append-only human/machine review actions
  with idempotent event keys.

The source ``visual_bible_versions`` rows are referenced read-only through the
``fk_derivative_visual_versions_source_scope`` composite RESTRICT FK, so an
Original Visual Bible snapshot can never be overwritten or deleted while a
derivative references it.

Revision ID: 20260802_derivative_visual01
Revises: 20260802_derivative_override01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260802_derivative_visual01"
down_revision = "20260802_derivative_override01"
branch_labels = None
depends_on = None

VERSIONS = "derivative_visual_versions"
ENTITIES = "derivative_visual_entities"
ASSETS = "derivative_visual_assets"
REVIEW_EVENTS = "derivative_visual_review_events"

# JSONB on PostgreSQL, plain JSON on SQLite (matches the ORM model variant so
# alembic check reports no drift).
JSONB = sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_STATES = "'candidate','approved','rejected','superseded','needs_relink'"
_ACTIONS = "'approve','reject','edit','supersede','needs_relink'"
_ENTITY_TYPES = "'character','place','item','faction','style'"
_AUTHORITY_LABELS = (
    "'canon_fact','probable_inference','literary_interpretation','user_interpretation'"
)
_RIGHTS_STATUSES = "'unreviewed','cleared','pending','denied'"
_ACTOR_SOURCES = "'human','machine'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the derivative Visual Bible tables (idempotent guard)."""
    if not _has_table(VERSIONS):
        op.create_table(
            VERSIONS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("fork_id", sa.Integer(), nullable=False),
            sa.Column(
                "visual_namespace",
                sa.String(length=32),
                nullable=False,
                server_default="fanfiction_visual",
            ),
            sa.Column("version_key", sa.String(length=160), nullable=False),
            sa.Column("revision_number", sa.Integer(), nullable=False),
            sa.Column("parent_version_id", sa.Integer(), nullable=True),
            sa.Column("source_version_id", sa.Integer(), nullable=False),
            sa.Column("source_snapshot_id", sa.String(length=160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
            sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("cutoff_chapter", sa.Integer(), nullable=False),
            sa.Column("divergence", JSONB, nullable=False),
            sa.Column("provenance", JSONB, nullable=False),
            sa.Column(
                "review_state",
                sa.String(length=16),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="derivative-visual.v1",
            ),
            sa.Column("schema_hash", sa.String(length=64), nullable=False),
            sa.Column("policy_hash", sa.String(length=64), nullable=False),
            sa.Column("prompt_hash", sa.String(length=64), nullable=True),
            sa.Column("model_hash", sa.String(length=64), nullable=True),
            sa.Column("config_hash", sa.String(length=64), nullable=True),
            sa.Column("manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("style_profile", JSONB, nullable=True),
            sa.Column("constraints", JSONB, nullable=True),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("projection_hash", sa.String(length=64), nullable=False),
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
            sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["project_id"], ["derivative_projects.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["fork_id"], ["canon_forks.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["parent_version_id"],
                ["derivative_visual_versions.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "source_version_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="RESTRICT",
                name="fk_derivative_visual_versions_source_scope",
            ),
            sa.UniqueConstraint(
                "owner_id", "novel_id", "id", name="uq_derivative_visual_versions_scope"
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_key",
                name="uq_derivative_visual_versions_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key", name="uq_derivative_visual_versions_idempotency"
            ),
            sa.CheckConstraint(
                "visual_namespace = 'fanfiction_visual'",
                name="ck_derivative_visual_versions_namespace",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_derivative_visual_versions_revision",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1", name="ck_derivative_visual_versions_cutoff"
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_derivative_visual_versions_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(source_manifest_hash) = 64",
                name="ck_derivative_visual_versions_source_manifest_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_derivative_visual_versions_schema_hash",
            ),
            sa.CheckConstraint(
                "length(policy_hash) = 64",
                name="ck_derivative_visual_versions_policy_hash",
            ),
            sa.CheckConstraint(
                "length(manifest_hash) = 64",
                name="ck_derivative_visual_versions_manifest_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_derivative_visual_versions_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_derivative_visual_versions_idempotency_key",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_STATES})",
                name="ck_derivative_visual_versions_review_state",
            ),
        )
        op.create_index(
            "idx_derivative_visual_versions_scope",
            VERSIONS,
            ["owner_id", "novel_id", "review_state"],
        )
        op.create_index(
            "idx_derivative_visual_versions_fork",
            VERSIONS,
            ["owner_id", "novel_id", "fork_id", "visual_namespace"],
        )
        op.create_index(
            "idx_derivative_visual_versions_source",
            VERSIONS,
            ["owner_id", "novel_id", "source_version_id"],
        )

    if not _has_table(ENTITIES):
        op.create_table(
            ENTITIES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=False),
            sa.Column("entity_key", sa.String(length=180), nullable=False),
            sa.Column("stable_id", sa.String(length=180), nullable=False),
            sa.Column("entity_type", sa.String(length=24), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer(), nullable=False),
            sa.Column("authority", sa.String(length=32), nullable=False),
            sa.Column("divergence", JSONB, nullable=False),
            sa.Column("source_entity_ref", JSONB, nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("projection_hash", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "derivative_visual_versions.owner_id",
                    "derivative_visual_versions.novel_id",
                    "derivative_visual_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_derivative_visual_entities_version_scope",
            ),
            sa.UniqueConstraint(
                "owner_id", "novel_id", "id", name="uq_derivative_visual_entities_scope"
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "stable_id",
                name="uq_derivative_visual_entities_stable_id",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "entity_key",
                name="uq_derivative_visual_entities_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key", name="uq_derivative_visual_entities_idempotency"
            ),
            sa.CheckConstraint(
                f"entity_type IN ({_ENTITY_TYPES})",
                name="ck_derivative_visual_entities_entity_type",
            ),
            sa.CheckConstraint(
                f"authority IN ({_AUTHORITY_LABELS})",
                name="ck_derivative_visual_entities_authority",
            ),
            sa.CheckConstraint(
                "disclosure_cutoff >= 1",
                name="ck_derivative_visual_entities_disclosure_cutoff",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_derivative_visual_entities_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_derivative_visual_entities_idempotency_key",
            ),
        )
        op.create_index(
            "idx_derivative_visual_entities_scope",
            ENTITIES,
            ["owner_id", "novel_id", "version_id"],
        )

    if not _has_table(ASSETS):
        op.create_table(
            ASSETS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=False),
            sa.Column("asset_key", sa.String(length=180), nullable=False),
            sa.Column("asset_id", sa.String(length=200), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=False),
            sa.Column("bytes_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "rights_status",
                sa.String(length=32),
                nullable=False,
                server_default="unreviewed",
            ),
            sa.Column("source_asset_ref", JSONB, nullable=False),
            sa.Column("provenance", JSONB, nullable=False),
            sa.Column(
                "approved",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("canonical_payload", JSONB, nullable=False),
            sa.Column("canonical_payload_hash", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column("projection_hash", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "derivative_visual_versions.owner_id",
                    "derivative_visual_versions.novel_id",
                    "derivative_visual_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_derivative_visual_assets_version_scope",
            ),
            sa.UniqueConstraint(
                "owner_id", "novel_id", "id", name="uq_derivative_visual_assets_scope"
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "asset_key",
                name="uq_derivative_visual_assets_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key", name="uq_derivative_visual_assets_idempotency"
            ),
            sa.CheckConstraint(
                f"rights_status IN ({_RIGHTS_STATUSES})",
                name="ck_derivative_visual_assets_rights_status",
            ),
            sa.CheckConstraint(
                "length(bytes_hash) = 64",
                name="ck_derivative_visual_assets_bytes_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_derivative_visual_assets_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_derivative_visual_assets_idempotency_key",
            ),
        )
        op.create_index(
            "idx_derivative_visual_assets_scope",
            ASSETS,
            ["owner_id", "novel_id", "version_id"],
        )

    if not _has_table(REVIEW_EVENTS):
        op.create_table(
            REVIEW_EVENTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("actor_source", sa.String(length=16), nullable=False),
            sa.Column("actor", sa.String(length=200), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("event_key", sa.String(length=160), nullable=False),
            sa.Column("from_review_state", sa.String(length=16), nullable=False),
            sa.Column("to_review_state", sa.String(length=16), nullable=False),
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
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "version_id"],
                [
                    "derivative_visual_versions.owner_id",
                    "derivative_visual_versions.novel_id",
                    "derivative_visual_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_derivative_visual_review_events_version_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_id",
                "event_key",
                name="uq_derivative_visual_review_events_key",
            ),
            sa.CheckConstraint(
                f"action IN ({_ACTIONS})",
                name="ck_derivative_visual_review_events_action",
            ),
            sa.CheckConstraint(
                f"actor_source IN ({_ACTOR_SOURCES})",
                name="ck_derivative_visual_review_events_actor_source",
            ),
            sa.CheckConstraint(
                f"from_review_state IN ({_STATES})",
                name="ck_derivative_visual_review_events_from_state",
            ),
            sa.CheckConstraint(
                f"to_review_state IN ({_STATES})",
                name="ck_derivative_visual_review_events_to_state",
            ),
        )
        op.create_index(
            "idx_derivative_visual_review_events_version",
            REVIEW_EVENTS,
            ["owner_id", "novel_id", "version_id"],
        )


def downgrade() -> None:
    """Drop the derivative Visual Bible tables symmetrically."""
    for table, indexes in (
        (REVIEW_EVENTS, ("idx_derivative_visual_review_events_version",)),
        (ASSETS, ("idx_derivative_visual_assets_scope",)),
        (ENTITIES, ("idx_derivative_visual_entities_scope",)),
        (
            VERSIONS,
            (
                "idx_derivative_visual_versions_scope",
                "idx_derivative_visual_versions_fork",
                "idx_derivative_visual_versions_source",
            ),
        ),
    ):
        if not _has_table(table):
            continue
        for index in indexes:
            try:
                op.drop_index(index, table_name=table)
            except Exception:
                pass
        op.drop_table(table)
