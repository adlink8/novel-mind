"""Reader bookmark 请求/响应契约。

基于 master 的 ReaderBookmark 模型（表 reader_bookmarks）：
owner-scoped 章节书签，位置以章内百分比表示。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BookmarkCreate(BaseModel):
    """创建书签请求"""

    chapter_id: int = Field(..., description="书签所在章节 ID")
    position_percent: float = Field(
        ..., ge=0, le=100, description="章内阅读位置百分比 0-100"
    )
    label: Optional[str] = Field(
        None, max_length=200, description="书签标签（短标题）"
    )
    note: Optional[str] = Field(None, description="书签备注")


class BookmarkResponse(BaseModel):
    """书签响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    novel_id: int
    chapter_id: int
    position_percent: float
    label: Optional[str] = None
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime
