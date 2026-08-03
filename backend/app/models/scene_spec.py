"""Scene Spec candidate Artifact contract and persistence (Phase 32-01, REQ-VIS-03).

D-32-01..D-32-04: ``SceneSpec`` is the canonical candidate Artifact; provider
prompts are derived revisions and never become source truth. Tables:

- ``scene_spec_versions``: immutable versioned candidate spec manifest for one
  owning novel. Records the source SceneCandidate hash, the approved Visual
  Bible revision it was compiled against, source snapshot, spoiler cutoff,
  compiler/policy/config lineage, candidate-only review state and a replayable
  canonical content hash.
- ``scene_spec_details``: one canonical detail row (subject/action/setting/
  composition/style/continuity). Every detail carries an authority source
  (evidence / visual_bible / user_interpretation); content rows are append-only.
- ``scene_spec_negative_constraints``: forbidden costume/era/identity/style/
  physical/continuity exclusions, also source-bounded.
- ``scene_spec_evidence_refs``: source-linked evidence with source snapshot,
  chapter/range, offsets, content hash and a DB-level ``chapter_number <=
  cutoff_chapter`` spoiler gate. Evidence is the only citation authority.
- ``scene_spec_uncertainties``: explicit unresolved items (missing evidence,
  conflicting claim, future spoiler, ambiguous reference); they are never canon.

Design conventions (following ``visual_bible.py`` / ``key_scene.py``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key; re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable version; replay
  recomputes it and fails closed on drift.
- No active-pointer / promotion / current-revision column (D-32-01) and no
  mutation path for content rows; review state is an append-only projection.
- Content rows (detail/constraint/evidence/uncertainty) are append-only —
  SQLAlchemy events reject UPDATE/DELETE so no silent in-place canon promotion
  or evidence mutation is possible. The version row keeps only its review-state
  projection mutable.
"""

from __future__ import annotations

