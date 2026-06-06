"""同人文/续写 API"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/{novel_id}")
async def list_fanfictions(novel_id: str):
    """获取小说的同人文列表"""
    # TODO: 查询数据库
    return []


@router.post("/")
async def create_fanfiction(data: dict):
    """创建同人文"""
    # TODO: 创建记录
    return {"id": "new-id", "status": "draft"}


@router.post("/{fanfiction_id}/continue")
async def continue_writing(fanfiction_id: str, prompt: dict):
    """AI 续写（流式输出）"""
    async def event_generator():
        yield f"data: {{"type": "start"}}\n\n"
        # TODO: 调用 AI 生成续写内容，逐段输出
        yield f"data: {{"type": "text", "content": "续写功能待实现..."}}\n\n"
        yield f"data: {{"type": "complete"}}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
    )
