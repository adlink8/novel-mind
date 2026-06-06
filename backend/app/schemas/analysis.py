"""分析相关请求/响应模型"""

from datetime import datetime
from typing import Any, Dict, List, Optional

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
        None, description="指定模型名称，为空则自动路由"
    )


class AnalysisResponse(BaseModel):
    """整本书的分析结果"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: int
    analysis_type: str
    result_data: Dict[str, Any] = Field(default_factory=dict)
    model_used: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class ChapterAnalysisResponse(BaseModel):
    """章节级分析结果"""
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
