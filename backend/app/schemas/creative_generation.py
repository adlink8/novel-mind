"""Strict, provider-neutral contracts for creative generation preparation.

This module deliberately stops before model invocation.  It describes an
auditable context package that a future authorized generation gateway may
consume, while keeping original evidence and creative output in separate
knowledge spaces.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.canon_space_policy import FANFICTION_CANON, ORIGINAL_CANON


class StrictCreativeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OriginalEvidenceRef(StrictCreativeModel):
    """Leaf evidence that may support a creative draft without becoming one."""

    evidence_key: str = Field(min_length=1, max_length=160)
    space: Literal["original_canon"] = ORIGINAL_CANON
    authority: Literal["source_text"] = "source_text"
    citation_policy: Literal["original_leaf"] = "original_leaf"
    novel_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    text_chunk_id: int | None = Field(default=None, gt=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_offsets(self) -> OriginalEvidenceRef:
        if self.source_end <= self.source_start:
            raise ValueError("source_end must be greater than source_start")
        return self


class UnderstandingStateRef(StrictCreativeModel):
    """Read-only lineage reference to a candidate understanding artifact."""

    state_kind: Literal["chapter_state", "story_arc", "global_story_model"]
    version_key: str = Field(min_length=1, max_length=128)
    source_key: str = Field(min_length=1, max_length=160)
    novel_id: int = Field(gt=0)
    chapter_number: int | None = Field(default=None, ge=1)


class CreativeOverride(StrictCreativeModel):
    """Explicit deviation decision; it never edits Original Canon."""

    override_key: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    original_evidence_key: str | None = Field(default=None, max_length=160)


class CreativeContextPackage(StrictCreativeModel):
    """Candidate-only context package for a future authorized generator."""

    schema_version: Literal["creative-context.v1"] = "creative-context.v1"
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    cutoff_chapter_number: int = Field(ge=1)
    output_space: Literal["fanfiction_canon"] = FANFICTION_CANON
    user_settings: dict[str, Any] = Field(default_factory=dict)
    original_evidence: list[OriginalEvidenceRef] = Field(default_factory=list, max_length=64)
    understanding_states: list[UnderstandingStateRef] = Field(default_factory=list, max_length=32)
    override: CreativeOverride | None = None
    candidate_only: Literal[True] = True
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_scope_and_cutoff(self) -> CreativeContextPackage:
        if any(ref.novel_id != self.novel_id for ref in self.original_evidence):
            raise ValueError("all original evidence must belong to the package novel")
        if any(ref.novel_id != self.novel_id for ref in self.understanding_states):
            raise ValueError("all understanding states must belong to the package novel")
        for ref in self.understanding_states:
            if ref.state_kind == "chapter_state" and ref.chapter_number is None:
                raise ValueError("chapter_state requires chapter_number")
        if self.override and self.override.original_evidence_key:
            keys = {ref.evidence_key for ref in self.original_evidence}
            if self.override.original_evidence_key not in keys:
                raise ValueError("override evidence key is not in original_evidence")
        return self
