"""
AI 模型配置请求/响应 Pydantic 模型

安全注意:
  AIModelConfigResponse 不暴露 api_key 字段（前端只能看到连接是否成功，不能读取密钥）。
  创建/更新时 api_key 通过请求体传入，后端存储到数据库。
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class AIModelConfigCreate(BaseModel):
    """添加 AI 模型配置请求"""
    name: str = Field(..., min_length=1, max_length=100, description="配置名称（唯一）")
    provider: str = Field(..., description="提供商: openai / anthropic / ollama / custom")
    model_id: str = Field(..., min_length=1, max_length=100, description="模型标识: gpt-4o, claude-3.5-sonnet 等")
    api_key: Optional[str] = Field(None, description="API Key（加密存储）")
    base_url: Optional[str] = Field(None, max_length=500, description="自定义 API 地址")
    tier: str = Field(default="balanced", description="层级: quality / balanced / budget")
    max_tokens: int = Field(default=4096, ge=1)       # 最大输出 token 数
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)  # 生成温度
    is_default: bool = False                           # 是否设为默认模型
    extra_params: Optional[Dict[str, Any]] = None      # LiteLLM 额外参数


class AIModelConfigUpdate(BaseModel):
    """更新 AI 模型配置请求（所有字段可选）"""
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
    """
    AI 模型配置响应（不含 api_key）。

    安全设计: api_key 永远不会出现在 API 响应中，
    前端只能通过"测试连接"功能间接验证密钥是否有效。
    """
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
    # 注意: api_key 故意不在响应中暴露


class AIModelTestRequest(BaseModel):
    """模型连通性测试请求"""
    model_config_name: str = Field(..., description="要测试的模型配置名称")
    test_prompt: str = Field(default="Hello, please reply with 'OK'.", description="测试用的提示词")


class AIModelTestResponse(BaseModel):
    """模型测试结果"""
    success: bool                                  # 测试是否成功
    model_name: str                                # 被测试的模型名称
    latency_ms: int = 0                            # 响应延迟（毫秒）
    response_text: Optional[str] = None            # 模型回复内容（截取前 200 字符）
    error: Optional[str] = None                    # 失败时的错误信息
