"""Illustration durable job, asset revision and budget contract tables (Phase 33-01).

REQ-VIS-04 / D-33-01..D-33-03: an image request is an idempotent durable job
whose provider output is an immutable candidate asset revision that stays
``candidate`` until explicit human approval. New append-only tables:

  - ``illustration_jobs``: durable job with explicit queued/running/paused/
    succeeded/failed/cancelled/outcome_unknown states, lease for worker claim,
    bounded retry and a unique nonterminal idempotency key (one charge per
    owner/novel/SceneSpec/prompt/model/config lineage).
  - ``illustration_budget_ledgers`` / ``illustration_budget_reservations``:
    novel-scoped worst-case reserve/settle with a price snapshot; unknown
    usage/cost stays explicit.
  - ``illustration_attempts``: auditable provider call with request/response
    hashes, provider request id, usage, cost, latency, status and error code.
  - ``asset_revisions``: immutable provider-output metadata (content bytes
    hash, MIME, dimensions, prompt/spec/model lineage, provider request id,
    provenance, rights) with a candidate-only approval projection.
  - ``illustration_consistency_reports``: versioned consistency evaluation
    evidence (evaluator/model/fixture lineage); a report is a review signal,
    never canon.
  - ``illustration_review_events``: append-only human/machine approval actions
    with an idempotent event key (candidate -> proposal_ready for Phase 34).

Create order follows FK dependencies: jobs -> budget ledgers -> budget
reservations -> attempts (references jobs + reservations) -> asset revisions
(references jobs) -> consistency reports + review events (reference assets).

Design conventions (matching 20260801_scene_spec_prompt/20260801_visual_bible):
idempotent inspector guards, symmetric downgrade, composite owner/novel scope
FKs, composite idempotency constraints and a JSONB-with-SQLite-variant for
tests. No existing cover/upload/Visual Bible rows are touched.

Revision ID: 20260801_illustration_jobs
Revises: 20260801_prompt_review_events
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_illustration_jobs"
down_revision = "20260801_prompt_review_events"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_JOB_STATUSES = (
    "'queued','running','paused_budget','paused_dependency',"
    "'succeeded','failed','cancelled','outcome_unknown'"
)
_NONTERMINAL_STATUSES = (
    "'queued','running','paused_budget','paused_dependency'"
)
_ATTEMPT_STATUSES = (
    "'started','succeeded','failed','cancelled','outcome_unknown'"
)
_RESERVATION_STATUSES = "'reserved','settled','released','failed'"
_APPROVAL_STATES = "'candidate','proposal_ready','rejected','superseded'"
_REVIEW_ACTIONS = "'approve','reject','supersede','needs_relink'"
_ACTOR_SOURCES = "'human','machine'"
_RIGHTS_STATUSES = "'unreviewed','cleared','pending','denied'"
_CONSISTENCY_VERDICTS = "'pass','concern','fail','unavailable'"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    """Upgrade schema (idempotent inspector guards)."""

    # ---- illustration_jobs ---------------------------------------------
    if not _has_table("illustration_jobs"):
        op.create_table(
            "illustration_jobs",
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
            sa.Column("job_key", sa.String(120), nullable=False),
            sa.Column("idempotency_key", sa.String(64), nullable=False),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="queued",
            ),
            sa.Column("status_reason", sa.String(160), nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
            sa.Column("lease_id", sa.String(64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "cancel_requested",
                sa.Boolean,
                nullable=False,
                server_default="false",
            ),
            sa.Column(
                "retry_count",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column("scene_spec_hash", sa.String(64), nullable=False),
            sa.Column("prompt_revision_id", sa.Integer, nullable=True),
            sa.Column("prompt_revision_hash", sa.String(64), nullable=False),
            sa.Column("visual_bible_revision_id", sa.Integer, nullable=True),
            sa.Column("visual_bible_revision_hash", sa.String(64), nullable=False),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column("cutoff_chapter", sa.Integer, nullable=False),
            sa.Column("model_lineage", JSONB, nullable=False),
            sa.Column("config_hash", sa.String(64), nullable=False),
            sa.Column("price_snapshot", JSONB, nullable=False),
            sa.Column("response_hash", sa.String(64), nullable=True),
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
                name="uq_illustration_jobs_scope",
            ),
            sa.CheckConstraint(
                f"status IN ({_JOB_STATUSES})",
                name="ck_illustration_jobs_status",
            ),
            sa.CheckConstraint(
                "retry_count >= 0",
                name="ck_illustration_jobs_retry",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_illustration_jobs_idempotency_key",
            ),
            sa.CheckConstraint(
                "length(scene_spec_hash) = 64",
                name="ck_illustration_jobs_scene_spec_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_revision_hash) = 64",
                name="ck_illustration_jobs_prompt_hash",
            ),
            sa.CheckConstraint(
                "length(visual_bible_revision_hash) = 64",
                name="ck_illustration_jobs_vb_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_illustration_jobs_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(config_hash) = 64",
                name="ck_illustration_jobs_config_hash",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_illustration_jobs_cutoff",
            ),
        )
        op.create_index(
            "idx_illustration_jobs_scope",
            "illustration_jobs",
            ["owner_id", "novel_id", "status"],
        )
        op.create_index(
            "idx_illustration_jobs_lease",
            "illustration_jobs",
            ["lease_expires_at"],
        )
        op.create_index(
            "uq_illustration_jobs_nonterminal_key",
            "illustration_jobs",
            ["idempotency_key"],
            unique=True,
            postgresql_where=sa.text(
                f"status IN ({_NONTERMINAL_STATUSES})"
            ),
        )

    # ---- illustration_budget_ledgers -----------------------------------
    if not _has_table("illustration_budget_ledgers"):
        op.create_table(
            "illustration_budget_ledgers",
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
            sa.Column("max_calls", sa.Integer, nullable=False),
            sa.Column("max_cost_usd", sa.Numeric(18, 8), nullable=False),
            sa.Column(
                "reserved_calls",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "reserved_cost_usd",
                sa.Numeric(18, 8),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "settled_calls",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "settled_cost_usd",
                sa.Numeric(18, 8),
                nullable=False,
                server_default="0",
            ),
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
                name="uq_illustration_budget_ledger_scope",
            ),
            sa.CheckConstraint(
                "max_calls >= 0",
                name="ck_illustration_budget_max_calls",
            ),
            sa.CheckConstraint(
                "max_cost_usd >= 0",
                name="ck_illustration_budget_max_cost",
            ),
        )
        op.create_index(
            "idx_illustration_budget_ledgers_scope",
            "illustration_budget_ledgers",
            ["owner_id", "novel_id"],
        )

    # ---- illustration_budget_reservations ------------------------------
    if not _has_table("illustration_budget_reservations"):
        op.create_table(
            "illustration_budget_reservations",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "ledger_id",
                sa.Integer,
                sa.ForeignKey(
                    "illustration_budget_ledgers.id", ondelete="CASCADE"
                ),
                nullable=False,
            ),
            sa.Column("reservation_key", sa.String(160), nullable=False),
            sa.Column(
                "status",
                sa.String(24),
                nullable=False,
                server_default="reserved",
            ),
            sa.Column("calls", sa.Integer, nullable=False),
            sa.Column(
                "input_tokens",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "output_tokens",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=False),
            sa.Column("price_snapshot", JSONB, nullable=False),
            sa.Column("settled_usage", JSONB, nullable=False),
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
                "ledger_id",
                "reservation_key",
                name="uq_illustration_budget_reservations_key",
            ),
            sa.CheckConstraint(
                f"status IN ({_RESERVATION_STATUSES})",
                name="ck_illustration_budget_reservations_status",
            ),
            sa.CheckConstraint(
                "calls >= 0",
                name="ck_illustration_budget_reservations_calls",
            ),
            sa.CheckConstraint(
                "cost_usd >= 0",
                name="ck_illustration_budget_reservations_cost",
            ),
        )
        op.create_index(
            "idx_illustration_budget_reservations_ledger",
            "illustration_budget_reservations",
            ["ledger_id"],
        )

    # ---- illustration_attempts -----------------------------------------
    if not _has_table("illustration_attempts"):
        op.create_table(
            "illustration_attempts",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "job_id",
                sa.Integer,
                sa.ForeignKey("illustration_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "reservation_id",
                sa.Integer,
                sa.ForeignKey(
                    "illustration_budget_reservations.id", ondelete="SET NULL"
                ),
                nullable=True,
            ),
            sa.Column("attempt_number", sa.Integer, nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("provider_request_id", sa.String(160), nullable=True),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("response_hash", sa.String(64), nullable=True),
            sa.Column("usage", JSONB, nullable=False),
            sa.Column("cost_usd", sa.Numeric(18, 8), nullable=True),
            sa.Column("latency_ms", sa.Integer, nullable=True),
            sa.Column("error_code", sa.String(80), nullable=True),
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
                "job_id",
                "attempt_number",
                name="uq_illustration_attempts_job_number",
            ),
            sa.CheckConstraint(
                f"status IN ({_ATTEMPT_STATUSES})",
                name="ck_illustration_attempts_status",
            ),
            sa.CheckConstraint(
                "attempt_number >= 1",
                name="ck_illustration_attempts_number",
            ),
            sa.CheckConstraint(
                "length(request_hash) = 64",
                name="ck_illustration_attempts_request_hash",
            ),
        )
        op.create_index(
            "idx_illustration_attempts_job",
            "illustration_attempts",
            ["job_id"],
        )

    # ---- asset_revisions -----------------------------------------------
    if not _has_table("asset_revisions"):
        op.create_table(
            "asset_revisions",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("job_id", sa.Integer, nullable=False),
            sa.Column("revision_key", sa.String(180), nullable=False),
            sa.Column("revision_number", sa.Integer, nullable=False),
            sa.Column("asset_id", sa.String(200), nullable=False),
            sa.Column("storage_key", sa.String(320), nullable=False),
            sa.Column("mime_type", sa.String(100), nullable=False),
            sa.Column("width", sa.Integer, nullable=False),
            sa.Column("height", sa.Integer, nullable=False),
            sa.Column("size_bytes", sa.Integer, nullable=False),
            sa.Column("bytes_hash", sa.String(64), nullable=False),
            sa.Column("scene_spec_hash", sa.String(64), nullable=False),
            sa.Column("prompt_revision_id", sa.Integer, nullable=True),
            sa.Column("prompt_revision_hash", sa.String(64), nullable=False),
            sa.Column("visual_bible_revision_hash", sa.String(64), nullable=False),
            sa.Column("source_snapshot_id", sa.String(160), nullable=False),
            sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
            sa.Column("cutoff_chapter", sa.Integer, nullable=False),
            sa.Column("model_lineage", JSONB, nullable=False),
            sa.Column("config_hash", sa.String(64), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("provider_model", sa.String(120), nullable=False),
            sa.Column("provider_request_id", sa.String(160), nullable=True),
            sa.Column("provider_response", JSONB, nullable=False),
            sa.Column("provenance", JSONB, nullable=False),
            sa.Column(
                "rights_status",
                sa.String(32),
                nullable=False,
                server_default="unreviewed",
            ),
            sa.Column(
                "approval_state",
                sa.String(24),
                nullable=False,
                server_default="candidate",
            ),
            sa.Column("approved_by", sa.String(200), nullable=True),
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
                name="uq_asset_revisions_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "job_id",
                "revision_key",
                name="uq_asset_revisions_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_asset_revisions_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "job_id"],
                [
                    "illustration_jobs.owner_id",
                    "illustration_jobs.novel_id",
                    "illustration_jobs.id",
                ],
                ondelete="CASCADE",
                name="fk_asset_revisions_job_scope",
            ),
            sa.CheckConstraint(
                f"approval_state IN ({_APPROVAL_STATES})",
                name="ck_asset_revisions_approval_state",
            ),
            sa.CheckConstraint(
                f"rights_status IN ({_RIGHTS_STATUSES})",
                name="ck_asset_revisions_rights_status",
            ),
            sa.CheckConstraint(
                "revision_number >= 1",
                name="ck_asset_revisions_revision",
            ),
            sa.CheckConstraint(
                "width > 0 AND height > 0",
                name="ck_asset_revisions_dimensions",
            ),
            sa.CheckConstraint(
                "size_bytes > 0",
                name="ck_asset_revisions_size",
            ),
            sa.CheckConstraint(
                "length(bytes_hash) = 64",
                name="ck_asset_revisions_bytes_hash",
            ),
            sa.CheckConstraint(
                "length(scene_spec_hash) = 64",
                name="ck_asset_revisions_scene_spec_hash",
            ),
            sa.CheckConstraint(
                "length(prompt_revision_hash) = 64",
                name="ck_asset_revisions_prompt_hash",
            ),
            sa.CheckConstraint(
                "length(visual_bible_revision_hash) = 64",
                name="ck_asset_revisions_vb_hash",
            ),
            sa.CheckConstraint(
                "length(source_snapshot_hash) = 64",
                name="ck_asset_revisions_snapshot_hash",
            ),
            sa.CheckConstraint(
                "length(config_hash) = 64",
                name="ck_asset_revisions_config_hash",
            ),
            sa.CheckConstraint(
                "cutoff_chapter >= 1",
                name="ck_asset_revisions_cutoff",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_asset_revisions_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_asset_revisions_idempotency_key",
            ),
        )
        op.create_index(
            "idx_asset_revisions_scope",
            "asset_revisions",
            ["owner_id", "novel_id", "approval_state"],
        )
        op.create_index(
            "idx_asset_revisions_job",
            "asset_revisions",
            ["job_id"],
        )
        op.create_index(
            "idx_asset_revisions_bytes_hash",
            "asset_revisions",
            ["bytes_hash"],
        )

    # ---- illustration_consistency_reports ------------------------------
    if not _has_table("illustration_consistency_reports"):
        op.create_table(
            "illustration_consistency_reports",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("asset_revision_id", sa.Integer, nullable=False),
            sa.Column("report_key", sa.String(180), nullable=False),
            sa.Column("evaluator_id", sa.String(120), nullable=False),
            sa.Column("evaluator_version", sa.String(64), nullable=False),
            sa.Column("model_lineage", JSONB, nullable=False),
            sa.Column("fixture_set_hash", sa.String(64), nullable=False),
            sa.Column("reference_asset_ids", JSONB, nullable=False),
            sa.Column("scores", JSONB, nullable=False),
            sa.Column("verdict", sa.String(24), nullable=False),
            sa.Column("details", JSONB, nullable=False),
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
                name="uq_illustration_consistency_scope",
            ),
            sa.UniqueConstraint(
                "owner_id",
                "novel_id",
                "asset_revision_id",
                "report_key",
                name="uq_illustration_consistency_key",
            ),
            sa.UniqueConstraint(
                "idempotency_key",
                name="uq_illustration_consistency_idempotency",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "asset_revision_id"],
                [
                    "asset_revisions.owner_id",
                    "asset_revisions.novel_id",
                    "asset_revisions.id",
                ],
                ondelete="CASCADE",
                name="fk_illustration_consistency_asset_scope",
            ),
            sa.CheckConstraint(
                f"verdict IN ({_CONSISTENCY_VERDICTS})",
                name="ck_illustration_consistency_verdict",
            ),
            sa.CheckConstraint(
                "length(fixture_set_hash) = 64",
                name="ck_illustration_consistency_fixture_hash",
            ),
            sa.CheckConstraint(
                "length(canonical_payload_hash) = 64",
                name="ck_illustration_consistency_payload_hash",
            ),
            sa.CheckConstraint(
                "length(idempotency_key) = 64",
                name="ck_illustration_consistency_idempotency_key",
            ),
        )
        op.create_index(
            "idx_illustration_consistency_asset",
            "illustration_consistency_reports",
            ["owner_id", "novel_id", "asset_revision_id"],
        )

    # ---- illustration_review_events ------------------------------------
    if not _has_table("illustration_review_events"):
        op.create_table(
            "illustration_review_events",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer, nullable=False),
            sa.Column("novel_id", sa.Integer, nullable=False),
            sa.Column("asset_revision_id", sa.Integer, nullable=False),
            sa.Column("action", sa.String(24), nullable=False),
            sa.Column("actor_source", sa.String(16), nullable=False),
            sa.Column("actor", sa.String(200), nullable=False),
            sa.Column("reason", sa.Text, nullable=False),
            sa.Column("event_key", sa.String(160), nullable=False),
            sa.Column("from_approval_state", sa.String(24), nullable=False),
            sa.Column("to_approval_state", sa.String(24), nullable=False),
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
                "asset_revision_id",
                "event_key",
                name="uq_illustration_review_events_key",
            ),
            sa.ForeignKeyConstraint(
                ["owner_id", "novel_id", "asset_revision_id"],
                [
                    "asset_revisions.owner_id",
                    "asset_revisions.novel_id",
                    "asset_revisions.id",
                ],
                ondelete="CASCADE",
                name="fk_illustration_review_events_asset_scope",
            ),
            sa.CheckConstraint(
                f"action IN ({_REVIEW_ACTIONS})",
                name="ck_illustration_review_events_action",
            ),
            sa.CheckConstraint(
                f"actor_source IN ({_ACTOR_SOURCES})",
                name="ck_illustration_review_events_actor_source",
            ),
            sa.CheckConstraint(
                f"from_approval_state IN ({_APPROVAL_STATES})",
                name="ck_illustration_review_events_from_state",
            ),
            sa.CheckConstraint(
                f"to_approval_state IN ({_APPROVAL_STATES})",
                name="ck_illustration_review_events_to_state",
            ),
        )
        op.create_index(
            "idx_illustration_review_events_asset",
            "illustration_review_events",
            ["owner_id", "novel_id", "asset_revision_id"],
        )


def downgrade() -> None:
    """Downgrade schema: symmetric drop; existing rows stay untouched by logic."""
    if _has_table("illustration_review_events"):
        op.drop_index(
            "idx_illustration_review_events_asset",
            table_name="illustration_review_events",
        )
        op.drop_table("illustration_review_events")

    if _has_table("illustration_consistency_reports"):
        op.drop_index(
            "idx_illustration_consistency_asset",
            table_name="illustration_consistency_reports",
        )
        op.drop_table("illustration_consistency_reports")

    if _has_table("asset_revisions"):
        op.drop_index(
            "idx_asset_revisions_bytes_hash",
            table_name="asset_revisions",
        )
        op.drop_index(
            "idx_asset_revisions_job",
            table_name="asset_revisions",
        )
        op.drop_index(
            "idx_asset_revisions_scope",
            table_name="asset_revisions",
        )
        op.drop_table("asset_revisions")

    if _has_table("illustration_attempts"):
        op.drop_index(
            "idx_illustration_attempts_job",
            table_name="illustration_attempts",
        )
        op.drop_table("illustration_attempts")

    if _has_table("illustration_budget_reservations"):
        op.drop_index(
            "idx_illustration_budget_reservations_ledger",
            table_name="illustration_budget_reservations",
        )
        op.drop_table("illustration_budget_reservations")

    if _has_table("illustration_budget_ledgers"):
        op.drop_index(
            "idx_illustration_budget_ledgers_scope",
            table_name="illustration_budget_ledgers",
        )
        op.drop_table("illustration_budget_ledgers")

    if _has_table("illustration_jobs"):
        op.drop_index(
            "uq_illustration_jobs_nonterminal_key",
            table_name="illustration_jobs",
        )
        op.drop_index(
            "idx_illustration_jobs_lease",
            table_name="illustration_jobs",
        )
        op.drop_index(
            "idx_illustration_jobs_scope",
            table_name="illustration_jobs",
        )
        op.drop_table("illustration_jobs")
