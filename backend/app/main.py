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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    novels,
    analysis,
    timeline,
    characters,
    fanfiction,
    models,
    auth,
    rag,
    search,
)
from app.api.agent_tools import router as agent_tools_router
from app.api.agent import router as agent_router
from app.api.clues import router as clues_router
from app.api.asset_audit import router as asset_audit_router
from app.api.eval import router as eval_router
from app.api.gateway import router as gateway_router
from app.api.knowledge import router as knowledge_router
from app.api.narrative_memory import router as narrative_memory_router
from app.api.reader_chat import router as reader_chat_router
from app.api.relationships import router as relationships_router
from app.api.settings import router as settings_router
from app.api.usage import router as usage_router
from app.api.visual_bible import router as visual_bible_router
from app.api.key_scenes import router as key_scenes_router
from app.api.scene_specs import router as scene_specs_router
from app.api.prompt_revisions import router as prompt_revisions_router
from app.api.illustrations import router as illustrations_router
from app.api.illustration_anchors import router as illustration_anchors_router
from app.api.export import router as export_router
from app.api.canon_fork import router as canon_fork_router
from app.api.canon_retrieval import router as canon_retrieval_router
from app.api.derivative_projects import router as derivative_projects_router
from app.api.derivative_chapters import router as derivative_chapters_router
from app.api.derivative_revisions import router as derivative_revisions_router
from app.api.derivative_context import router as derivative_context_router
from app.api.derivative_generation import router as derivative_generation_router
from app.api.derivative_overrides import router as derivative_overrides_router
from app.api.derivative_visual import router as derivative_visual_router
from app.api.derivative_visual_assets import router as derivative_visual_assets_router
from app.api.agent_derivative_edits import router as agent_derivative_edits_router
from app.config import settings
from app.core.logging import RequestLoggingMiddleware, setup_logging
from app.services.agent_tools.errors import AgentToolError

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

    # 恢复过期租约的导入任务
    from app.services.import_service import import_service
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        recovered = await import_service.recover_stale_jobs(db)
        if recovered:
            logger.info(f"恢复 {len(recovered)} 个过期导入任务")
        await db.commit()

    # 从库中恢复 AI 路由全局偏好（未设置时保持默认 "balanced"）
    try:
        from app.services.ai_router import ai_router
        from app.services.settings_service import get_routing_preference

        async with async_session_factory() as db:
            preference = await get_routing_preference(db)
        ai_router.update_preference(preference)
        logger.info(f"AI 路由偏好: {preference}")
    except Exception as e:
        # 表不存在（迁移未执行）等情况不阻断启动
        logger.warning(f"恢复 AI 路由偏好失败，使用默认值: {e}")

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


# 智能体工具错误 → 冻结错误码信封 {error: {code, message}}（25.2-02 / D-07）。
# 该错误类型只由 agent-tools 门面抛出，因此不影响其他 API 的错误形状。
@app.exception_handler(AgentToolError)
async def agent_tool_error_handler(request: Request, exc: AgentToolError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


# 请求校验失败（FastAPI 422）: agent-tools / gateway 路径包装为冻结的
# invalid_input 错误码；其余路径保持 FastAPI 默认 422 形状，避免回归。
@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/agent-tools") or request.url.path.startswith(
        "/api/gateway"
    ):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_input", "message": "参数校验失败"}},
        )
    from fastapi.encoders import jsonable_encoder

    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


# ── 注册 API 路由 ──
# 每个路由模块负责一个业务领域，prefix 定义 URL 前缀，tags 用于 Swagger 分组
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(novels.router, prefix="/api/novels", tags=["小说管理"])
app.include_router(analysis.router, prefix="/api/analysis", tags=["剧情分析"])
app.include_router(timeline.router, prefix="/api/timeline", tags=["时间线"])
app.include_router(clues_router, prefix="/api/clues", tags=["线索与伏笔"])
app.include_router(
    narrative_memory_router,
    prefix="/api/narrative-memory",
    tags=["叙事记忆结构"],
)
app.include_router(asset_audit_router)
app.include_router(characters.router, prefix="/api/characters", tags=["人物关系"])
app.include_router(
    relationships_router,
    prefix="/api/relationships",
    tags=["人物关系图"],
)
app.include_router(fanfiction.router, prefix="/api/fanfiction", tags=["同人文"])
app.include_router(models.router, prefix="/api/models", tags=["AI 模型"])
app.include_router(settings_router, prefix="/api/settings", tags=["设置中心"])
app.include_router(usage_router, prefix="/api/usage", tags=["用量统计"])
app.include_router(rag.router, prefix="/api/novels", tags=["RAG 检索"])
app.include_router(knowledge_router)
app.include_router(search.router, prefix="/api/search", tags=["搜索"])
app.include_router(eval_router)
app.include_router(
    reader_chat_router,
    prefix="/api/novels",
    tags=["阅读器对话"],
)
app.include_router(
    agent_tools_router,
    prefix="/api/agent-tools",
    tags=["智能体工具"],
)
app.include_router(
    gateway_router,
    prefix="/api/gateway",
    tags=["模型网关"],
)
app.include_router(
    agent_router,
    prefix="/api/agent",
    tags=["智能体运行时"],
)
app.include_router(
    visual_bible_router,
    prefix="/api/novels",
    tags=["视觉圣经"],
)
app.include_router(
    key_scenes_router,
    prefix="/api/novels",
    tags=["关键场景"],
)
app.include_router(
    scene_specs_router,
    prefix="/api/novels",
    tags=["场景规格"],
)
app.include_router(
    prompt_revisions_router,
    prefix="/api/novels",
    tags=["提示词修订"],
)
app.include_router(
    illustrations_router,
    prefix="/api/novels",
    tags=["插图生成"],
)
app.include_router(
    illustration_anchors_router,
    prefix="/api/novels",
    tags=["插图锚点"],
)
app.include_router(
    export_router,
    prefix="/api/novels",
    tags=["小说导出"],
)
app.include_router(
    canon_fork_router,
    prefix="/api/novels",
    tags=["Canon Fork"],
)
app.include_router(
    canon_retrieval_router,
    prefix="/api/novels",
    tags=["Canon Fork Retrieval"],
)
app.include_router(
    derivative_projects_router,
    prefix="/api/novels",
    tags=["Derivative Projects"],
)
app.include_router(
    derivative_chapters_router,
    prefix="/api/novels",
    tags=["Derivative Chapters"],
)
app.include_router(
    derivative_revisions_router,
    prefix="/api/novels",
    tags=["Derivative Revisions"],
)
app.include_router(
    derivative_context_router,
    prefix="/api/novels",
    tags=["Derivative Context Packages"],
)
app.include_router(
    derivative_generation_router,
    prefix="/api/novels",
    tags=["Derivative Generation Jobs"],
)
app.include_router(
    derivative_overrides_router,
    prefix="/api/novels",
    tags=["Derivative Overrides"],
)
app.include_router(
    derivative_visual_router,
    prefix="/api/novels",
    tags=["Derivative Visual"],
)
app.include_router(
    derivative_visual_assets_router,
    prefix="/api/novels",
    tags=["Derivative Visual Assets"],
)
app.include_router(
    agent_derivative_edits_router,
    prefix="/api/agent",
    tags=["Agent Derivative Edits"],
)


@app.get("/api/health")
async def health_check():
    """
    健康检查端点。

    用于 Docker healthcheck、负载均衡器探活、前端启动检测。
    返回应用状态和版本号。
    """
    return {"status": "ok", "version": "0.1.0"}
