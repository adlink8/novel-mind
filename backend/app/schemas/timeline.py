"""
时间线事件请求/响应 Pydantic 模型

事件类型:
  - plot     : 剧情推进事件
  - character: 角色相关事件
  - world    : 世界观事件
  - conflict : 冲突事件
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TimelineEventCreate(BaseModel):
    """创建时间线事件请求"""

    novel_id: int
    chapter_id: Optional[int] = None  # 关联章节（可选）
    event_title: str = Field(..., min_length=1, max_length=200, description="事件标题")
    event_description: Optional[str] = Field(None, description="事件描述")
    event_type: str = Field(
        default="plot", description="事件类型: plot / character / world / conflict"
    )
    sort_order: float = Field(default=0.0, description="排序权重（越小越靠前）")
    characters_involved: Optional[str] = Field(None, description="涉及角色（逗号分隔）")
    location: Optional[str] = Field(None, max_length=200, description="地点")
    time_reference: Optional[str] = Field(None, max_length=200, description="时间参考")


class TimelineEventUpdate(BaseModel):
    """更新时间线事件请求（所有字段可选）"""

    event_title: Optional[str] = Field(None, min_length=1, max_length=200)
    event_description: Optional[str] = None
    event_type: Optional[str] = None
    sort_order: Optional[float] = None
    characters_involved: Optional[str] = None
    location: Optional[str] = Field(None, max_length=200)
    time_reference: Optional[str] = Field(None, max_length=200)
    chapter_id: Optional[int] = None


class TimelineEventResponse(BaseModel):
    """时间线事件响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_id: Optional[int] = None
    event_title: str
    event_description: Optional[str] = None
    event_type: str
    sort_order: float
    characters_involved: Optional[str] = None
    location: Optional[str] = None
    time_reference: Optional[str] = None
    created_at: datetime
    updated_at: datetime
