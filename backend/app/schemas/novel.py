"""小说与章节请求/响应模型"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- 小说 ----------

class NovelCreate(BaseModel):
    """创建小说（手动录入元信息）"""
    title: str = Field(..., min_length=1, max_length=200, description="小说标题")
    author: Optional[str] = Field(None, max_length=100, description="作者")
    description: Optional[str] = Field(None, description="简介")
    genre: Optional[str] = Field(None, max_length=50, description="类型/流派")
    cover_url: Optional[str] = Field(None, max_length=500, description="封面地址")


class NovelUpdate(BaseModel):
    """更新小说信息（所有字段可选）"""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    author: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    genre: Optional[str] = Field(None, max_length=50)
    cover_url: Optional[str] = Field(None, max_length=500)


class ChapterResponse(BaseModel):
    """章节响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_number: int
    title: str
    word_count: int
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class NovelResponse(BaseModel):
    """小说详情响应（含章节列表）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    genre: Optional[str] = None
    chapter_count: int = 0
    word_count: int = 0
    status: str
    source_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    chapters: List[ChapterResponse] = []


class NovelListResponse(BaseModel):
    """小说列表项（不含完整章节内容，仅摘要信息）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    genre: Optional[str] = None
    chapter_count: int = 0
    word_count: int = 0
    status: str
    created_at: datetime
    updated_at: datetime


class NovelUploadResponse(BaseModel):
    """小说上传进度响应"""
    id: int
    title: str
    status: str = Field(..., description="处理状态: importing / chunking / embedding / ready")
    message: str = Field(default="文件已上传，正在处理中...")
    chapter_count: int = 0
    word_count: int = 0
