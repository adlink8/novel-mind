"""
剧情分析请求/响应 Pydantic 模型

分析类型 (analysis_type):
  - plot_summary      : 全书/章节剧情摘要
  - character_analysis: 人物分析（性格、动机、成长弧线）
  - theme             : 主题分析
  - style             : 写作风格分析
  - chapter_summary   : 章节级摘要

请求流程:
  前端发送 AnalysisRequest → 后端路由到合适的 AI 模型 → 生成结果 → 存储到 analysis_results 表
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRequest(BaseModel):
    """发起分析请求"""

    novel_id: int = Field(..., description="小说 ID")
    analysis_type: str = Field(
        default="plot_summary",
        description="分析类型: plot_summary / character_analysis / theme / style / chapter_summary",
    )
    chapter_id: Optional[int] = Field(None, description="可选：仅分析指定章节")
    model_config_name: Optional[str] = Field(
        None, description="指定模型名称，为空则由 AI 路由器自动选择"
    )


class AnalysisResponse(BaseModel):
    """整本书的分析结果响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    analysis_type: str
    result_data: Dict[str, Any] = Field(default_factory=dict)  # 结构化分析结果
    model_used: Optional[str] = None  # 使用的 AI 模型
    prompt_tokens: Optional[int] = None  # 输入 token 数
    completion_tokens: Optional[int] = None  # 输出 token 数
    created_at: datetime
    updated_at: datetime


class ChapterAnalysisResponse(BaseModel):
    """章节级分析结果响应"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    chapter_id: Optional[int] = None
    analysis_type: str
    result_data: Dict[str, Any] = Field(default_factory=dict)
    model_used: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    created_at: datetime
    updated_at: datetime
