"""Immutable illustration asset revisions and consistency evidence (Phase 33-01, REQ-VIS-04).

D-33-03 / D-33-04: provider outputs are immutable asset revisions and
identity/style consistency is review evidence, never canon. This module is the
asset/consistency plane (the ``visual_bible.py`` / ``narrative_memory_qualification*.py``
analog):

- ``asset_revisions``: immutable binary metadata row for one generated output.
  Records the content bytes hash, MIME, dimensions, prompt/spec/model lineage,
  provider request id, provenance and rights status. Approval state defaults to
  ``candidate`` and only explicit human approval moves it to ``proposal_ready``
  (Phase 34 owns publish). No empty-success asset is possible: a revision is
  only created from a successful provider attempt with verified bytes.
- ``illustration_consistency_reports``: versioned consistency evaluation
  evidence with evaluator/model/fixture lineage. A report score is a review
  signal that requires a human decision; it never auto-approves and never
  rewrites the Visual Bible (D-33-04).

Design conventions (following ``visual_bible.py`` / ``prompt_revision.py``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key; re-append only replays the existing row.
- Content rows are append-only — SQLAlchemy events reject UPDATE/DELETE so no
  silent in-place canon promotion or provenance mutation is possible.
- No ``cover_url`` / upload path authority and no published flag: the Visual
  Bible ``Novel.cover_url`` and ``backend/storage/images`` remain unrelated
  upload/cover artifacts (D-33-03 anti-patterns).
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

# Approval vocabulary mirrors ``illustration_job.py`` so model/migration/schema
# stay byte-identical (the review event table lives on the job module).
ILLUSTRATION_APPROVAL_STATES = (
    "candidate",
    "proposal_ready",
    "rejected",
    "superseded",
)
ILLUSTRATION_RIGHTS_STATUSES = ("unreviewed", "cleared", "pending", "denied")
# D-33-04: consistency is evidence, not canon. verdict is a review signal; a
# report may be ``unavailable`` when no evaluator is configured.
ILLUSTRATION_CONSISTENCY_VERDICTS = ("pass", "concern", "fail", "unavailable")


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class AssetRevision(TimestampMixin, Base):
    """Immutable provider output candidate revision (D-33-03)."""

    __tablename__ = "asset_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_asset_revisions_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "job_id",
            "revision_key",
            name="uq_asset_revisions_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_asset_revisions_idempotency",
        ),
        Index(
            "idx_asset_revisions_scope",
            "owner_id",
            "novel_id",
            "approval_state",
        ),
        Index("idx_asset_revisions_job", "job_id"),
        Index("idx_asset_revisions_bytes_hash", "bytes_hash"),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "job_id"],
            [
                "illustration_jobs.owner_id",
                "illustration_jobs.novel_id",
                "illustration_jobs.id",
            ],
            ondelete="CASCADE",
            name="fk_asset_revisions_job_scope",
        ),
        CheckConstraint(
            f"approval_state IN ({_sql_values(ILLUSTRATION_APPROVAL_STATES)})",
            name="ck_asset_revisions_approval_state",
        ),
        CheckConstraint(
            f"rights_status IN ({_sql_values(ILLUSTRATION_RIGHTS_STATUSES)})",
            name="ck_asset_revisions_rights_status",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_asset_revisions_revision",
        ),
        CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_asset_revisions_dimensions",
        ),
        CheckConstraint(
            "size_bytes > 0",
            name="ck_asset_revisions_size",
        ),
        CheckConstraint(
            "length(bytes_hash) = 64",
            name="ck_asset_revisions_bytes_hash",
        ),
        CheckConstraint(
            "length(scene_spec_hash) = 64",
            name="ck_asset_revisions_scene_spec_hash",
        ),
        CheckConstraint(
            "length(prompt_revision_hash) = 64",
            name="ck_asset_revisions_prompt_hash",
        ),
        CheckConstraint(
            "length(visual_bible_revision_hash) = 64",
            name="ck_asset_revisions_vb_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_asset_revisions_snapshot_hash",
        ),
        CheckConstraint(
            "length(config_hash) = 64",
            name="ck_asset_revisions_config_hash",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_asset_revisions_cutoff",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_asset_revisions_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_asset_revisions_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_key: Mapped[str] = mapped_column(String(180), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Storage metadata: content-hash addressed bytes under an owner/novel-scoped
    # path; the DB row is authoritative for MIME/dimensions/rights/approval.
    asset_id: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(320), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    bytes_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Frozen source/prompt/model lineage (D-33-01/03) — same values the job was
    # created with; a revision must replay exactly from its job.
    scene_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_revision_id: Mapped[int | None] = mapped_column(Integer)
    prompt_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visual_bible_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Provider metadata: request id for reconciliation, redacted response.
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    provider_response: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # D-33-03: provenance and rights are first-class; never silently cleared.
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rights_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unreviewed", server_default="unreviewed"
    )
    approval_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="candidate",
        server_default="candidate",
    )
    approved_by: Mapped[str | None] = mapped_column(String(200))
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class ConsistencyReport(TimestampMixin, Base):
    """Versioned consistency evaluation evidence; never canon (D-33-04)."""

    __tablename__ = "illustration_consistency_reports"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_illustration_consistency_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "asset_revision_id",
            "report_key",
            name="uq_illustration_consistency_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_illustration_consistency_idempotency",
        ),
        Index(
            "idx_illustration_consistency_asset",
            "owner_id",
            "novel_id",
            "asset_revision_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "asset_revision_id"],
            [
                "asset_revisions.owner_id",
                "asset_revisions.novel_id",
                "asset_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_illustration_consistency_asset_scope",
        ),
        CheckConstraint(
            f"verdict IN ({_sql_values(ILLUSTRATION_CONSISTENCY_VERDICTS)})",
            name="ck_illustration_consistency_verdict",
        ),
        CheckConstraint(
            "length(fixture_set_hash) = 64",
            name="ck_illustration_consistency_fixture_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_illustration_consistency_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_illustration_consistency_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_revision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    report_key: Mapped[str] = mapped_column(String(180), nullable=False)
    # D-33-04: evaluator/model/fixture lineage makes the score replayable.
    evaluator_id: Mapped[str] = mapped_column(String(120), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fixture_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_asset_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    scores: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


def _reject_asset_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    """AssetRevision content is immutable; only the approval projection moves.

    ``approval_state`` / ``approved_by`` mirror the PromptRevision
    ``review_state`` projection pattern: explicit append-only review events are
    the audit history, and the asset row's approval state is the single mutable
    surface (D-33-03). Everything else (bytes, lineage, provenance, rights)
    fails closed on any in-place change.
    """
    from sqlalchemy import inspect as _sa_inspect

    state = _sa_inspect(target)
    changed = {
        attr.key
        for attr in state.attrs
        if attr.key not in {"created_at", "updated_at"} and attr.history.has_changes()
    }
    allowed = {"approval_state", "approved_by"}
    forbidden = changed - allowed
    if forbidden:
        raise ValueError(
            "AssetRevision content is immutable; unexpected changes: "
            f"{sorted(forbidden)}"
        )


# Content rows are append-only: no in-place edit of an asset revision or a
# consistency report and no delete; approval state is the only mutable
# projection on an asset revision (mirrors PromptRevision.review_state).
event.listen(AssetRevision, "before_update", _reject_asset_content_mutation)
event.listen(AssetRevision, "before_delete", _reject_content_mutation)
event.listen(ConsistencyReport, "before_update", _reject_content_mutation)
event.listen(ConsistencyReport, "before_delete", _reject_content_mutation)
