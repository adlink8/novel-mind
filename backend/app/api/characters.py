"""
人物关系 API 路由 — 已退役（Phase 25 收口，NM-API-001）

历史占位实现曾对查询类端点返回"看似合法的空数组"，会误导消费方认为
数据已就绪。该能力已由 Phase 09 的 owner/version/spoiler-scoped
关系图 API（`/api/relationships`，见 `app/api/relationships.py`）接管。

现状: 所有端点显式返回 HTTP 410 Gone，响应体携带结构化迁移指引
`{"detail": ..., "successor": "/api/relationships/..."}`。
路由保留注册（显式 410 优于 404 静默消失），便于旧客户端定位新端点。

端点列表:
  GET  /api/characters/{novel_id}           -> 410, successor: /api/relationships/{novel_id}/graph
  GET  /api/characters/{novel_id}/relations -> 410, successor: /api/relationships/{novel_id}/graph
  POST /api/characters/{novel_id}/extract   -> 410, successor: /api/relationships/{novel_id}/graph
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


def _gone(detail: str, successor: str) -> HTTPException:
    return HTTPException(
        status_code=410,
        detail={"detail": detail, "successor": successor},
    )


@router.get("/{novel_id}")
async def get_characters(novel_id: int):
    """已退役: 人物列表由关系图 envelope（nodes）提供。"""
    raise _gone(
        detail=(
            "该端点已退役：人物数据由版本化关系图 API 提供，"
            "请改用 GET /api/relationships/{novel_id}/graph（envelope.nodes）"
        ),
        successor=f"/api/relationships/{novel_id}/graph",
    )


@router.get("/{novel_id}/relations")
async def get_relations(novel_id: int):
    """已退役: 关系网络由 spoiler-safe 关系图 envelope（edges）提供。"""
    raise _gone(
        detail=(
            "该端点已退役：人物关系网络由版本化关系图 API 提供，"
            "请改用 GET /api/relationships/{novel_id}/graph（envelope.edges）"
        ),
        successor=f"/api/relationships/{novel_id}/graph",
    )


@router.post("/{novel_id}/extract")
async def extract_characters(novel_id: int):
    """已退役: 抽取由服务端 timeline/KG 管线完成，不再由客户端触发。"""
    raise _gone(
        detail=(
            "该端点已退役：人物与关系抽取由服务端管线（timeline 抽取 + KG 回填）"
            "完成，结果经 GET /api/relationships/{novel_id}/graph 消费"
        ),
        successor=f"/api/relationships/{novel_id}/graph",
    )
