"""NovelMind 后端 - FastAPI 入口"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import novels, analysis, timeline, characters, fanfiction, models
from app.config import settings

app = FastAPI(
    title="NovelMind API",
    description="AI 辅助小说创作与理解平台",
    version="0.1.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
