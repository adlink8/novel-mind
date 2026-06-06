"""时间线 API"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{novel_id}")
async def get_timeline(novel_id: str):
    """获取小说时间线"""
    # TODO: 查询数据库
    return []


@router.post("/{novel_id}/extract")
async def extract_timeline(novel_id: str):
    """AI 自动提取时间线事件"""
    # TODO:
    # 1. 加载小说全文
    # 2. 调用 AI 提取时间-事件对
    # 3. 结构化存储
    return {"novel_id": novel_id, "status": "extracting", "events": []}


@router.put("/events/{event_id}")
async def update_event(event_id: str, data: dict):
    """更新时间线事件"""
    # TODO: 更新数据库
    return {"event_id": event_id, "status": "updated"}


@router.delete("/events/{event_id}")
async def delete_event(event_id: str):
    """删除时间线事件"""
    # TODO: 删除数据库记录
    return {"message": "已删除"}
