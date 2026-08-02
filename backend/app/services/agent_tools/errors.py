"""
冻结的智能体工具错误码表（D-07 / REQ-AGENT-02）。

本表是 agent-service 与 NovelMind FastAPI 之间的稳定契约。一旦发布，
错误码名称**不得更改**（见 25.2-02-PLAN Task 1 "freeze" 要求）。
agent-service 侧必须镜像本表（AGENT_TOOL_ERRORS，见 25.2-RESEARCH Code Examples）。

契约约定:
  - 每个错误码对应一个 AgentToolError 子类，status_code 决定 HTTP 状态，
    code 决定错误响应体中的稳定标识。
  - 响应体形状恒为 ``{"error": {"code": <code>, "message": <message>}}``。
  - 新增错误码必须同时更新 AGENT_TOOL_ERROR_CODES 元组（唯一事实源），
    并在 contract 测试中断言错误码表完整。
"""

from __future__ import annotations

# 唯一事实源：冻结的错误码元组。顺序即文档顺序，请勿重排。
AGENT_TOOL_ERROR_CODES: tuple[str, ...] = (
    "forbidden",  # 客户端无权限访问目标资源（当前走 404-hide 约定，通常不直接出现）
    "not_found",  # 资源不存在（章节不属于该小说等）
    "beyond_cutoff",  # 请求的章节/范围超出当前阅读进度截止点（防剧透）
    "budget_exceeded",  # 预算策略拒绝本次调用（fail closed，调用前拦截）
    "timeout",  # 上游服务执行超过 per-tool 超时
    "output_too_large",  # 序列化后的响应超过 per-tool 字节上限
    "invalid_input",  # 请求参数校验失败（422）
    "upstream_error",  # 未预期/未分类的上游错误
)

# 冻结集合，供校验与测试使用。
AGENT_TOOL_ERROR_CODE_SET: frozenset[str] = frozenset(AGENT_TOOL_ERROR_CODES)


class AgentToolError(Exception):
    """智能体工具错误基类。

    code 必须属于 AGENT_TOOL_ERROR_CODES；status_code 为 HTTP 状态码。
    """

    code: str = "upstream_error"
    status_code: int = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ForbiddenError(AgentToolError):
    code = "forbidden"
    status_code = 403


class NotFoundError(AgentToolError):
    code = "not_found"
    status_code = 404


class BeyondCutoffError(AgentToolError):
    """请求范围超出截止点：响应体中保证零受保护章节内容。"""

    code = "beyond_cutoff"
    status_code = 422


class BudgetExceededError(AgentToolError):
    code = "budget_exceeded"
    status_code = 429


class ToolTimeoutError(AgentToolError):
    code = "timeout"
    status_code = 504


class OutputTooLargeError(AgentToolError):
    code = "output_too_large"
    status_code = 413


class InvalidInputError(AgentToolError):
    code = "invalid_input"
    status_code = 422


class UpstreamError(AgentToolError):
    code = "upstream_error"
    status_code = 502


def code_of(exc: Exception) -> str:
    """从任意异常提取冻结错误码；未知异常统一映射为 upstream_error。"""
    if isinstance(exc, AgentToolError):
        return exc.code
    return "upstream_error"
