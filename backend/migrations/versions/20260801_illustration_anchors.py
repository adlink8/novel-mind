"""Hash-verified illustration anchor tables (Phase 34-01, REQ-VIS-05).

D-34-01 / D-34-03: an approved illustration stays consistent between the
reader and every export through a hash-verified anchor bound to
owner/novel/chapter, an immutable source snapshot, exact source coordinates
and the proposal-ready AssetRevision. New append-only tables:

  - ``illustration_anchor_proposals``: candidate proposals carrying the exact
    source span (excerpt + anchor hash), the frozen chapter content hash /
    source snapshot, the proposal-ready AssetRevision ref and the Web approval
    request id. The lifecycle status is ``proposed`` / ``pending_approval`` /
    ``valid`` / ``needs_repair`` / ``invalid``; only the 34-05 deterministic
    publish transaction may fill published_asset_revision_id +
    publish_manifest_hash and enter ``valid`` (fail-closed check).
  - ``illustration_anchors``: the published, reader/export-visible anchor
    created only by the deterministic publish transaction. A valid anchor must
    bind an approved action (approval_request_id), the published AssetRevision
    and a frozen publish manifest hash; stale/missing presentation is explicit
    (``needs_repair`` / ``invalid``), never a broken URL or silent drop.

Design conventions (matching 20260801_illustration_jobs): idempotent inspector
guards, symmetric downgrade, composite owner/novel scope FKs, composite
idempotency constraints and a JSONB-with-SQLite-variant for tests. No existing
asset/review/approval rows are touched.

Revision ID: 20260801_illustration_anchors
Revises: 20260801_illustration_jobs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_illustration_anchors"
down_revision = "20260801_illustration_jobs"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_ANCHOR_STATUSES = (
    "'proposed','pending_approval','valid','needs_repair','invalid'"
)
_PUBLISHED_STATUSES = "'valid','needs_repair','invalid'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""

    # ---- illustration_anchor_proposals --------------------------------
    if not _has_table("illustration_anchor_proposals"):
        op.create_table(
            "illustration_anchor_proposals",
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
            sa.Column(
                "chapter_id",
                sa.Integer,
                sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chapter_number", sa.Integer, nullable=False),
            sa.Column("proposal_key", sa.String(160), nullable=False),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column("paragraph_start", sa.Integer, nullable=True),
            sa.Column("paragraph_end", sa.Integer, nullable=True),
            sa.Column("source_start", sa.Integer, nullable=False),
            sa.Column("source_end", sa.Integer, nullable=False),
            sa.Column("excerpt", sa.Text, nullable=False),
            sa.Column("anchor_hash", sa.String(64), nullable=False),
            sa.Column("chapter_content_hash", sa.String(64), nullable=False),
            sa.Column("proposal_asset_revision_id", sa.Integer, nullable=False),
            sa.Column(
                "approval_request_id",
                sa.Integer,
                sa.ForeignKey("approval_requests.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("published_asset_revision_id", sa.Integer, nullable=True),
            sa.Column("publish_manifest_hash", sa.String(64), nullable=True),
            sa.Column(
                "status",
                sa.String(24),
                nullable=False,
                server_default="proposed",
            ),
            sa.Column("caption", sa.String(500), nullable=False),
            sa.Column("alt_text", sa.String(500), nullable=False),
            sa.Column("citation", sa.String(1000), nullable=False),
            sa.Column("approved_by", sa.String(200), nullable=True),
            sa.Column(
                "approved_at",
                sa.DateTime(timezone=True),
                nullable=True,
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
                name="uq_illustration_anchor_proposals_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "proposal_key",
                name="uq_illustration_anchor_proposals_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_illustration_anchor_proposals_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "proposal_asset_revision_id"],
                [
                    "asset_revisions.owner_id",
                    "asset_revisions.novel_id",
                    "asset_revisions.id",
                ],
                ondelete="CASCADE",
                name="fk_illustration_anchor_proposals_asset_scope",
            ),
            sa.CheckConstraint(
                f"status IN ({_ANCHOR_STATUSES})",
                name="ck_illustration_anchor_proposals_status",
            ),
            sa.CheckConstraint(
                "(status = 'valid' AND published_asset_revision_id IS NOT NULL "
                "AND publish_manifest_hash IS NOT NULL) OR status <> 'valid'",
                name="ck_illustration_anchor_proposals_publish_shape",
            ),
            sa.CheckConstraint(
                "(approval_request_id IS NULL) OR status IN "
                "('pending_approval','valid','needs_repair','invalid')",
                name="ck_illustration_anchor_proposals_approval_shape",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_illustration_anchor_proposals_chapter_number",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_illustration_anchor_proposals_offsets",
            ),
            sa.CheckConstraint(
                "(paragraph_start IS NULL AND paragraph_end IS NULL) OR "
                "(paragraph_start >= 1 AND paragraph_end >= paragraph_start)",
                name="ck_illustration_anchor_proposals_paragraph",
            ),
            sa.CheckConstraint(
                "length(anchor_hash) = 64",
                name="ck_illustration_anchor_proposals_anchor_hash",
            ),
            sa.CheckConstraint(
                "length(chapter_content_hash) = 64",
                name="ck_illustration_anchor_proposals_content_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_illustration_anchor_proposals_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_illustration_anchor_proposals_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_illustration_anchor_proposals_idempotency_key",
            ),
        )
        op.create_index(
            "idx_illustration_anchor_proposals_scope",
            "illustration_anchor_proposals",
            ["owner_id", "novel_id", "status"],
        )
        op.create_index(
            "idx_illustration_anchor_proposals_chapter",
            "illustration_anchor_proposals",
            ["owner_id", "novel_id", "chapter_id"],
        )

    # ---- illustration_anchors ------------------------------------------
    if not _has_table("illustration_anchors"):
        op.create_table(
            "illustration_anchors",
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
            sa.Column(
                "chapter_id",
                sa.Integer,
                sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chapter_number", sa.Integer, nullable=False),
            sa.Column("anchor_key", sa.String(160), nullable=False),
            sa.Column(
                "proposal_id",
                sa.Integer,
                sa.ForeignKey(
                    "illustration_anchor_proposals.id", ondelete="RESTRICT"
                ),
                nullable=False,
            ),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column("paragraph_start", sa.Integer, nullable=True),
            sa.Column("paragraph_end", sa.Integer, nullable=True),
            sa.Column("source_start", sa.Integer, nullable=False),
            sa.Column("source_end", sa.Integer, nullable=False),
            sa.Column("excerpt", sa.Text, nullable=False),
            sa.Column("anchor_hash", sa.String(64), nullable=False),
            sa.Column("chapter_content_hash", sa.String(64), nullable=False),
            sa.Column("published_asset_revision_id", sa.Integer, nullable=False),
            sa.Column("publish_manifest_hash", sa.String(64), nullable=False),
            sa.Column(
                "approval_request_id",
                sa.Integer,
                sa.ForeignKey("approval_requests.id", ondelete="SET NULL"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(24),
                nullable=False,
                server_default="valid",
            ),
            sa.Column("caption", sa.String(500), nullable=False),
            sa.Column("alt_text", sa.String(500), nullable=False),
            sa.Column("citation", sa.String(1000), nullable=False),
            sa.Column("approved_by", sa.String(200), nullable=True),
            sa.Column(
                "approved_at",
                sa.DateTime(timezone=True),
                nullable=True,
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
                name="uq_illustration_anchors_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "anchor_key",
                name="uq_illustration_anchors_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_illustration_anchors_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "published_asset_revision_id"],
                [
                    "asset_revisions.owner_id",
                    "asset_revisions.novel_id",
                    "asset_revisions.id",
                ],
                ondelete="CASCADE",
                name="fk_illustration_anchors_published_asset_scope",
            ),
            sa.CheckConstraint(
                f"status IN ({_PUBLISHED_STATUSES})",
                name="ck_illustration_anchors_status",
            ),
            sa.CheckConstraint(
                "(status = 'valid' AND published_asset_revision_id IS NOT NULL "
                "AND publish_manifest_hash IS NOT NULL "
                "AND approval_request_id IS NOT NULL) OR "
                "status IN ('needs_repair','invalid')",
                name="ck_illustration_anchors_publish_shape",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_illustration_anchors_chapter_number",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_illustration_anchors_offsets",
            ),
            sa.CheckConstraint(
                "(paragraph_start IS NULL AND paragraph_end IS NULL) OR "
                "(paragraph_start >= 1 AND paragraph_end >= paragraph_start)",
                name="ck_illustration_anchors_paragraph",
            ),
            sa.CheckConstraint(
                "length(anchor_hash) = 64",
                name="ck_illustration_anchors_anchor_hash",
            ),
            sa.CheckConstraint(
                "length(chapter_content_hash) = 64",
                name="ck_illustration_anchors_content_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_illustration_anchors_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_illustration_anchors_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_illustration_anchors_idempotency_key",
            ),
        )
        op.create_index(
            "idx_illustration_anchors_scope",
            "illustration_anchors",
            ["owner_id", "novel_id", "status"],
        )
        op.create_index(
            "idx_illustration_anchors_chapter",
            "illustration_anchors",
            ["owner_id", "novel_id", "chapter_id"],
        )
        op.create_index(
            "idx_illustration_anchors_proposal",
            "illustration_anchors",
            ["proposal_id"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched by logic."""
    if _has_table("illustration_anchors"):
        op.drop_index(
            "idx_illustration_anchors_proposal",
            table_name="illustration_anchors",
        )
        op.drop_index(
            "idx_illustration_anchors_chapter",
            table_name="illustration_anchors",
        )
        op.drop_index(
            "idx_illustration_anchors_scope",
            table_name="illustration_anchors",
        )
        op.drop_table("illustration_anchors")

    if _has_table("illustration_anchor_proposals"):
        op.drop_index(
            "idx_illustration_anchor_proposals_chapter",
            table_name="illustration_anchor_proposals",
        )
        op.drop_index(
            "idx_illustration_anchor_proposals_scope",
            table_name="illustration_anchor_proposals",
        )
        op.drop_table("illustration_anchor_proposals")
