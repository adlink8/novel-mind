"""
时间线 API 路由（Phase 4 实现）

当前状态: 占位实现，生成类端点返回 HTTP 501，查询类返回空数组。

端点列表:
  GET    /api/timeline/{novel_id}           - 获取小说时间线 -> []
  POST   /api/timeline/{novel_id}/extract   - AI 自动提取时间线事件 -> 501
  PUT    /api/timeline/events/{event_id}    - 更新时间线事件 -> 501
  DELETE /api/timeline/events/{event_id}    - 删除时间线事件 -> 501
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/{novel_id}")
async def get_timeline(novel_id: int):
    """获取小说时间线（事件列表，按 sort_order 排序）"""
    # TODO: 从 timeline_events 表查询
    return []


@router.post("/{novel_id}/extract")
async def extract_timeline(novel_id: int):
    """AI 自动提取时间线事件（LLM 从全文抽取事件 + 因果链）"""
    raise HTTPException(status_code=501, detail="时间线事件抽取尚未实现")


@router.put("/events/{event_id}")
async def update_event(event_id: str, data: dict):
    """更新时间线事件（手动编辑）"""
    raise HTTPException(status_code=501, detail="时间线事件编辑尚未实现")


@router.delete("/events/{event_id}")
async def delete_event(event_id: str):
    """删除时间线事件"""
    raise HTTPException(status_code=501, detail="时间线事件删除尚未实现")
