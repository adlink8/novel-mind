"""
设置中心请求/响应 Pydantic 模型

路由偏好 (routing preference):
  - quality  : 质量优先（深度分析、续写创作）
  - balanced : 均衡（默认值）
  - budget   : 经济（本地/低价模型优先）
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

RoutingPreference = Literal["quality", "balanced", "budget"]


class RoutingPreferenceUpdate(BaseModel):
    """更新路由偏好请求（非法值返回 422）"""

    preference: RoutingPreference = Field(
        ..., description="路由偏好: quality / balanced / budget"
    )


class RoutingPreferenceResponse(BaseModel):
    """路由偏好响应"""

    preference: str


class AIBudgetLimits(BaseModel):
    """一个作用域的 AI 调用上限。"""

    max_calls: int = Field(..., gt=0, le=1_000_000)
    max_input_tokens: int = Field(..., gt=0, le=2_000_000_000)
    max_output_tokens: int = Field(..., gt=0, le=2_000_000_000)
    max_cost_usd: float = Field(..., gt=0, le=1_000_000)


class AIBudgetResponse(BaseModel):
    """当前默认值或指定小说/会话作用域的预算配置。"""

    conversation: AIBudgetLimits
    novel: AIBudgetLimits
    arc_window_size: int = Field(..., ge=1, le=5)
    scope: Literal["defaults", "novel", "conversation"]
    novel_id: int | None = None
    conversation_id: int | None = None


class AIBudgetUpdate(BaseModel):
    """更新默认值、单本小说或单次会话预算。"""

    novel_id: int | None = Field(default=None, gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    conversation: AIBudgetLimits | None = None
    novel: AIBudgetLimits | None = None
    arc_window_size: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def validate_scope(self) -> "AIBudgetUpdate":
        if self.novel_id is not None and self.conversation_id is not None:
            raise ValueError("novel_id 和 conversation_id 不能同时设置")
        if (
            self.novel_id is None
            and self.conversation_id is None
            and self.conversation is None
            and self.novel is None
            and self.arc_window_size is None
        ):
            raise ValueError("至少提供一个要更新的设置")
        if self.conversation_id is not None and self.conversation is None:
            raise ValueError("更新会话预算时必须提供 conversation")
        if self.novel_id is not None and self.novel is None and self.arc_window_size is None:
            raise ValueError("更新小说预算时必须提供 novel 或 arc_window_size")
        return self
