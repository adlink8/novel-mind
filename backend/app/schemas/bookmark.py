"""选区书签 API 契约。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BookmarkCreate(BaseModel):
    chapter_id: int = Field(..., gt=0)
    source_start: int = Field(..., ge=0)
    source_end: int = Field(..., gt=0)
    selection_text: str = Field(..., min_length=1, max_length=20_000)
    selection_text_hash: str = Field(..., min_length=64, max_length=64)
    chapter_content_hash: str = Field(..., min_length=64, max_length=64)


class BookmarkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    chapter_id: int
    source_start: int
    source_end: int
    selected_text: str
    selection_text_hash: str
    chapter_content_hash: str
    created_at: datetime
    updated_at: datetime
