"""
设置中心请求/响应 Pydantic 模型

路由偏好 (routing preference):
  - quality  : 质量优先（深度分析、续写创作）
  - balanced : 均衡（默认值）
  - budget   : 经济（本地/低价模型优先）
"""

from typing import Literal

from pydantic import BaseModel, Field

RoutingPreference = Literal["quality", "balanced", "budget"]


class RoutingPreferenceUpdate(BaseModel):
    """更新路由偏好请求（非法值返回 422）"""

    preference: RoutingPreference = Field(
        ..., description="路由偏好: quality / balanced / budget"
    )


class RoutingPreferenceResponse(BaseModel):
    """路由偏好响应"""

    preference: str
