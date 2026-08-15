"""
同人文/续写旧 API 路由 — 明确废弃（Phase 25 收口，NM-API-003）

旧入口不再是功能旁路：创作域已由 v1.4 Phase 35-39 的 derivative/agent 链接管。
这些兼容路由统一返回 HTTP 410，并携带到 canonical route-skill / derivative
context package 入口的结构化迁移信息；不伪造空结果，也不在这里重造生成链。

端点列表:
  GET  /api/fanfiction/{novel_id}            - 获取小说的同人文列表 -> 410 deprecated
  POST /api/fanfiction                       - 创建同人文 -> 410 deprecated
  POST /api/fanfiction/{fanfiction_id}/continue - AI 续写 -> 410 deprecated
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

router = APIRouter(dependencies=[Depends(require_user)])

CANONICAL_ENTRY = "/api/agent/novels/{novel_id}/route-skill"
CANONICAL_GENERATION = "/api/novels/{novel_id}/derivative-context-packages"


def _deprecated(detail: str, *, canonical: str) -> HTTPException:
    return HTTPException(
        status_code=410,
        detail={
            "detail": detail,
            "status": "deprecated",
            "canonical_route": canonical,
            "reason": "legacy_fanfiction_route_not_connected",
        },
    )


@router.get("/{novel_id}")
async def list_fanfictions(novel_id: int):
    """旧列表入口已废弃；使用 derivative project/chapter 查询入口。"""
    raise _deprecated(
        "旧同人文列表入口已废弃，请使用 derivative project/chapter 入口",
        canonical=CANONICAL_GENERATION,
    )


@router.post("")
async def create_fanfiction(data: dict):
    """旧创建入口已废弃；不在此旁路创建任何记录。"""
    raise _deprecated(
        "旧同人文创建入口已废弃，请先走 derivative project/fork 边界",
        canonical=CANONICAL_GENERATION,
    )


@router.post("/{fanfiction_id}/continue")
async def continue_writing(fanfiction_id: str, prompt: dict):
    """旧续写入口已废弃；续写必须走 route-skill→agent→candidate/approval 链。"""
    raise _deprecated(
        "旧 AI 续写入口已废弃，请从 route-skill 路由到 continue-derivative-story",
        canonical=CANONICAL_ENTRY,
    )
