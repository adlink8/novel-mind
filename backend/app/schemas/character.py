"""角色与关系响应模型"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CharacterResponse(BaseModel):
    """角色基本信息响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    name: str
    aliases: Optional[str] = None
    role: str = "supporting"
    description: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    first_appearance_chapter: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class CharacterRelationResponse(BaseModel):
    """角色关系响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    source_character_id: int
    target_character_id: int
    relation_type: str
    description: Optional[str] = None
    strength: Optional[int] = 5
    chapter_first_seen: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class CharacterDetailResponse(CharacterResponse):
    """角色详情响应（含关系网络）"""
    relations: List[CharacterRelationResponse] = Field(default_factory=list)
    extra_data: Optional[Dict[str, Any]] = None
