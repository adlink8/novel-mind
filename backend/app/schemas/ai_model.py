"""AI 模型配置请求/响应模型"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class AIModelConfigCreate(BaseModel):
    """创建 AI 模型配置"""
    name: str = Field(..., min_length=1, max_length=100, description="配置名称（唯一）")
    provider: str = Field(..., description="提供商: openai / anthropic / ollama / custom")
    model_id: str = Field(..., min_length=1, max_length=100, description="模型标识: gpt-4o, claude-3.5-sonnet 等")
    api_key: Optional[str] = Field(None, description="API Key（加密存储）")
    base_url: Optional[str] = Field(None, max_length=500, description="自定义 API 地址")
    tier: str = Field(default="balanced", description="层级: quality / balanced / budget")
    max_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    is_default: bool = False
    extra_params: Optional[Dict[str, Any]] = None


class AIModelConfigUpdate(BaseModel):
    """更新 AI 模型配置（所有字段可选）"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    provider: Optional[str] = None
    model_id: Optional[str] = Field(None, min_length=1, max_length=100)
    api_key: Optional[str] = None
    base_url: Optional[str] = Field(None, max_length=500)
    tier: Optional[str] = None
    max_tokens: Optional[int] = Field(None, ge=1)
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    extra_params: Optional[Dict[str, Any]] = None


class AIModelConfigResponse(BaseModel):
    """AI 模型配置响应"""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    model_id: str
    base_url: Optional[str] = None
    tier: str
    max_tokens: int
    temperature: float
    is_default: bool
    is_active: bool
    extra_params: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    # 注意: api_key 不在响应中暴露


class AIModelTestRequest(BaseModel):
    """模型连通性测试请求"""
    model_config_name: str = Field(..., description="要测试的模型配置名称")
    test_prompt: str = Field(default="Hello, please reply with 'OK'.", description="测试用的提示词")


class AIModelTestResponse(BaseModel):
    """模型测试结果"""
    success: bool
    model_name: str
    latency_ms: int = 0
    response_text: Optional[str] = None
    error: Optional[str] = None
