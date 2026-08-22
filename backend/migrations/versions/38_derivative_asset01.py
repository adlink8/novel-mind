"""Phase 38-03: derivative asset candidate storage and review lineage (D-38-03).

Creates the derivative asset candidate tables behind the REQ-FORK-04 /
REQ-CRE-06 contract:

- ``derivative_visual_candidates``: immutable candidate asset bytes metadata.
  Records the generated ``asset_id``, the replayed content checksum
  (``content_hash``), the sealed ``visual_namespace = 'fanfiction_visual'``,
  the frozen canonical ``scene_spec_hash`` the asset is bound to, the full
  identity/source/generator lineage, the divergence manifest hash and the
  deterministic cross-chapter consistency review signal. Only the
  ``review_state`` projection may move (candidate / needs_review / approved /
  rejected / superseded / blocked); every other column is frozen.
- ``derivative_visual_candidate_review_events``: append-only human/machine
  review actions with idempotent event keys.

The source ``derivative_visual_versions`` rows are referenced read-only
through the composite ``fk_derivative_visual_candidates_version_scope`` FK, so
an approved derivative fork version can never be overwritten by candidate
storage, and the Original Visual Bible rows are never touched.

Revision ID: 20260802_derivative_asset01
Revises: 20260802_derivative_visual01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260802_derivative_asset01"
down_revision = "20260802_derivative_visual01"
branch_labels = None
depends_on = None

CANDIDATES = "derivative_visual_candidates"
REVIEW_EVENTS = "derivative_visual_candidate_review_events"

# JSONB on PostgreSQL, plain JSON on SQLite (matches the ORM model variant so
# alembic check reports no drift).
JSONB = sa.dialects.postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_STATES = "'candidate','needs_review','approved','rejected','superseded','blocked'"
_ACTIONS = "'approve','reject','supersede'"
_VERDICTS = "'pass','concern','fail','unavailable'"
_ACTOR_SOURCES = "'human','machine'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Create the derivative asset candidate tables (idempotent guard)."""
    if not _has_table(CANDIDATES):
        op.create_table(
            CANDIDATES,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("fork_id", sa.Integer(), nullable=False),
            sa.Column("visual_version_id", sa.Integer(), nullable=False),
            sa.Column("visual_version_hash", sa.String(length=64), nullable=False),
            sa.Column("version_key", sa.String(length=160), nullable=False),
            sa.Column("asset_key", sa.String(length=180), nullable=False),
            sa.Column("asset_id", sa.String(length=200), nullable=False),
            sa.Column("storage_key", sa.String(length=320), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column(
                "visual_namespace",
                sa.String(length=32),
                nullable=False,
                server_default="fanfiction_visual",
            ),
            sa.Column("scene_spec_hash", sa.String(length=64), nullable=False),
            sa.Column("chapter_number", sa.Integer(), nullable=False),
            sa.Column("source_snapshot_id", sa.String(length=160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(length=64), nullable=False),
            sa.Column("source_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("cutoff_chapter", sa.Integer(), nullable=False),
            sa.Column("identity_key", sa.String(length=180), nullable=False),
            sa.Column("identity_lineage", JSONB, nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
            sa.Column("generator_lineage", JSONB, nullable=False),
            sa.Column("divergence_manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("consistency_evidence", JSONB, nullable=False),
            sa.Column("consistency_report", JSONB, nullable=False),
            sa.Column("consistency_verdict", sa.String(length=24), nullable=False),
            sa.Column(
                "review_state",
                sa.String(length=16),
                nullable=False,
                server_default="candidate",
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
                ["owner_id", "novel_id", "visual_version_id"],
                [
                    "derivative_visual_versions.owner_id",
                    "derivative_visual_versions.novel_id",
                    "derivative_visual_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_derivative_visual_candidates_version_scope",
            ),
            sa.UniqueConstraint(
                "owner_id", "novel_id", "id", name="uq_derivative_visual_candidates_scope"
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "visual_version_id",
                "asset_key",
                name="uq_derivative_visual_candidates_key",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "visual_version_id",
                "asset_id",
                name="uq_derivative_visual_candidates_asset_id",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_derivative_visual_candidates_idempotency",
            ),
            sa.CheckConstraint(
                "visual_namespace = 'fanfiction_visual'",
                name="ck_derivative_visual_candidates_namespace",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_STATES})",
                name="ck_derivative_visual_candidates_review_state",
            ),
            sa.CheckConstraint(
                f"consistency_verdict IN ({_VERDICTS})",
                name="ck_derivative_visual_candidates_verdict",
            ),
            sa.CheckConstraint(
                "size_bytes > 0", name="ck_derivative_visual_candidates_size"
            ),
            sa.CheckConstraint(
                "chapter_number >= 1", name="ck_derivative_visual_candidates_chapter"
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1", name="ck_derivative_visual_candidates_cutoff"
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_derivative_visual_candidates_content_hash",
            ),
            sa.CheckConstraint(
                "length(scene_spec_hash) = 64",
                name="ck_derivative_visual_candidates_scene_spec_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_derivative_visual_candidates_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(source_manifest_hash) = 64",
                name="ck_derivative_visual_candidates_source_manifest_hash",
            ),
            sa.CheckConstraint(
                "length(divergence_manifest_hash) = 64",
                name="ck_derivative_visual_candidates_divergence_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_derivative_visual_candidates_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_derivative_visual_candidates_idempotency_key",
            ),
        )
        op.create_index(
            "idx_derivative_visual_candidates_scope",
            CANDIDATES,
            ["owner_id", "novel_id", "review_state"],
        )
        op.create_index(
            "idx_derivative_visual_candidates_identity",
            CANDIDATES,
            ["owner_id", "novel_id", "visual_version_id", "identity_key"],
        )

    if not _has_table(REVIEW_EVENTS):
        op.create_table(
            REVIEW_EVENTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("novel_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=False),
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
                ["owner_id", "novel_id", "candidate_id"],
                [
                    "derivative_visual_candidates.owner_id",
                    "derivative_visual_candidates.novel_id",
                    "derivative_visual_candidates.id",
                ],
                ondelete="CASCADE",
                name="fk_derivative_visual_candidate_review_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "candidate_id",
                "event_key",
                name="uq_derivative_visual_candidate_review_key",
            ),
            sa.CheckConstraint(
                f"action IN ({_ACTIONS})",
                name="ck_derivative_visual_candidate_review_action",
            ),
            sa.CheckConstraint(
                f"actor_source IN ({_ACTOR_SOURCES})",
                name="ck_derivative_visual_candidate_review_actor_source",
            ),
            sa.CheckConstraint(
                f"from_review_state IN ({_STATES})",
                name="ck_derivative_visual_candidate_review_from_state",
            ),
            sa.CheckConstraint(
                f"to_review_state IN ({_STATES})",
                name="ck_derivative_visual_candidate_review_to_state",
            ),
        )
        op.create_index(
            "idx_derivative_visual_candidate_review_candidate",
            REVIEW_EVENTS,
            ["owner_id", "novel_id", "candidate_id"],
        )


def downgrade() -> None:
    """Drop the derivative asset candidate tables symmetrically."""
    for table, indexes in (
        (REVIEW_EVENTS, ("idx_derivative_visual_candidate_review_candidate",)),
        (
            CANDIDATES,
            (
                "idx_derivative_visual_candidates_scope",
                "idx_derivative_visual_candidates_identity",
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
