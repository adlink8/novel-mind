"""Key Scene candidate Artifact contract tables (Phase 31-01).

REQ-VIS-02/06 / D-31-01..D-31-05: scene candidates are evidence-first,
candidate-only, versioned Artifacts. New append-only tables:
  - ``key_scene_sets``: immutable versioned candidate-set manifest with source
    snapshot, detector/policy lineage, spoiler cutoff, candidate-only review
    state and the approved Visual Bible revision the set was frozen against.
  - ``key_scene_candidates``: one ranked candidate carrying chapter/range/source
    hash, narrative coordinates (cast/place/time/POV), spoiler cutoff, salience
    reasons, diversity key, score breakdown, detector lineage and the optional
    advisory ``SpeakerDialogueHeuristicSignal`` metadata (REQ-VIS-06). Content
    rows are append-only; a candidate is never canon.
  - ``key_scene_evidence_ranges``: source-linked evidence ranges with source
    snapshot, chapter/range, offsets, content hash and a DB-level
    ``chapter_number <= cutoff_chapter`` spoiler gate. Evidence is the only
    citation authority a candidate carries.
  - ``key_scene_review_decisions``: append-only review decisions
    (approve/reject/needs_relink/supersede) with idempotent decision keys and
    an optional per-candidate ``candidate_key``.

Design conventions (matching 20260801_visual_bible/2703/2801): idempotent
inspector guards, symmetric downgrade, JSONB via ``JSONB().with_variant(JSON(),
"sqlite")``, composite owner/novel/version scope FKs and composite idempotency
constraints. No existing cover/upload/chapter rows are touched.

Revision ID: 20260801_key_scene
Revises: 20260801_visual_bible
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_key_scene"
down_revision = "20260801_visual_bible"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_REVIEW_ACTIONS = "'approve','reject','needs_relink','supersede'"
_REVIEW_STATES = (
    "'candidate','approved','rejected','superseded','needs_relink'"
)
_ACTOR_SOURCES = "'human','machine'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""

    # ---- key_scene_sets ------------------------------------------------
    if not _has_table("key_scene_sets"):
        op.create_table(
            "key_scene_sets",
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
                "parent_set_id",
                sa.Integer,
                sa.ForeignKey("key_scene_sets.id", ondelete="SET NULL"),
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
            sa.Column("detector_id", sa.String(120), nullable=False),
            sa.Column("detector_version", sa.String(64), nullable=False),
            sa.Column("manifest_hash", sa.String(64), nullable=False),
            sa.Column(
                "approved_visual_bible_revision_id", sa.Integer, nullable=True
            ),
            sa.Column(
                "approved_visual_bible_revision_hash",
                sa.String(64),
                nullable=True,
            ),
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
                name="uq_key_scene_sets_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "version_key",
                name="uq_key_scene_sets_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_key_scene_sets_idempotency",
            ),
            sa.ForeignKeyConstraint(
                [
                    "owner_id",
                    "novel_id",
                    "approved_visual_bible_revision_id",
                ],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="SET NULL",
                name="fk_key_scene_sets_visual_bible_approval",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_key_scene_sets_revision",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_key_scene_sets_cutoff",
            ),
            sa.CheckConstraint(
                "length(manifest_hash) = 64",
                name="ck_key_scene_sets_manifest_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_key_scene_sets_schema_hash",
            ),
            sa.CheckConstraint(
                "length(policy_hash) = 64",
                name="ck_key_scene_sets_policy_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_key_scene_sets_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_key_scene_sets_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_key_scene_sets_idempotency_key",
            ),
            sa.CheckConstraint(
                "(approved_visual_bible_revision_id IS NULL AND "
                "approved_visual_bible_revision_hash IS NULL) OR "
                "(approved_visual_bible_revision_id IS NOT NULL AND "
                "length(approved_visual_bible_revision_hash) = 64)",
                name="ck_key_scene_sets_visual_bible_approval",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_REVIEW_STATES})",
                name="ck_key_scene_sets_review_state",
            ),
        )
        op.create_index(
            "idx_key_scene_sets_scope",
            "key_scene_sets",
            ["owner_id", "novel_id", "review_state"],
        )

    # ---- key_scene_candidates -----------------------------------------
    if not _has_table("key_scene_candidates"):
        op.create_table(
            "key_scene_candidates",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("set_id", sa.Integer, nullable=False),
            sa.Column("candidate_key", sa.String(180), nullable=False),
            sa.Column("candidate_order", sa.Integer, nullable=False),
            sa.Column("scene_id", sa.String(200), nullable=False),
            sa.Column(
                "chapter_id",
                sa.Integer,
                sa.ForeignKey("chapters.id", ondelete="SET NULL"),
            ),
            sa.Column("chapter_number", sa.Integer, nullable=False),
            sa.Column("source_start", sa.Integer, nullable=False),
            sa.Column("source_end", sa.Integer, nullable=False),
            sa.Column("source_hash", sa.String(64), nullable=False),
            sa.Column("coordinates", JSONB, nullable=False),
            sa.Column("spoiler_cutoff", sa.Integer, nullable=False),
            sa.Column("salience_reasons", JSONB, nullable=True),
            sa.Column("score_total", sa.Float, nullable=False),
            sa.Column("score_breakdown", JSONB, nullable=True),
            sa.Column("diversity_key", sa.String(200), nullable=False),
            sa.Column("detector_id", sa.String(120), nullable=False),
            sa.Column("detector_version", sa.String(64), nullable=False),
            sa.Column("policy_hash", sa.String(64), nullable=False),
            sa.Column(
                "review_state",
                sa.String(16),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column("heuristic_signal", JSONB, nullable=True),
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
                name="uq_key_scene_candidates_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "set_id",
                "candidate_key",
                name="uq_key_scene_candidates_key",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "set_id",
                "candidate_order",
                name="uq_key_scene_candidates_order",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "set_id",
                "id",
                name="uq_key_scene_candidates_set_scope",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_key_scene_candidates_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "set_id"],
                [
                    "key_scene_sets.owner_id",
                    "key_scene_sets.novel_id",
                    "key_scene_sets.id",
                ],
                ondelete="CASCADE",
                name="fk_key_scene_candidates_set_scope",
            ),
            sa.CheckConstraint(
                "candidate_order >= 0",
                name="ck_key_scene_candidates_order",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_key_scene_candidates_chapter",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_key_scene_candidates_offsets",
            ),
            sa.CheckConstraint(
                "chapter_number <= spoiler_cutoff",
                name="ck_key_scene_candidates_spoiler_cutoff",
            ),
            sa.CheckConstraint(
                "spoiler_cutoff >= 1",
                name="ck_key_scene_candidates_cutoff",
            ),
            sa.CheckConstraint(
                "score_total >= 0",
                name="ck_key_scene_candidates_score",
            ),
            sa.CheckConstraint(
                "length(source_hash) = 64",
                name="ck_key_scene_candidates_source_hash",
            ),
            sa.CheckConstraint(
                "length(policy_hash) = 64",
                name="ck_key_scene_candidates_policy_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_key_scene_candidates_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_key_scene_candidates_idempotency_key",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_REVIEW_STATES})",
                name="ck_key_scene_candidates_review_state",
            ),
        )
        op.create_index(
            "idx_key_scene_candidates_scope",
            "key_scene_candidates",
            ["owner_id", "novel_id", "set_id"],
        )

    # ---- key_scene_evidence_ranges ------------------------------------
    if not _has_table("key_scene_evidence_ranges"):
        op.create_table(
            "key_scene_evidence_ranges",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("set_id", sa.Integer, nullable=False),
            sa.Column("candidate_id", sa.Integer, nullable=False),
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
                name="uq_key_scene_evidence_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "set_id",
                "candidate_id",
                "evidence_key",
                name="uq_key_scene_evidence_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_key_scene_evidence_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "set_id"],
                [
                    "key_scene_sets.owner_id",
                    "key_scene_sets.novel_id",
                    "key_scene_sets.id",
                ],
                ondelete="CASCADE",
                name="fk_key_scene_evidence_set_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "candidate_id"],
                [
                    "key_scene_candidates.owner_id",
                    "key_scene_candidates.novel_id",
                    "key_scene_candidates.id",
                ],
                ondelete="CASCADE",
                name="fk_key_scene_evidence_candidate_scope",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_key_scene_evidence_offsets",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_key_scene_evidence_chapter",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_key_scene_evidence_cutoff",
            ),
            sa.CheckConstraint(
                "chapter_number <= cutoff_chapter",
                name="ck_key_scene_evidence_spoiler_cutoff",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_key_scene_evidence_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_key_scene_evidence_content_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_key_scene_evidence_idempotency_key",
            ),
        )
        op.create_index(
            "idx_key_scene_evidence_scope",
            "key_scene_evidence_ranges",
            ["owner_id", "novel_id", "set_id"],
        )
        op.create_index(
            "idx_key_scene_evidence_candidate",
            "key_scene_evidence_ranges",
            ["candidate_id"],
        )

    # ---- key_scene_review_decisions -----------------------------------
    if not _has_table("key_scene_review_decisions"):
        op.create_table(
            "key_scene_review_decisions",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("set_id", sa.Integer, nullable=False),
            sa.Column("decision_key", sa.String(160), nullable=False),
            sa.Column("action", sa.String(24), nullable=False),
            sa.Column("actor_source", sa.String(16), nullable=False),
            sa.Column("actor", sa.String(200), nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("from_review_state", sa.String(16), nullable=False),
            sa.Column("to_review_state", sa.String(16), nullable=False),
            sa.Column("candidate_key", sa.String(180), nullable=True),
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
                "set_id",
                "decision_key",
                name="uq_key_scene_review_decisions_key",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "set_id"],
                [
                    "key_scene_sets.owner_id",
                    "key_scene_sets.novel_id",
                    "key_scene_sets.id",
                ],
                ondelete="CASCADE",
                name="fk_key_scene_review_decisions_set_scope",
            ),
            sa.CheckConstraint(
                f"action IN ({_REVIEW_ACTIONS})",
                name="ck_key_scene_review_decisions_action",
            ),
            sa.CheckConstraint(
                f"actor_source IN ({_ACTOR_SOURCES})",
                name="ck_key_scene_review_decisions_actor_source",
            ),
            sa.CheckConstraint(
                f"from_review_state IN ({_REVIEW_STATES})",
                name="ck_key_scene_review_decisions_from_state",
            ),
            sa.CheckConstraint(
                f"to_review_state IN ({_REVIEW_STATES})",
                name="ck_key_scene_review_decisions_to_state",
            ),
        )
        op.create_index(
            "idx_key_scene_review_decisions_set",
            "key_scene_review_decisions",
            ["owner_id", "novel_id", "set_id"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched by logic."""
    if _has_table("key_scene_review_decisions"):
        op.drop_index(
            "idx_key_scene_review_decisions_set",
            table_name="key_scene_review_decisions",
        )
        op.drop_table("key_scene_review_decisions")

    if _has_table("key_scene_evidence_ranges"):
        op.drop_index(
            "idx_key_scene_evidence_candidate",
            table_name="key_scene_evidence_ranges",
        )
        op.drop_index(
            "idx_key_scene_evidence_scope",
            table_name="key_scene_evidence_ranges",
        )
        op.drop_table("key_scene_evidence_ranges")

    if _has_table("key_scene_candidates"):
        op.drop_index(
            "idx_key_scene_candidates_scope",
            table_name="key_scene_candidates",
        )
        op.drop_table("key_scene_candidates")

    if _has_table("key_scene_sets"):
        op.drop_index(
            "idx_key_scene_sets_scope",
            table_name="key_scene_sets",
        )
        op.drop_table("key_scene_sets")
