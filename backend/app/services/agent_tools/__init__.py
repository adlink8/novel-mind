"""
智能体工具门面包（25.2-02 Domain Tool Contract）。

对外暴露:
  - errors.py : 冻结的错误码表（唯一事实源，agent-service 需镜像）
  - facade.py : ToolFacade —— 7 个只读工具的统一执行门面
"""

from app.services.agent_tools.errors import (
    AGENT_TOOL_ERROR_CODES,
    AgentToolError,
    BeyondCutoffError,
    BudgetExceededError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    OutputTooLargeError,
    ToolTimeoutError,
    UpstreamError,
)
from app.services.agent_tools.facade import (
    TOOL_NAMES,
    ToolFacade,
    tool_facade,
)

__all__ = [
    "AGENT_TOOL_ERROR_CODES",
    "AgentToolError",
    "BeyondCutoffError",
    "BudgetExceededError",
    "ForbiddenError",
    "InvalidInputError",
    "NotFoundError",
    "OutputTooLargeError",
    "ToolTimeoutError",
    "UpstreamError",
    "TOOL_NAMES",
    "ToolFacade",
    "tool_facade",
]
