"""Scene Spec and Prompt Revision candidate Artifact contract tables (Phase 32-01).

REQ-VIS-03 / D-32-01..D-32-04: ``SceneSpec`` is the canonical candidate
Artifact and compiled prompts are derived, provider-neutral candidate revisions.
New append-only tables:
  - ``scene_spec_versions``: immutable versioned candidate spec manifest with the
    source SceneCandidate hash, approved Visual Bible revision, source snapshot,
    spoiler cutoff, compiler/policy/config lineage, candidate-only review state
    and a replayable canonical content hash.
  - ``scene_spec_details``: one canonical detail row (subject/action/setting/
    composition/style/continuity), each source-bounded (evidence / visual_bible /
    user_interpretation).
  - ``scene_spec_negative_constraints``: forbidden costume/era/identity/style/
    physical/continuity exclusions, also source-bounded.
  - ``scene_spec_evidence_refs``: source-linked evidence with source snapshot,
    chapter/range, offsets, content hash and a DB-level
    ``chapter_number <= cutoff_chapter`` spoiler gate.
  - ``scene_spec_uncertainties``: explicit unresolved items (missing evidence,
    conflicting claim, future spoiler, ambiguous reference); never canon.
  - ``prompt_revisions``: immutable compiled-prompt candidate recording the
    Scene Spec hash, Visual Bible revision, source snapshot, spoiler cutoff,
    schema/prompt-schema/compiler/adapter/config hashes, ``input_hash`` and
    ``prompt_hash`` (which must differ), canonical sections, negative
    constraints, uncertainties and the redacted preview.

Design conventions (matching 20260801_key_scene/visual_bible): idempotent
inspector guards, symmetric downgrade, JSONB via ``JSONB().with_variant(JSON(),
"sqlite")``, composite owner/novel/version scope FKs and composite idempotency
constraints. No existing cover/upload/chapter rows are touched.

Revision ID: 20260801_scene_spec_prompt
Revises: 20260801_key_scene
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_scene_spec_prompt"
down_revision = "20260801_key_scene"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_DETAIL_KINDS = "'subject','action','setting','composition','style','continuity'"
_SOURCES = "'evidence','visual_bible','user_interpretation'"
_SCOPES = "'costume','era','identity','style','physical','continuity'"
_REASONS = "'missing_evidence','conflicting_claim','future_spoiler','ambiguous_reference'"
_REVIEW_STATES = (
    "'candidate','approved','rejected','superseded','needs_relink'"
)


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""

    # ---- scene_spec_versions ------------------------------------------
    if not _has_table("scene_spec_versions"):
        op.create_table(
            "scene_spec_versions",
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
            sa.Column("spec_key", sa.String(120), nullable=False),
            sa.Column("revision_number", sa.Integer, nullable=False),
            sa.Column("scene_candidate_id", sa.Integer, nullable=True),
            sa.Column("scene_candidate_hash", sa.String(64), nullable=False),
            sa.Column("visual_bible_revision_id", sa.Integer, nullable=True),
            sa.Column("visual_bible_revision_hash", sa.String(64), nullable=False),
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
            sa.Column("compiler_id", sa.String(120), nullable=False),
            sa.Column("compiler_version", sa.String(64), nullable=False),
            sa.Column("policy_hash", sa.String(64), nullable=False),
            sa.Column("config_hash", sa.String(64), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
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
                name="uq_scene_spec_versions_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "spec_key",
                name="uq_scene_spec_versions_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_scene_spec_versions_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "scene_candidate_id"],
                [
                    "key_scene_candidates.owner_id",
                    "key_scene_candidates.novel_id",
                    "key_scene_candidates.id",
                ],
                ondelete="SET NULL",
                name="fk_scene_spec_versions_candidate_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "visual_bible_revision_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="SET NULL",
                name="fk_scene_spec_versions_visual_bible_scope",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_scene_spec_versions_revision",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_scene_spec_versions_cutoff",
            ),
            sa.CheckConstraint(
                "length(scene_candidate_hash) = 64",
                name="ck_scene_spec_versions_candidate_hash",
            ),
            sa.CheckConstraint(
                "length(visual_bible_revision_hash) = 64",
                name="ck_scene_spec_versions_vb_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_scene_spec_versions_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_scene_spec_versions_schema_hash",
            ),
            sa.CheckConstraint(
                "length(policy_hash) = 64",
                name="ck_scene_spec_versions_policy_hash",
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_scene_spec_versions_content_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_scene_spec_versions_idempotency_key",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_REVIEW_STATES})",
                name="ck_scene_spec_versions_review_state",
            ),
        )
        op.create_index(
            "idx_scene_spec_versions_scope",
            "scene_spec_versions",
            ["owner_id", "novel_id", "review_state"],
        )

    # ---- scene_spec_details -------------------------------------------
    if not _has_table("scene_spec_details"):
        op.create_table(
            "scene_spec_details",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("spec_id", sa.Integer, nullable=False),
            sa.Column("detail_key", sa.String(180), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("author", sa.String(200), nullable=True),
            sa.Column("rationale", sa.Text, nullable=True),
            sa.Column("spoiler_cutoff", sa.Integer, nullable=False),
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
                name="uq_scene_spec_details_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "spec_id",
                "detail_key",
                name="uq_scene_spec_details_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_scene_spec_details_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "spec_id"],
                [
                    "scene_spec_versions.owner_id",
                    "scene_spec_versions.novel_id",
                    "scene_spec_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_details_spec_scope",
            ),
            sa.CheckConstraint(
                f"kind IN ({_DETAIL_KINDS})",
                name="ck_scene_spec_details_kind",
            ),
            sa.CheckConstraint(
                f"source IN ({_SOURCES})",
                name="ck_scene_spec_details_source",
            ),
            sa.CheckConstraint(
                "spoiler_cutoff >= 1",
                name="ck_scene_spec_details_cutoff",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_scene_spec_details_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_scene_spec_details_idempotency_key",
            ),
        )
        op.create_index(
            "idx_scene_spec_details_scope",
            "scene_spec_details",
            ["owner_id", "novel_id", "spec_id"],
        )

    # ---- scene_spec_negative_constraints ------------------------------
    if not _has_table("scene_spec_negative_constraints"):
        op.create_table(
            "scene_spec_negative_constraints",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("spec_id", sa.Integer, nullable=False),
            sa.Column("constraint_key", sa.String(180), nullable=False),
            sa.Column("scope", sa.String(24), nullable=False),
            sa.Column("source", sa.String(32), nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("author", sa.String(200), nullable=True),
            sa.Column("rationale", sa.Text, nullable=True),
            sa.Column("spoiler_cutoff", sa.Integer, nullable=False),
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
                name="uq_scene_spec_constraints_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "spec_id",
                "constraint_key",
                name="uq_scene_spec_constraints_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_scene_spec_constraints_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "spec_id"],
                [
                    "scene_spec_versions.owner_id",
                    "scene_spec_versions.novel_id",
                    "scene_spec_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_constraints_spec_scope",
            ),
            sa.CheckConstraint(
                f"scope IN ({_SCOPES})",
                name="ck_scene_spec_constraints_scope",
            ),
            sa.CheckConstraint(
                f"source IN ({_SOURCES})",
                name="ck_scene_spec_constraints_source",
            ),
            sa.CheckConstraint(
                "spoiler_cutoff >= 1",
                name="ck_scene_spec_constraints_cutoff",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_scene_spec_constraints_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_scene_spec_constraints_idempotency_key",
            ),
        )
        op.create_index(
            "idx_scene_spec_constraints_scope",
            "scene_spec_negative_constraints",
            ["owner_id", "novel_id", "spec_id"],
        )

    # ---- scene_spec_evidence_refs -------------------------------------
    if not _has_table("scene_spec_evidence_refs"):
        op.create_table(
            "scene_spec_evidence_refs",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("spec_id", sa.Integer, nullable=False),
            sa.Column("detail_id", sa.Integer, nullable=True),
            sa.Column("constraint_id", sa.Integer, nullable=True),
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
                name="uq_scene_spec_evidence_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "spec_id",
                "evidence_key",
                name="uq_scene_spec_evidence_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_scene_spec_evidence_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "spec_id"],
                [
                    "scene_spec_versions.owner_id",
                    "scene_spec_versions.novel_id",
                    "scene_spec_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_evidence_spec_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "detail_id"],
                [
                    "scene_spec_details.owner_id",
                    "scene_spec_details.novel_id",
                    "scene_spec_details.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_evidence_detail_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "constraint_id"],
                [
                    "scene_spec_negative_constraints.owner_id",
                    "scene_spec_negative_constraints.novel_id",
                    "scene_spec_negative_constraints.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_evidence_constraint_scope",
            ),
            sa.CheckConstraint(
                "source_start >= 0 AND source_end > source_start",
                name="ck_scene_spec_evidence_offsets",
            ),
            sa.CheckConstraint(
                "chapter_number >= 1",
                name="ck_scene_spec_evidence_chapter",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_scene_spec_evidence_cutoff",
            ),
            sa.CheckConstraint(
                "chapter_number <= cutoff_chapter",
                name="ck_scene_spec_evidence_spoiler_cutoff",
            ),
            sa.CheckConstraint(
                "(detail_id IS NULL) <> (constraint_id IS NULL)",
                name="ck_scene_spec_evidence_owner",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_scene_spec_evidence_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(content_hash) = 64",
                name="ck_scene_spec_evidence_content_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_scene_spec_evidence_idempotency_key",
            ),
        )
        op.create_index(
            "idx_scene_spec_evidence_scope",
            "scene_spec_evidence_refs",
            ["owner_id", "novel_id", "spec_id"],
        )
        op.create_index(
            "idx_scene_spec_evidence_detail",
            "scene_spec_evidence_refs",
            ["detail_id"],
        )

    # ---- scene_spec_uncertainties -------------------------------------
    if not _has_table("scene_spec_uncertainties"):
        op.create_table(
            "scene_spec_uncertainties",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("spec_id", sa.Integer, nullable=False),
            sa.Column("uncertainty_key", sa.String(180), nullable=False),
            sa.Column("reason", sa.String(32), nullable=False),
            sa.Column("detail", sa.Text, nullable=False),
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
                name="uq_scene_spec_uncertainties_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "spec_id",
                "uncertainty_key",
                name="uq_scene_spec_uncertainties_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_scene_spec_uncertainties_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "spec_id"],
                [
                    "scene_spec_versions.owner_id",
                    "scene_spec_versions.novel_id",
                    "scene_spec_versions.id",
                ],
                ondelete="CASCADE",
                name="fk_scene_spec_uncertainties_spec_scope",
            ),
            sa.CheckConstraint(
                f"reason IN ({_REASONS})",
                name="ck_scene_spec_uncertainties_reason",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_scene_spec_uncertainties_idempotency_key",
            ),
        )
        op.create_index(
            "idx_scene_spec_uncertainties_scope",
            "scene_spec_uncertainties",
            ["owner_id", "novel_id", "spec_id"],
        )

    # ---- prompt_revisions ---------------------------------------------
    if not _has_table("prompt_revisions"):
        op.create_table(
            "prompt_revisions",
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
            sa.Column("prompt_key", sa.String(120), nullable=False),
            sa.Column("revision_number", sa.Integer, nullable=False),
            sa.Column(
                "parent_prompt_revision_id",
                sa.Integer,
                sa.ForeignKey("prompt_revisions.id", ondelete="SET NULL"),
            ),
            sa.Column("scene_spec_id", sa.Integer, nullable=True),
            sa.Column("scene_spec_hash", sa.String(64), nullable=False),
            sa.Column("visual_bible_revision_id", sa.Integer, nullable=True),
            sa.Column("visual_bible_revision_hash", sa.String(64), nullable=False),
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
            sa.Column("prompt_schema_hash", sa.String(64), nullable=False),
            sa.Column("compiler_version", sa.String(64), nullable=False),
            sa.Column("adapter_id", sa.String(120), nullable=False),
            sa.Column("adapter_version", sa.String(64), nullable=False),
            sa.Column("config_hash", sa.String(64), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("prompt_hash", sa.String(64), nullable=False),
            sa.Column("sections", JSONB, nullable=False),
            sa.Column("negative_constraints", JSONB, nullable=True),
            sa.Column("uncertainties", JSONB, nullable=True),
            sa.Column("prompt_text", sa.Text, nullable=False),
            sa.Column("redacted_preview", sa.Text, nullable=True),
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
                name="uq_prompt_revisions_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "prompt_key",
                name="uq_prompt_revisions_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_prompt_revisions_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "scene_spec_id"],
                [
                    "scene_spec_versions.owner_id",
                    "scene_spec_versions.novel_id",
                    "scene_spec_versions.id",
                ],
                ondelete="SET NULL",
                name="fk_prompt_revisions_spec_scope",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "visual_bible_revision_id"],
                [
                    "visual_bible_versions.owner_id",
                    "visual_bible_versions.novel_id",
                    "visual_bible_versions.id",
                ],
                ondelete="SET NULL",
                name="fk_prompt_revisions_visual_bible_scope",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_prompt_revisions_revision",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_prompt_revisions_cutoff",
            ),
            sa.CheckConstraint(
                "length(scene_spec_hash) = 64",
                name="ck_prompt_revisions_spec_hash",
            ),
            sa.CheckConstraint(
                "length(visual_bible_revision_hash) = 64",
                name="ck_prompt_revisions_vb_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_prompt_revisions_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(schema_hash) = 64",
                name="ck_prompt_revisions_schema_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_schema_hash) = 64",
                name="ck_prompt_revisions_prompt_schema_hash",
            ),
            sa.CheckConstraint(
                "length(config_hash) = 64",
                name="ck_prompt_revisions_config_hash",
            ),
            sa.CheckConstraint(
                "length(input_hash) = 64",
                name="ck_prompt_revisions_input_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_hash) = 64",
                name="ck_prompt_revisions_prompt_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_prompt_revisions_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_prompt_revisions_idempotency_key",
            ),
            sa.CheckConstraint(
                "input_hash <> prompt_hash",
                name="ck_prompt_revisions_hash_separation",
            ),
            sa.CheckConstraint(
                f"review_state IN ({_REVIEW_STATES})",
                name="ck_prompt_revisions_review_state",
            ),
        )
        op.create_index(
            "idx_prompt_revisions_scope",
            "prompt_revisions",
            ["owner_id", "novel_id", "review_state"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched by logic."""
    if _has_table("prompt_revisions"):
        op.drop_index(
            "idx_prompt_revisions_scope",
            table_name="prompt_revisions",
        )
        op.drop_table("prompt_revisions")

    if _has_table("scene_spec_uncertainties"):
        op.drop_index(
            "idx_scene_spec_uncertainties_scope",
            table_name="scene_spec_uncertainties",
        )
        op.drop_table("scene_spec_uncertainties")

    if _has_table("scene_spec_evidence_refs"):
        op.drop_index(
            "idx_scene_spec_evidence_detail",
            table_name="scene_spec_evidence_refs",
        )
        op.drop_index(
            "idx_scene_spec_evidence_scope",
            table_name="scene_spec_evidence_refs",
        )
        op.drop_table("scene_spec_evidence_refs")

    if _has_table("scene_spec_negative_constraints"):
        op.drop_index(
            "idx_scene_spec_constraints_scope",
            table_name="scene_spec_negative_constraints",
        )
        op.drop_table("scene_spec_negative_constraints")

    if _has_table("scene_spec_details"):
        op.drop_index(
            "idx_scene_spec_details_scope",
            table_name="scene_spec_details",
        )
        op.drop_table("scene_spec_details")

    if _has_table("scene_spec_versions"):
        op.drop_index(
            "idx_scene_spec_versions_scope",
            table_name="scene_spec_versions",
        )
        op.drop_table("scene_spec_versions")