from sqlalchemy import (
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

SCENE_SPEC_SCHEMA_VERSION = "scene-spec.v1"
SPEC_DETAIL_KINDS = (
    "subject",
    "action",
    "setting",
    "composition",
    "style",
    "continuity",
)
SPEC_SOURCES = ("evidence", "visual_bible", "user_interpretation")
SPEC_CONSTRAINT_SCOPES = (
    "costume",
    "era",
    "identity",
    "style",
    "physical",
    "continuity",
)
SPEC_UNCERTAINTY_REASONS = (
    "missing_evidence",
    "conflicting_claim",
    "future_spoiler",
    "ambiguous_reference",
)
SPEC_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class SceneSpecVersion(TimestampMixin, Base):
    """Immutable versioned candidate spec manifest for one owning novel (D-32-01)."""

    __tablename__ = "scene_spec_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_scene_spec_versions_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "spec_key",
            name="uq_scene_spec_versions_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_spec_versions_idempotency",
        ),
        Index(
            "idx_scene_spec_versions_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "scene_candidate_id"],
            [
                "key_scene_candidates.owner_id",
                "key_scene_candidates.novel_id",
                "key_scene_candidates.id",
            ],
            ondelete="SET NULL",
            name="fk_scene_spec_versions_candidate_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "visual_bible_revision_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="SET NULL",
            name="fk_scene_spec_versions_visual_bible_scope",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_scene_spec_versions_revision",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_scene_spec_versions_cutoff",
        ),
        CheckConstraint(
            "length(scene_candidate_hash) = 64",
            name="ck_scene_spec_versions_candidate_hash",
        ),
        CheckConstraint(
            "length(visual_bible_revision_hash) = 64",
            name="ck_scene_spec_versions_vb_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_scene_spec_versions_snapshot_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_scene_spec_versions_schema_hash",
        ),
        CheckConstraint(
            "length(policy_hash) = 64",
            name="ck_scene_spec_versions_policy_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_scene_spec_versions_content_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_scene_spec_versions_idempotency_key",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(SPEC_REVIEW_STATES)})",
            name="ck_scene_spec_versions_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    spec_key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Source SceneCandidate lineage (D-32-03): content hash is the replay key.
    scene_candidate_id: Mapped[int | None] = mapped_column(Integer)
    scene_candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Approved Visual Bible revision the spec was compiled against.
    visual_bible_revision_id: Mapped[int | None] = mapped_column(Integer)
    visual_bible_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=SCENE_SPEC_SCHEMA_VERSION
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiler_id: Mapped[str] = mapped_column(String(120), nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SceneSpecDetail(TimestampMixin, Base):
    """One canonical detail row; source-bounded and append-only (D-32-02)."""

    __tablename__ = "scene_spec_details"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_scene_spec_details_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "spec_id",
            "detail_key",
            name="uq_scene_spec_details_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_spec_details_idempotency",
        ),
        Index(
            "idx_scene_spec_details_scope",
            "owner_id",
            "novel_id",
            "spec_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "spec_id"],
            [
                "scene_spec_versions.owner_id",
                "scene_spec_versions.novel_id",
                "scene_spec_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_details_spec_scope",
        ),
        CheckConstraint(
            f"kind IN ({_sql_values(SPEC_DETAIL_KINDS)})",
            name="ck_scene_spec_details_kind",
        ),
        CheckConstraint(
            f"source IN ({_sql_values(SPEC_SOURCES)})",
            name="ck_scene_spec_details_source",
        ),
        CheckConstraint(
            "spoiler_cutoff >= 1",
            name="ck_scene_spec_details_cutoff",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_scene_spec_details_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_scene_spec_details_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_id: Mapped[int] = mapped_column(Integer, nullable=False)
    detail_key: Mapped[str] = mapped_column(String(180), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str | None] = mapped_column(Text)
    spoiler_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class SceneSpecNegativeConstraint(TimestampMixin, Base):
    """Forbidden detail row (costume/era/identity/style/physical/continuity)."""

    __tablename__ = "scene_spec_negative_constraints"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_scene_spec_constraints_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "spec_id",
            "constraint_key",
            name="uq_scene_spec_constraints_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_spec_constraints_idempotency",
        ),
        Index(
            "idx_scene_spec_constraints_scope",
            "owner_id",
            "novel_id",
            "spec_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "spec_id"],
            [
                "scene_spec_versions.owner_id",
                "scene_spec_versions.novel_id",
                "scene_spec_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_constraints_spec_scope",
        ),
        CheckConstraint(
            f"scope IN ({_sql_values(SPEC_CONSTRAINT_SCOPES)})",
            name="ck_scene_spec_constraints_scope",
        ),
        CheckConstraint(
            f"source IN ({_sql_values(SPEC_SOURCES)})",
            name="ck_scene_spec_constraints_source",
        ),
        CheckConstraint(
            "spoiler_cutoff >= 1",
            name="ck_scene_spec_constraints_cutoff",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_scene_spec_constraints_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_scene_spec_constraints_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_id: Mapped[int] = mapped_column(Integer, nullable=False)
    constraint_key: Mapped[str] = mapped_column(String(180), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(200))
    rationale: Mapped[str | None] = mapped_column(Text)
    spoiler_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class SceneSpecEvidenceRef(TimestampMixin, Base):
    """Source-linked evidence for a detail or constraint (D-32-02 spoiler gate)."""

    __tablename__ = "scene_spec_evidence_refs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_scene_spec_evidence_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "spec_id",
            "evidence_key",
            name="uq_scene_spec_evidence_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_spec_evidence_idempotency",
        ),
        Index(
            "idx_scene_spec_evidence_scope",
            "owner_id",
            "novel_id",
            "spec_id",
        ),
        Index(
            "idx_scene_spec_evidence_detail",
            "detail_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "spec_id"],
            [
                "scene_spec_versions.owner_id",
                "scene_spec_versions.novel_id",
                "scene_spec_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_evidence_spec_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "detail_id"],
            [
                "scene_spec_details.owner_id",
                "scene_spec_details.novel_id",
                "scene_spec_details.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_evidence_detail_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "constraint_id"],
            [
                "scene_spec_negative_constraints.owner_id",
                "scene_spec_negative_constraints.novel_id",
                "scene_spec_negative_constraints.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_evidence_constraint_scope",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_scene_spec_evidence_offsets",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_scene_spec_evidence_chapter",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_scene_spec_evidence_cutoff",
        ),
        CheckConstraint(
            "chapter_number <= cutoff_chapter",
            name="ck_scene_spec_evidence_spoiler_cutoff",
        ),
        CheckConstraint(
            "(detail_id IS NULL) <> (constraint_id IS NULL)",
            name="ck_scene_spec_evidence_owner",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_scene_spec_evidence_snapshot_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_scene_spec_evidence_content_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_scene_spec_evidence_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_id: Mapped[int] = mapped_column(Integer, nullable=False)
    detail_id: Mapped[int | None] = mapped_column(Integer)
    constraint_id: Mapped[int | None] = mapped_column(Integer)
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


class SceneSpecUncertainty(TimestampMixin, Base):
    """Explicit unresolved item; never canon (D-32-02)."""

    __tablename__ = "scene_spec_uncertainties"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_scene_spec_uncertainties_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "spec_id",
            "uncertainty_key",
            name="uq_scene_spec_uncertainties_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scene_spec_uncertainties_idempotency",
        ),
        Index(
            "idx_scene_spec_uncertainties_scope",
            "owner_id",
            "novel_id",
            "spec_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "spec_id"],
            [
                "scene_spec_versions.owner_id",
                "scene_spec_versions.novel_id",
                "scene_spec_versions.id",
            ],
            ondelete="CASCADE",
            name="fk_scene_spec_uncertainties_spec_scope",
        ),
        CheckConstraint(
            f"reason IN ({_sql_values(SPEC_UNCERTAINTY_REASONS)})",
            name="ck_scene_spec_uncertainties_reason",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_scene_spec_uncertainties_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_id: Mapped[int] = mapped_column(Integer, nullable=False)
    uncertainty_key: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Content rows are append-only: no in-place edit of a detail, constraint,
# evidence ref or uncertainty. The version row keeps only its review-state
# projection mutable (explicit append-only approval).
event.listen(SceneSpecDetail, "before_update", _reject_content_mutation)
event.listen(SceneSpecDetail, "before_delete", _reject_content_mutation)
event.listen(SceneSpecNegativeConstraint, "before_update", _reject_content_mutation)
event.listen(SceneSpecNegativeConstraint, "before_delete", _reject_content_mutation)
event.listen(SceneSpecEvidenceRef, "before_update", _reject_content_mutation)
event.listen(SceneSpecEvidenceRef, "before_delete", _reject_content_mutation)
event.listen(SceneSpecUncertainty, "before_update", _reject_content_mutation)
event.listen(SceneSpecUncertainty, "before_delete", _reject_content_mutation)
