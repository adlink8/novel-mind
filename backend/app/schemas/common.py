"""
通用 API 响应模型

提供所有 API 端点共用的基础响应结构:
  - ErrorResponse    : 统一错误响应（400/404/500）
  - SuccessResponse  : 统一成功响应（删除、操作确认）
  - PaginatedResponse: 通用分页响应（泛型，支持任意数据类型）

分页响应格式:
  {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20,
    "total_pages": 5
  }
"""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """统一错误响应，用于 400/404/500 等错误码"""

    detail: str  # 错误描述信息
    code: Optional[str] = None  # 可选的错误代码（便于前端国际化）


class SuccessResponse(BaseModel):
    """统一成功响应，用于删除确认、操作成功等场景"""

    message: str  # 成功描述信息
    data: Optional[dict] = None  # 可选的附加数据


class PaginatedResponse(BaseModel, Generic[T]):
    """
    通用分页响应（泛型）。

    使用方式:
      PaginatedResponse[NovelListResponse]  → items 类型为 List[NovelListResponse]
    """

    items: List[T]  # 当前页数据列表
    total: int  # 总记录数
    page: int  # 当前页码（从 1 开始）
    page_size: int  # 每页大小
    total_pages: int  # 总页数

    # from_attributes=True: 支持直接从 ORM 对象转换（Novel → NovelResponse）
    model_config = ConfigDict(from_attributes=True)
