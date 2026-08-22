"""Key Scene candidate Artifact contract and persistence boundaries (Phase 31-01).

REQ-VIS-02 / D-31-01..D-31-05: scene candidates are evidence-first, derived
artifacts only. Selection never changes source text, canon or active reader
state. Tables:

- ``key_scene_sets``: immutable versioned candidate-set manifest for one owning
  novel. Records source snapshot, detector/policy lineage, spoiler cutoff,
  candidate-only review state and the approved Visual Bible revision the set
  was frozen against (owner/version/approval/hash verified before freeze).
- ``key_scene_candidates``: one ranked candidate. Carries chapter/range/source
  hash, narrative coordinates (cast/place/time/POV), spoiler cutoff, salience
  reasons, diversity key, score breakdown and detector lineage. A candidate is
  never canon: no promotion field exists and content rows are append-only.
- ``key_scene_evidence_ranges``: source-linked evidence ranges with source
  snapshot, chapter/range, offsets, content hash and a DB-level
  ``chapter_number <= cutoff_chapter`` spoiler gate. Evidence is the only
  citation authority a candidate carries.
- ``key_scene_review_decisions``: append-only human/machine review decisions
  (approve/reject/needs_relink/supersede) with idempotent decision keys and an
  optional per-candidate ``candidate_key`` so rejected candidates stay auditable.

REQ-VIS-06 / D-31-05: a candidate may carry a typed ``SpeakerDialogueHeuristicSignal``
metadata payload (``speaker_offsets`` / ``dialogue_offsets`` / ``confidence`` /
``warnings``, or an explicit ``unavailable`` state). That payload is diagnostic
candidate metadata for recall/ranking only; it is stored on the candidate row
and never populates ``key_scene_evidence_ranges``, never becomes a Canon/citation
authority and never justifies approval/publish.

Design conventions (following ``visual_bible.py`` / ``knowledge_unit.py``):
- Every row stores its canonical payload JSON, a SHA-256 canonical payload hash
  and a unique idempotency key; re-append only replays the existing row.
- ``projection_hash`` is shared by every row of one immutable set; replay
  recomputes it and fails closed on drift.
- No active-pointer / promotion / current-revision column (D-31-01) and no
  mutation path for content rows; review state is an append-only projection.
- Content rows (candidate/evidence range/review decision) are append-only —
  SQLAlchemy events reject UPDATE/DELETE so no silent in-place canon promotion
  or silent score/source mutation is possible. The set row keeps only its
  review-state projection mutable (approval is an explicit append-only action).
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Float,
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

KEY_SCENE_SCHEMA_VERSION = "key-scene.v1"
KEY_SCENE_REVIEW_ACTIONS = ("approve", "reject", "needs_relink", "supersede")
KEY_SCENE_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)
KEY_SCENE_ACTOR_SOURCES = ("human", "machine")
KEY_SCENE_SIGNAL_AVAILABILITIES = ("available", "ambiguous", "unavailable")
# Closed salience/diversity/boundary reason-code vocabulary. Scoring weights
# live in the detector policy (Phase 31-02); the vocabulary is part of the
# candidate contract so reasons stay inspectable and deterministic.
KEY_SCENE_REASON_CODES = (
    "plot_turn",
    "emotional_peak",
    "character_salience",
    "visual_expressiveness",
    "arc_impact",
    "quiet_emotional",
    "dialogue_turn",
    "repetition_penalty",
    "diversity_quota",
    "ambiguity_warning",
    "detector_fallback",
    "evidence_boundary",
    "no_scene_boundaries",
    "malformed_range",
    "beyond_cutoff",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class SceneCandidateSet(TimestampMixin, Base):
    """Immutable versioned candidate-set manifest for one owning novel (D-31-01/02)."""

    __tablename__ = "key_scene_sets"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_key_scene_sets_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "version_key",
            name="uq_key_scene_sets_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_key_scene_sets_idempotency",
        ),
        Index(
            "idx_key_scene_sets_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "approved_visual_bible_revision_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="SET NULL",
            name="fk_key_scene_sets_visual_bible_approval",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_key_scene_sets_revision",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_key_scene_sets_cutoff",
        ),
        CheckConstraint(
            "length(manifest_hash) = 64",
            name="ck_key_scene_sets_manifest_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_key_scene_sets_schema_hash",
        ),
        CheckConstraint(
            "length(policy_hash) = 64",
            name="ck_key_scene_sets_policy_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_key_scene_sets_snapshot_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_key_scene_sets_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_key_scene_sets_idempotency_key",
        ),
        CheckConstraint(
            (
                "(approved_visual_bible_revision_id IS NULL AND "
                "approved_visual_bible_revision_hash IS NULL) OR "
                "(approved_visual_bible_revision_id IS NOT NULL AND "
                "length(approved_visual_bible_revision_hash) = 64)"
            ),
            name="ck_key_scene_sets_visual_bible_approval",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(KEY_SCENE_REVIEW_STATES)})",
            name="ck_key_scene_sets_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_set_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("key_scene_sets.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=KEY_SCENE_SCHEMA_VERSION
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_id: Mapped[str] = mapped_column(String(120), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Approved Visual Bible revision the set was frozen against (D-31-01/02):
    # the set verifies owner/novel/approved status and manifest hash before freeze.
    approved_visual_bible_revision_id: Mapped[int | None] = mapped_column(Integer)
    approved_visual_bible_revision_hash: Mapped[str | None] = mapped_column(String(64))
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class SceneCandidate(TimestampMixin, Base):
    """One evidence-first candidate row; never canon, content is append-only."""

    __tablename__ = "key_scene_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_key_scene_candidates_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "set_id",
            "candidate_key",
            name="uq_key_scene_candidates_key",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "set_id",
            "candidate_order",
            name="uq_key_scene_candidates_order",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "set_id",
            "id",
            name="uq_key_scene_candidates_set_scope",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_key_scene_candidates_idempotency",
        ),
        Index(
            "idx_key_scene_candidates_scope",
            "owner_id",
            "novel_id",
            "set_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "set_id"],
            [
                "key_scene_sets.owner_id",
                "key_scene_sets.novel_id",
                "key_scene_sets.id",
            ],
            ondelete="CASCADE",
            name="fk_key_scene_candidates_set_scope",
        ),
        CheckConstraint(
            "candidate_order >= 0",
            name="ck_key_scene_candidates_order",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_key_scene_candidates_chapter",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_key_scene_candidates_offsets",
        ),
        CheckConstraint(
            "chapter_number <= spoiler_cutoff",
            name="ck_key_scene_candidates_spoiler_cutoff",
        ),
        CheckConstraint(
            "spoiler_cutoff >= 1",
            name="ck_key_scene_candidates_cutoff",
        ),
        CheckConstraint(
            "score_total >= 0",
            name="ck_key_scene_candidates_score",
        ),
        CheckConstraint(
            "length(source_hash) = 64",
            name="ck_key_scene_candidates_source_hash",
        ),
        CheckConstraint(
            "length(policy_hash) = 64",
            name="ck_key_scene_candidates_policy_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_key_scene_candidates_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_key_scene_candidates_idempotency_key",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(KEY_SCENE_REVIEW_STATES)})",
            name="ck_key_scene_candidates_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    set_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(180), nullable=False)
    candidate_order: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Narrative coordinates {cast: [...], place: ..., time: ..., pov: ...};
    # unsupported coordinate keys are rejected by the strict schema contract.
    coordinates: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    spoiler_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    salience_reasons: Mapped[list | None] = mapped_column(JSONB)
    score_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB)
    diversity_key: Mapped[str] = mapped_column(String(200), nullable=False)
    detector_id: Mapped[str] = mapped_column(String(120), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    # REQ-VIS-06 diagnostic metadata only (offsets/confidence/warnings); never
    # evidence/citation/approval authority.
    heuristic_signal: Mapped[dict | None] = mapped_column(JSONB)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)


class SceneEvidenceRange(TimestampMixin, Base):
    """Source-linked evidence range; the only citation authority (D-31-02/05)."""

    __tablename__ = "key_scene_evidence_ranges"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_key_scene_evidence_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "set_id",
            "candidate_id",
            "evidence_key",
            name="uq_key_scene_evidence_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_key_scene_evidence_idempotency",
        ),
        Index(
            "idx_key_scene_evidence_scope",
            "owner_id",
            "novel_id",
            "set_id",
        ),
        Index(
            "idx_key_scene_evidence_candidate",
            "candidate_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "set_id"],
            [
                "key_scene_sets.owner_id",
                "key_scene_sets.novel_id",
                "key_scene_sets.id",
            ],
            ondelete="CASCADE",
            name="fk_key_scene_evidence_set_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "candidate_id"],
            [
                "key_scene_candidates.owner_id",
                "key_scene_candidates.novel_id",
                "key_scene_candidates.id",
            ],
            ondelete="CASCADE",
            name="fk_key_scene_evidence_candidate_scope",
        ),
        CheckConstraint(
            "source_start >= 0 AND source_end > source_start",
            name="ck_key_scene_evidence_offsets",
        ),
        CheckConstraint(
            "chapter_number >= 1",
            name="ck_key_scene_evidence_chapter",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_key_scene_evidence_cutoff",
        ),
        CheckConstraint(
            "chapter_number <= cutoff_chapter",
            name="ck_key_scene_evidence_spoiler_cutoff",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_key_scene_evidence_snapshot_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_key_scene_evidence_content_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_key_scene_evidence_idempotency_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    set_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
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


class SceneReviewDecision(TimestampMixin, Base):
    """Append-only human/machine review decision with idempotent key (D-31-04)."""

    __tablename__ = "key_scene_review_decisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "set_id",
            "decision_key",
            name="uq_key_scene_review_decisions_key",
        ),
        Index(
            "idx_key_scene_review_decisions_set",
            "owner_id",
            "novel_id",
            "set_id",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "set_id"],
            [
                "key_scene_sets.owner_id",
                "key_scene_sets.novel_id",
                "key_scene_sets.id",
            ],
            ondelete="CASCADE",
            name="fk_key_scene_review_decisions_set_scope",
        ),
        CheckConstraint(
            f"action IN ({_sql_values(KEY_SCENE_REVIEW_ACTIONS)})",
            name="ck_key_scene_review_decisions_action",
        ),
        CheckConstraint(
            f"actor_source IN ({_sql_values(KEY_SCENE_ACTOR_SOURCES)})",
            name="ck_key_scene_review_decisions_actor_source",
        ),
        CheckConstraint(
            f"from_review_state IN ({_sql_values(KEY_SCENE_REVIEW_STATES)})",
            name="ck_key_scene_review_decisions_from_state",
        ),
        CheckConstraint(
            f"to_review_state IN ({_sql_values(KEY_SCENE_REVIEW_STATES)})",
            name="ck_key_scene_review_decisions_to_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    novel_id: Mapped[int] = mapped_column(Integer, nullable=False)
    set_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_key: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    from_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_review_state: Mapped[str] = mapped_column(String(16), nullable=False)
    # Optional: the decision may target a single candidate of the set; rejected
    # candidates stay auditable without mutating the append-only candidate row.
    candidate_key: Mapped[str | None] = mapped_column(String(180))
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


def _reject_content_mutation(
    _mapper: object, _connection: object, target: object
) -> None:
    raise ValueError(f"{type(target).__name__} records are immutable (append-only)")


# Content rows are append-only: no in-place edit of a candidate, its evidence
# ranges or a review decision. The set row keeps only its review-state
# projection mutable (explicit append-only approval, D-31-04).
event.listen(SceneCandidate, "before_update", _reject_content_mutation)
event.listen(SceneCandidate, "before_delete", _reject_content_mutation)
event.listen(SceneEvidenceRange, "before_update", _reject_content_mutation)
event.listen(SceneEvidenceRange, "before_delete", _reject_content_mutation)
event.listen(SceneReviewDecision, "before_update", _reject_content_mutation)
event.listen(SceneReviewDecision, "before_delete", _reject_content_mutation)
