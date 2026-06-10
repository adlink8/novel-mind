"""
小说与章节请求/响应 Pydantic 模型

模型用途:
  - NovelCreate        : 创建小说（手动录入元信息）
  - NovelUpdate        : 更新小说信息（所有字段可选）
  - NovelResponse      : 小说详情响应（含章节列表）
  - NovelListResponse  : 小说列表项（不含完整章节，仅摘要）
  - NovelUploadResponse: 上传结果响应（含处理状态和统计）
  - ChapterResponse    : 章节详情响应

注意:
  - 所有 Response 模型使用 from_attributes=True 支持 ORM 自动转换
  - 创建/更新模型使用 Field() 定义校验规则
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────── 小说 ───────────

class NovelCreate(BaseModel):
    """创建小说请求（手动录入元信息，非文件上传场景）"""
    title: str = Field(..., min_length=1, max_length=200, description="小说标题")
    author: Optional[str] = Field(None, max_length=100, description="作者")
    description: Optional[str] = Field(None, description="简介")
    genre: Optional[str] = Field(None, max_length=50, description="类型/流派")
    cover_url: Optional[str] = Field(None, max_length=500, description="封面地址")


class NovelUpdate(BaseModel):
    """更新小说信息请求（所有字段可选，仅更新传入的字段）"""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    author: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    genre: Optional[str] = Field(None, max_length=50)
    cover_url: Optional[str] = Field(None, max_length=500)


class ChapterResponse(BaseModel):
    """章节详情响应（不含完整 content，阅读页单独获取）"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_number: int
    title: str
    word_count: int
    summary: Optional[str] = None              # AI 生成的章节摘要（Phase 3）
    created_at: datetime
    updated_at: datetime


class NovelResponse(BaseModel):
    """小说详情响应（含完整章节列表，用于小说详情页）"""
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
    chapters: List[ChapterResponse] = []       # 关联的章节列表（通过 selectin 预加载）


class NovelListResponse(BaseModel):
    """
    小说列表项响应（用于书架页卡片展示）。

    与 NovelResponse 的区别: 不含 chapters 列表，减少数据传输量。
    """
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
    """小说上传结果响应（含处理状态和统计信息）"""
    id: int                                      # 新创建的小说 ID
    title: str                                   # 小说标题（从文件名提取）
    status: str = Field(..., description="处理状态: importing / chunking / embedding / ready")
    message: str = Field(default="文件已上传，正在处理中...")
    chapter_count: int = 0                       # 解析出的章节数
    word_count: int = 0                          # 总字数
