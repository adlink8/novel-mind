"""通用响应与分页模型"""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """统一错误响应"""

    detail: str
    code: Optional[str] = None


class SuccessResponse(BaseModel):
    """统一成功响应"""

    message: str
    data: Optional[dict] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应"""

    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int

    model_config = ConfigDict(from_attributes=True)
