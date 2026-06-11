"""
角色与关系响应 Pydantic 模型

响应层级:
  - CharacterResponse        : 角色基本信息（列表展示）
  - CharacterRelationResponse: 关系信息（图谱边）
  - CharacterDetailResponse  : 角色详情（含关系网络，继承 CharacterResponse）
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class CharacterResponse(BaseModel):
    """角色基本信息响应（用于人物列表页）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    name: str
    aliases: Optional[str] = None  # 别名（逗号分隔字符串）
    role: str = "supporting"  # 角色类型
    description: Optional[str] = None
    personality: Optional[str] = None
    background: Optional[str] = None
    first_appearance_chapter: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class CharacterRelationResponse(BaseModel):
    """角色关系响应（用于关系图谱的边）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    source_character_id: int  # 关系起点角色 ID
    target_character_id: int  # 关系终点角色 ID
    relation_type: str  # 关系类型
    description: Optional[str] = None
    strength: Optional[int] = 5  # 关系强度 1-10
    chapter_first_seen: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class CharacterDetailResponse(CharacterResponse):
    """
    角色详情响应（继承 CharacterResponse，增加关系网络和扩展数据）。

    用于角色详情卡片/页面，展示角色的完整信息和所有关系。
    """

    relations: List[CharacterRelationResponse] = Field(
        default_factory=list
    )  # 该角色的所有关系
    extra_data: Optional[Dict[str, Any]] = None  # 扩展数据
