"""NovelMind 后端 - FastAPI 入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import novels, analysis, timeline, characters, fanfiction, models
from app.config import settings
from app.core.logging import RequestLoggingMiddleware, setup_logging

logger = logging.getLogger("novelmind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    setup_logging(debug=settings.debug)
    logger.info("NovelMind API 启动中...")
    logger.info(f"  数据库: {settings.database_url[:50]}...")
    logger.info(f"  调试模式: {settings.debug}")
    logger.info("服务就绪 ✓")
    yield
    logger.info("NovelMind API 已关闭")


app = FastAPI(
    title="NovelMind API",
    description="AI 辅助小说创作与理解平台",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)


class TrailingSlashMiddleware:
    """ASGI 中间件：在路由前去除路径尾部斜杠，解决 Next.js 代理冲突"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path != "/" and path.endswith("/"):
                scope["path"] = path.rstrip("/")
        await self.app(scope, receive, send)


app.add_middleware(TrailingSlashMiddleware)

# 日志中间件
app.add_middleware(RequestLoggingMiddleware)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理的异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )


# 注册路由
app.include_router(novels.router, prefix="/api/novels", tags=["小说管理"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["剧情分析"])
app.include_router(timeline.router, prefix="/api/timeline", tags=["时间线"])
app.include_router(characters.router, prefix="/api/characters", tags=["人物关系"])
app.include_router(fanfiction.router, prefix="/api/fanfiction", tags=["同人文"])
app.include_router(models.router, prefix="/api/models", tags=["AI 模型"])


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "0.1.0"}
