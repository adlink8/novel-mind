"""Wire contracts for bounded chapter analysis batches."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.agent_runtime.base import StrictAgentRuntimeModel


class ChapterBatchCreate(StrictAgentRuntimeModel):
    chapter_start: int | None = Field(default=None, gt=0)
    chapter_end: int | None = Field(default=None, gt=0)
    chapter_ids: list[int] | None = Field(default=None, min_length=1, max_length=5000)
    concurrency_window: int = Field(default=3, ge=1, le=32)

    @model_validator(mode="after")
    def _validate_scope(self) -> "ChapterBatchCreate":
        has_range = self.chapter_start is not None or self.chapter_end is not None
        has_ids = self.chapter_ids is not None
        if has_range == has_ids:
            raise ValueError("provide either chapter_start+chapter_end or chapter_ids")
        if has_range and (self.chapter_start is None or self.chapter_end is None):
            raise ValueError("chapter_start and chapter_end must be provided together")
        if self.chapter_ids is not None and any(
            value < 1 for value in self.chapter_ids
        ):
            raise ValueError("chapter_ids must be positive")
        return self


class ChapterBatchChapterView(StrictAgentRuntimeModel):
    chapter_id: int
    chapter_number: int
    status: Literal["pending", "queued", "running", "completed", "failed", "cancelled"]
    run_id: int | None = None


class ChapterBatchView(StrictAgentRuntimeModel):
    batch_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_id: int
    novel_id: int
    cutoff_chapter: int
    concurrency_window: int
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    total: int
    queued: int
    running: int
    completed: int
    failed: int
    cancelled: int
    pending: int
    created_run_ids: list[int] = Field(default_factory=list)
    chapters: list[ChapterBatchChapterView] = Field(default_factory=list)
