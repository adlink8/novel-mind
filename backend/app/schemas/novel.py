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


class ChapterSummaryResponse(BaseModel):
    """
    章节摘要响应（不含完整 content，用于章节列表）。

    安全设计: 列表接口不返回完整正文，避免大 payload。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_number: int
    title: str
    word_count: int
    summary: Optional[str] = None  # AI 生成的章节摘要（Phase 3）
    created_at: datetime
    updated_at: datetime


class ChapterResponse(BaseModel):
    """章节详情响应（含完整 content，用于阅读页）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_number: int
    title: str
    content: str  # 章节完整正文内容
    word_count: int
    summary: Optional[str] = None  # AI 生成的章节摘要（Phase 3）
    created_at: datetime
    updated_at: datetime


class NovelResponse(BaseModel):
    """
    小说详情响应（含章节摘要列表，用于小说详情页）。

    安全设计:
      - 不返回 source_path（服务端文件路径泄露风险）
      - chapters 使用 ChapterSummaryResponse（不含 content），避免大 payload
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
    reading_progress: Optional[dict] = None  # 阅读进度
    created_at: datetime
    updated_at: datetime
    chapters: List[ChapterSummaryResponse] = []  # 关联的章节摘要列表


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

    id: int  # 新创建的小说 ID
    title: str  # 小说标题（从文件名提取）
    status: str = Field(
        ..., description="处理状态: importing / chunking / embedding / ready"
    )
    message: str = Field(default="文件已上传，正在处理中...")
    chapter_count: int = 0  # 解析出的章节数
    word_count: int = 0  # 总字数


class ReadingProgressUpdate(BaseModel):
    """更新阅读进度请求"""

    chapter_id: int = Field(..., description="当前阅读到的章节ID")
    progress_percent: float = Field(
        ..., ge=0, le=100, description="阅读进度百分比 0-100"
    )


class ReadingProgressResponse(BaseModel):
    """阅读进度响应"""

    novel_id: int
    chapter_id: int
    progress_percent: float
    chapter_title: Optional[str] = None
    updated_at: Optional[datetime] = None


class ImportStatusResponse(BaseModel):
    """小说导入状态响应（用于前端轮询进度）"""

    novel_id: int
    stage: str = Field(
        ...,
        description="当前阶段: uploading / detecting / parsing / saving / ready / error",
    )
    percent: int = Field(..., ge=0, le=100, description="进度百分比 0-100")
    message: str = Field(default="", description="阶段描述信息")


class ImportJobResponse(BaseModel):
    """导入任务响应"""

    job_id: int
    novel_id: int
    status: str = Field(
        ...,
        description="任务状态: pending / uploading / detecting / parsing / chunking / embedding / ready / failed",
    )
    progress: int = Field(..., ge=0, le=100, description="进度百分比 0-100")
    message: str = Field(default="", description="状态描述信息")
    error_detail: Optional[str] = Field(None, description="错误详情")
    retry_count: int = Field(0, description="已重试次数")
    max_retries: int = Field(3, description="最大重试次数")
    created_at: datetime
    updated_at: datetime


# ─────────── RAG 检索 ───────────


class RAGSearchRequest(BaseModel):
    """RAG 语义搜索请求"""

    query: str = Field(..., min_length=1, max_length=1000, description="搜索查询文本")
    top_k: int = Field(5, ge=1, le=50, description="返回结果数量上限")
    chunk_types: list[str] | None = Field(None, description="按块类型过滤: scene/dialogue/description/narration/paragraph")


class RAGSearchResult(BaseModel):
    """单条 RAG 搜索结果"""

    chunk_id: int
    content: str
    score: float
    chapter_id: int | None = None
    chunk_index: int
    chunk_type: str


class RAGSearchResponse(BaseModel):
    """RAG 语义搜索响应"""

    results: list[RAGSearchResult]


class IndexStatusResponse(BaseModel):
    """小说索引状态响应"""

    novel_id: int
    status: str
    chunk_count: int
    embedded_count: int


# ─────────── 混合搜索 ───────────


# ─────────── 混合搜索（BM25 + 向量）───────────

class SearchRequest(BaseModel):
    """混合搜索请求（BM25 + 向量）"""

    query: str = Field(..., min_length=1, max_length=500, description="搜索查询文本")
    top_k: int = Field(default=10, ge=1, le=50, description="返回结果数量上限")


class SearchResultItem(BaseModel):
    """单条搜索结果"""

    novel_id: int
    novel_title: str | None = None
    chapter_id: int | None = None
    chapter_title: str | None = None
    chunk_id: int
    chunk_index: int
    content_snippet: str  # 高亮片段（含 <mark> 标签）
    score: float
    vector_score: float | None = None
    bm25_score: float | None = None


class SearchResponse(BaseModel):
    """混合搜索响应"""

    results: list[SearchResultItem]
    total: int
    query: str
