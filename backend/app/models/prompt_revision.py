"""Prompt Revision candidate Artifact contract and persistence (Phase 32-01).

D-32-01..D-32-04: a compiled prompt is a derived, provider-neutral candidate
revision of a canonical ``SceneSpec``; it never becomes source truth. One table:

- ``prompt_revisions``: immutable versioned compiled-prompt candidate for one
  owning novel. Records the Scene Spec hash, Visual Bible revision, source
  snapshot, spoiler cutoff, schema/prompt-schema/compiler/adapter/config
  hashes, both ``input_hash`` (provider-neutral canonical inputs) and
  ``prompt_hash`` (rendered adapter output), the ordered canonical sections,
  negative constraints, unresolved uncertainties, the redacted preview and a
  candidate-only review state. A human edit creates a new revision
  (``parent_prompt_revision_id``); a candidate is never overwritten in place.

Design conventions (following ``visual_bible.py`` / ``key_scene.py``):
- No active-pointer / promotion / current-revision column (D-32-01).
- ``(owner_id, novel_id, prompt_key)`` uniqueness plus a replayable
  ``canonical_payload_hash`` enforce that a candidate cannot be silently
  overwritten and old lineage always replays.
- ``input_hash != prompt_hash``: identical text can come from different
  evidence/Visual Bible revisions, so both inputs and output are hashed.
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

PROMPT_SCHEMA_VERSION = "prompt-revision.v1"
PROMPT_REVIEW_STATES = (
    "candidate",
    "approved",
    "rejected",
    "superseded",
    "needs_relink",
)


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


class PromptRevision(TimestampMixin, Base):
    """Immutable versioned compiled-prompt candidate (D-32-01/03/04)."""

    __tablename__ = "prompt_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "id",
            name="uq_prompt_revisions_scope",
        ),
        UniqueConstraint(
            "owner_id",
            "novel_id",
            "prompt_key",
            name="uq_prompt_revisions_key",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_prompt_revisions_idempotency",
        ),
        Index(
            "idx_prompt_revisions_scope",
            "owner_id",
            "novel_id",
            "review_state",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "scene_spec_id"],
            [
                "scene_spec_versions.owner_id",
                "scene_spec_versions.novel_id",
                "scene_spec_versions.id",
            ],
            ondelete="SET NULL",
            name="fk_prompt_revisions_spec_scope",
        ),
        ForeignKeyConstraint(
            ["owner_id", "novel_id", "visual_bible_revision_id"],
            [
                "visual_bible_versions.owner_id",
                "visual_bible_versions.novel_id",
                "visual_bible_versions.id",
            ],
            ondelete="SET NULL",
            name="fk_prompt_revisions_visual_bible_scope",
        ),
        CheckConstraint(
            "revision_number >= 1",
            name="ck_prompt_revisions_revision",
        ),
        CheckConstraint(
            "cutoff_chapter >= 1",
            name="ck_prompt_revisions_cutoff",
        ),
        CheckConstraint(
            "length(scene_spec_hash) = 64",
            name="ck_prompt_revisions_spec_hash",
        ),
        CheckConstraint(
            "length(visual_bible_revision_hash) = 64",
            name="ck_prompt_revisions_vb_hash",
        ),
        CheckConstraint(
            "length(source_snapshot_hash) = 64",
            name="ck_prompt_revisions_snapshot_hash",
        ),
        CheckConstraint(
            "length(schema_hash) = 64",
            name="ck_prompt_revisions_schema_hash",
        ),
        CheckConstraint(
            "length(prompt_schema_hash) = 64",
            name="ck_prompt_revisions_prompt_schema_hash",
        ),
        CheckConstraint(
            "length(config_hash) = 64",
            name="ck_prompt_revisions_config_hash",
        ),
        CheckConstraint(
            "length(input_hash) = 64",
            name="ck_prompt_revisions_input_hash",
        ),
        CheckConstraint(
            "length(prompt_hash) = 64",
            name="ck_prompt_revisions_prompt_hash",
        ),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_prompt_revisions_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_prompt_revisions_idempotency_key",
        ),
        CheckConstraint(
            "input_hash <> prompt_hash",
            name="ck_prompt_revisions_hash_separation",
        ),
        CheckConstraint(
            f"review_state IN ({_sql_values(PROMPT_REVIEW_STATES)})",
            name="ck_prompt_revisions_review_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    prompt_key: Mapped[str] = mapped_column(String(120), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_prompt_revision_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("prompt_revisions.id", ondelete="SET NULL")
    )
    scene_spec_id: Mapped[int | None] = mapped_column(Integer)
    scene_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    visual_bible_revision_id: Mapped[int | None] = mapped_column(Integer)
    visual_bible_revision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(160), nullable=False)
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cutoff_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    review_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    schema_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=PROMPT_SCHEMA_VERSION
    )
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_id: Mapped[str] = mapped_column(String(120), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sections: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    negative_constraints: Mapped[list | None] = mapped_column(JSONB)
    uncertainties: Mapped[list | None] = mapped_column(JSONB)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    redacted_preview: Mapped[str | None] = mapped_column(Text)
    canonical_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_hash: Mapped[str] = mapped_column(String(64), nullable=False)
