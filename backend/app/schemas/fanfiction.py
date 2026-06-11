"""
同人文/续写请求/响应 Pydantic 模型

续写流程:
  1. 用户通过 FanFictionCreate 创建续写项目（指定 prompt 和续写起点）
  2. AI 通过 /continue 端点流式生成内容
  3. 生成结果通过 FanFictionResponse 返回
  4. 分章节内容通过 FanFictionChapterResponse 返回
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class FanFictionCreate(BaseModel):
    """创建同人文请求"""

    novel_id: int = Field(..., description="原小说 ID")
    title: str = Field(..., min_length=1, max_length=200, description="同人文标题")
    prompt: str = Field(..., min_length=1, description="续写提示/创作要求")
    style_config: Optional[str] = Field(None, description="风格配置（JSON 字符串）")
    parent_chapter_id: Optional[int] = Field(None, description="从哪个章节开始续写")


class FanFictionResponse(BaseModel):
    """同人文响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    title: str
    prompt: str  # 用户的续写提示
    content: Optional[str] = None  # AI 生成的内容
    style_config: Optional[str] = None  # 风格配置
    word_count: int = 0
    status: str = "draft"  # draft / generating / completed / failed
    model_used: Optional[str] = None
    parent_chapter_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class FanFictionChapterResponse(BaseModel):
    """同人文章节响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fanfiction_id: int
    chapter_number: int
    title: str
    content: Optional[str] = None
    ai_generated: bool = False  # 是否为 AI 生成
    model_used: Optional[str] = None
    style_score: Optional[float] = None  # 风格一致性评分
    rag_context: Optional[Dict[str, Any]] = None  # RAG 参考段落
    word_count: int = 0
    created_at: datetime
    updated_at: datetime
