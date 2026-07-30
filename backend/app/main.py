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

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from sqlalchemy.engine import make_url
from sqlalchemy import select

from fastapi import FastAPI, Request
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
from app.api.clues import router as clues_router
from app.api.asset_audit import router as asset_audit_router
from app.api.eval import router as eval_router
from app.api.knowledge import router as knowledge_router
from app.api.narrative_memory import router as narrative_memory_router
from app.api.reader_chat import router as reader_chat_router
from app.api.relationships import router as relationships_router
from app.api.settings import router as settings_router
from app.api.usage import router as usage_router
from app.config import settings
from app.core.logging import RequestLoggingMiddleware, setup_logging

logger = logging.getLogger("novelmind")


async def _resume_pending_embeddings_on_startup() -> None:
    """扫描 pending 文本块并在独立后台任务中续跑 embedding。"""
    from app.core.database import async_session_factory
    from app.models.import_job import ImportJob
    from app.models.text_chunk import TextChunk
    from app.services.indexing_service import indexing_service

    try:
        async with async_session_factory() as db:
            result = await db.execute(
                select(TextChunk.novel_id)
                .where(TextChunk.embedding_status == "pending")
                .distinct()
            )
            novel_ids = list(result.scalars().all())

        if not novel_ids:
            logger.info("启动恢复检查：没有待续跑的 embedding 块")
            return

        logger.info(
            "启动恢复检查：发现 %d 本小说存在 pending embedding，将自动续跑",
            len(novel_ids),
        )
        for novel_id in novel_ids:
            async with async_session_factory() as db:
                try:
                    job_result = await db.execute(
                        select(ImportJob)
                        .where(ImportJob.novel_id == novel_id)
                        .order_by(ImportJob.id.desc())
                    )
                    job = job_result.scalars().first()
                    if job and job.status == "cancelled":
                        logger.info(
                            "跳过已取消任务的 embedding 恢复: novel_id=%s, job_id=%s",
                            novel_id,
                            job.id,
                        )
                        continue

                    result = await indexing_service.resume_pending_embeddings(
                        db, novel_id
                    )
                    if job:
                        # 启动恢复是崩溃恢复路径，直接修正持久化状态，
                        # 不受正常导入状态机的 ready -> embedding 限制影响。
                        job.lease_id = None
                        job.lease_expires_at = None
                        if result["failed_chunks"]:
                            detail = (
                                f"自动恢复 embedding 失败：{result['failed_chunks']} 个块未完成"
                            )
                            job.status = "failed"
                            job.progress = 100
                            job.message = detail
                            job.error_detail = detail
                        else:
                            job.status = "ready"
                            job.progress = 100
                            job.message = (
                                f"服务重启后自动恢复索引完成："
                                f"{result['embedded_chunks']} 个待嵌入块"
                            )
                            job.error_detail = None
                    await db.commit()
                    logger.info(
                        "启动恢复完成 novel_id=%s: %s",
                        novel_id,
                        result,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await db.rollback()
                    logger.exception("启动恢复 embedding 失败 novel_id=%s", novel_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # 恢复失败不能阻断 API 启动；下次重启仍会再次扫描 pending。
        logger.exception("启动扫描 pending embedding 失败")


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
    recovery_task = asyncio.create_task(
        _resume_pending_embeddings_on_startup(),
        name="resume-pending-embeddings",
    )
    try:
        yield
    finally:
        if not recovery_task.done():
            recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await recovery_task
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


@app.get("/api/health")
async def health_check():
    """
    健康检查端点。

    用于 Docker healthcheck、负载均衡器探活、前端启动检测。
    返回应用状态和版本号。
    """
    return {"status": "ok", "version": "0.1.0"}
