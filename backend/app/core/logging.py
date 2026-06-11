"""
结构化日志系统

本模块提供两个核心功能:
1. RequestLoggingMiddleware: ASGI 中间件，记录每个 HTTP 请求的 method、path、状态码、耗时
2. setup_logging(): 配置全局日志格式和级别

日志格式:
  - 开发环境 (debug=True): 人类可读格式 "时间 | 级别 | 模块 | 消息"
  - 生产环境 (debug=False): JSON 格式，方便 ELK/Loki 等日志系统解析

日志命名空间:
  - "novelmind"        : 应用主日志
  - "novelmind.access" : HTTP 请求访问日志
"""

import json
import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

# 访问日志专用 logger
logger = logging.getLogger("novelmind.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    HTTP 请求日志中间件。

    为每个请求记录两条日志:
    1. request_start: 请求开始（method、path、client_ip）
    2. request_complete: 请求结束（method、path、status_code、duration_ms）

    这些日志可用于:
    - 性能监控（duration_ms 识别慢请求）
    - 访问审计（client_ip 追踪来源）
    - 错误排查（status_code 快速定位 4xx/5xx）
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 记录请求开始时间（高精度计时器）
        start_time = time.perf_counter()

        # 提取请求元信息
        method = request.method  # GET / POST / PUT / DELETE
        path = request.url.path  # 请求路径，如 /api/novels
        client_ip = request.client.host if request.client else "unknown"

        # 输出请求开始日志（JSON 格式，便于日志系统解析）
        logger.info(
            json.dumps(
                {
                    "event": "request_start",
                    "method": method,
                    "path": path,
                    "client_ip": client_ip,
                },
                ensure_ascii=False,
            )
        )

        # 执行后续中间件和路由处理
        response: Response = await call_next(request)

        # 计算请求处理耗时（毫秒，保留 2 位小数）
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status_code = response.status_code

        # 输出请求完成日志
        logger.info(
            json.dumps(
                {
                    "event": "request_complete",
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "client_ip": client_ip,
                },
                ensure_ascii=False,
            )
        )

        return response


def setup_logging(debug: bool = False):
    """
    配置全局日志格式与级别。

    Args:
        debug: True 时使用 DEBUG 级别 + 人类可读格式；False 时使用 INFO + JSON 格式

    配置内容:
    - 设置根 logger 级别和 handler
    - 降低第三方库（uvicorn、sqlalchemy、httpx）的日志噪音
    """
    level = logging.DEBUG if debug else logging.INFO

    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()  # 清除已有 handler，避免重复输出

    # 创建控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    if debug:
        # 开发环境：带颜色的可读格式
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # 生产环境：纯 JSON 格式（消息本身已经是 JSON）
        formatter = logging.Formatter(fmt="%(message)s")

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 降低第三方库日志级别，减少噪音
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # uvicorn 访问日志
    logging.getLogger("sqlalchemy.engine").setLevel(  # SQL 语句
        logging.WARNING if not debug else logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx HTTP 客户端
