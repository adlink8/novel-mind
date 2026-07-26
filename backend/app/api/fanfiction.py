"""
同人文/续写 API 路由 — 明确延期（Phase 25 收口，NM-API-003）

现状: deferred。创作域（同人文/续写/编辑/导出）由 v1.4 Phase 31-33 接管，
在此之前所有端点统一返回 HTTP 501，响应体携带结构化延期声明
`{"detail": ..., "status": "deferred", "planned_milestone": "v1.4"}`，
不返回"看似合法的空数组"。

端点列表:
  GET  /api/fanfiction/{novel_id}            - 获取小说的同人文列表 -> 501 deferred
  POST /api/fanfiction                       - 创建同人文 -> 501 deferred
  POST /api/fanfiction/{fanfiction_id}/continue - AI 续写 -> 501 deferred
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

router = APIRouter(dependencies=[Depends(require_user)])

PLANNED_MILESTONE = "v1.4"


def _deferred(detail: str) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "detail": detail,
            "status": "deferred",
            "planned_milestone": PLANNED_MILESTONE,
        },
    )


@router.get("/{novel_id}")
async def list_fanfictions(novel_id: int):
    """获取小说的同人文列表（deferred，v1.4 Phase 31-33 实现）"""
    raise _deferred("同人文列表尚未实现，创作域功能延期至 v1.4（Phase 31-33）")


@router.post("")
async def create_fanfiction(data: dict):
    """创建同人文（deferred，v1.4 Phase 31-33 实现）"""
    raise _deferred("同人文创建尚未实现，创作域功能延期至 v1.4（Phase 31-33）")


@router.post("/{fanfiction_id}/continue")
async def continue_writing(fanfiction_id: str, prompt: dict):
    """AI 续写（deferred，v1.4 Phase 31-33 实现）"""
    raise _deferred("AI 续写尚未实现，创作域功能延期至 v1.4（Phase 31-33）")
