"""
智能体工具门面请求/响应 Pydantic 模型（25.2-02 Domain Tool Contract）。

设计约定:
  - 所有请求模型使用 StrictAgentToolModel（extra="forbid"），未知字段直接
    422，避免 agent（可能被提示注入）悄悄塞入服务端未声明的参数。
  - novel_id **不放在请求体**中，而是通过查询参数注入
    ``Depends(require_owned_novel)``，使 owner 校验与 404-hide 在路由签名上
    结构上不可避免。
  - ``full_book`` 字段被有意省略：服务端只读取持久化的每本小说开关
    （novel.reading_progress["timeline_full_book"]），绝不接受裸请求参数
    （D-07 防剧透）。
  - 响应模型：成功时返回各领域既有响应形状（NovelResponse / ChapterResponse /
    SearchResponse / TimelineEnvelope / RelationshipGraphEnvelope / 线索信封 /
    NM 信封）；失败时统一返回 AgentToolErrorEnvelope。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictAgentToolModel(BaseModel):
    """严格输入模型：未知字段一律拒绝（fail closed）。"""

    model_config = ConfigDict(extra="forbid")


# ────────────────────────── 各工具请求模型 ──────────────────────────


class GetNovelRequest(StrictAgentToolModel):
    """获取小说元信息。novel_id 由查询参数注入。"""


class GetChapterRequest(StrictAgentToolModel):
    """获取章节全文（受 per-tool 字节上限与 spoiler cutoff 约束）。"""

    chapter_id: int = Field(..., gt=0, description="章节 ID")


class SearchNovelTextRequest(StrictAgentToolModel):
    """小说内全文检索（raw chunks + NU，见 ADR-0002）。"""

    query: str = Field(..., min_length=1, max_length=500, description="检索查询文本")
    top_k: int = Field(default=10, ge=1, le=50, description="返回结果数量上限")
    mode: Literal["auto", "chunks", "units", "hybrid"] = Field(
        default="auto",
        description="检索意图；实际执行层由服务端 router 决策并诚实标注",
    )


class GetTimelineRequest(StrictAgentToolModel):
    """时间线事件（spoiler cutoff 由服务端 resolve_chapter_cutoff 决定）。"""

    ordering: Literal["narrative", "story"] = Field(
        default="narrative", description="排序方式"
    )
    person: str | None = Field(default=None, max_length=100, description="人物过滤")
    causal: bool = Field(default=False, description="是否包含因果边")
    chapter_start: int | None = Field(default=None, ge=1, description="起始章节（含）")
    chapter_end: int | None = Field(
        default=None, ge=1, description="结束章节（含）；超过服务端截止点将被拒绝"
    )


class GetRelationshipsRequest(StrictAgentToolModel):
    """人物关系图（spoiler cutoff 由服务端决定）。"""

    source: Literal["active", "running_candidate"] = Field(
        default="active", description="关系版本来源"
    )
    version_id: int | None = Field(default=None, gt=0, description="显式版本 ID")
    through_chapter: int | None = Field(
        default=None, gt=0, description="截止章节；超过服务端截止点将被拒绝"
    )
    character_id: int | None = Field(default=None, gt=0, description="人物过滤")
    relation_type: str | None = Field(default=None, max_length=32, description="关系类型过滤")
    include_provisional: bool = Field(
        default=False, description="是否包含临时共现边"
    )


class GetCluesRequest(StrictAgentToolModel):
    """线索与伏笔信封（spoiler cutoff 由服务端决定）。"""

    character_id: int | None = Field(default=None, gt=0, description="人物过滤")
    status: str | None = Field(default=None, max_length=32, description="状态过滤")


class GetNarrativeMemoryRequest(StrictAgentToolModel):
    """叙事记忆结构（候选-only，ADR-0002，响应带 release_status="candidate"）。"""

    version_id: int | None = Field(default=None, gt=0, description="显式版本 ID")
    view: Literal["versions", "tree"] = Field(
        default="versions",
        description="versions=版本列表；tree=指定版本的结构树",
    )
    through_chapter: int | None = Field(
        default=None, gt=0, description="截止章节（tree 视图可见性过滤）"
    )


# ────────────────────────── 统一错误信封 ──────────────────────────


class AgentToolErrorBody(BaseModel):
    """稳定错误码体（冻结表见 services/agent_tools/errors.py）。"""

    code: str
    message: str


class AgentToolErrorEnvelope(BaseModel):
    """工具错误统一响应形状：``{"error": {"code", "message"}}``。"""

    error: AgentToolErrorBody
