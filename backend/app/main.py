"""
NovelMind 后端 - FastAPI ASGI 应用入口

本文件负责:
1. 创建 FastAPI 应用实例，配置元数据和生命周期
2. 注册三层中间件（从外到内）:
   - TrailingSlashMiddleware: 去除路径尾部斜杠，解决 Next.js 代理冲突
   - RequestLoggingMiddleware: 记录请求日志
   - CORSMiddleware: 跨域资源共享
3. 注册全局异常处理（500 通用错误、400 参数错误）
4. 挂载 6 个 API 路由模块（小说、分析、时间线、人物、同人文、AI模型）
5. 提供 /api/health 健康检查端点

中间件执行顺序（请求进入时）:
  TrailingSlash → RequestLogging → CORS → 路由处理
"""

import logging
from contextlib import asynccontextmanager
from sqlalchemy.engine import make_url

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import novels, analysis, timeline, characters, fanfiction, models, auth
from app.config import settings
from app.core.logging import RequestLoggingMiddleware, setup_logging

logger = logging.getLogger("novelmind")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器。

    启动时（yield 之前）:
    - 初始化日志系统
    - 打印启动信息（数据库连接串、调试模式）

    关闭时（yield 之后）:
    - 打印关闭日志
    - 可在此处添加资源清理逻辑（关闭连接池等）
    """
    setup_logging(debug=settings.debug)
    logger.info("NovelMind API 启动中...")
    logger.info(
        "  数据库: %s",
        make_url(settings.database_url).render_as_string(hide_password=True),
    )
    logger.info(f"  调试模式: {settings.debug}")
    logger.info("服务就绪 ✓")
    yield
    logger.info("NovelMind API 已关闭")


# 创建 FastAPI 应用实例
app = FastAPI(
    title="NovelMind API",  # Swagger UI 标题
    description="AI 辅助小说创作与理解平台",  # API 描述
    version="0.1.0",  # 版本号
    lifespan=lifespan,  # 生命周期管理
    redirect_slashes=False,  # 禁用自动斜杠重定向（由中间件处理）
)


class TrailingSlashMiddleware:
    """
    ASGI 中间件：去除请求路径的尾部斜杠。

    解决问题: Next.js rewrite 代理有时会在路径末尾添加斜杠，
    导致 FastAPI 路由匹配失败（如 /api/novels/ 不匹配 /api/novels）。

    行为: 除了根路径 "/" 外，所有以 "/" 结尾的路径都会被去掉尾部斜杠。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path != "/" and path.endswith("/"):
                scope["path"] = path.rstrip("/")
        await self.app(scope, receive, send)


# ── 注册中间件（注意：后注册的先执行） ──

# 第三层（最内层）: 尾部斜杠处理
app.add_middleware(TrailingSlashMiddleware)

# 第二层: 请求日志记录
app.add_middleware(RequestLoggingMiddleware)

# 第一层（最外层）: CORS 跨域配置
# allow_origins: 允许前端域名访问
# allow_credentials: 允许携带 Cookie（未来认证需要）
# allow_methods/allow_headers: 开放所有方法和头部
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 全局异常处理 ──


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    兜底异常处理器：捕获所有未处理的异常。

    返回统一的 500 错误响应，避免将内部堆栈暴露给客户端。
    同时记录完整错误日志（含堆栈），便于排查。
    """
    logger.error(f"未处理的异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试"},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """
    ValueError 处理器：参数校验失败等业务错误。

    返回 400 响应，将错误信息直接传递给客户端（如文件格式错误、编码失败等）。
    """
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
    )


# ── 注册 API 路由 ──
# 每个路由模块负责一个业务领域，prefix 定义 URL 前缀，tags 用于 Swagger 分组
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(novels.router, prefix="/api/novels", tags=["小说管理"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["剧情分析"])
app.include_router(timeline.router, prefix="/api/timeline", tags=["时间线"])
app.include_router(characters.router, prefix="/api/characters", tags=["人物关系"])
app.include_router(fanfiction.router, prefix="/api/fanfiction", tags=["同人文"])
app.include_router(models.router, prefix="/api/models", tags=["AI 模型"])


@app.get("/api/health")
async def health_check():
    """
    健康检查端点。

    用于 Docker healthcheck、负载均衡器探活、前端启动检测。
    返回应用状态和版本号。
    """
    return {"status": "ok", "version": "0.1.0"}
