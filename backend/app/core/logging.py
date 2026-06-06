"""结构化日志中间件 - 请求日志与 JSON 格式化"""

import json
import logging
import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("novelmind.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    HTTP 请求日志中间件。
    记录每个请求的 method、path、status_code、duration_ms。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 记录请求开始时间
        start_time = time.perf_counter()

        # 获取请求信息
        method = request.method
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"

        logger.info(
            json.dumps({
                "event": "request_start",
                "method": method,
                "path": path,
                "client_ip": client_ip,
            }, ensure_ascii=False)
        )

        # 执行后续处理
        response: Response = await call_next(request)

        # 计算耗时
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status_code = response.status_code

        logger.info(
            json.dumps({
                "event": "request_complete",
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "client_ip": client_ip,
            }, ensure_ascii=False)
        )

        return response


def setup_logging(debug: bool = False):
    """
    配置全局日志格式与级别。
    - 开发环境: DEBUG 级别 + 控制台输出
    - 生产环境: INFO 级别 + JSON 格式
    """
    level = logging.DEBUG if debug else logging.INFO

    # 根日志配置
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 handlers，避免重复
    root_logger.handlers.clear()

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)

    if debug:
        # 开发环境：人类可读格式
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # 生产环境：JSON 格式（方便日志收集工具解析）
        formatter = logging.Formatter(fmt="%(message)s")

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 降低第三方库的日志级别
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING if not debug else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
