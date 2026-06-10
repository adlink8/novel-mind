"""
同人文/续写 API 路由（Phase 6 实现）

当前状态: 占位实现，生成类端点返回 HTTP 501，查询类返回空数组。

端点列表:
  GET  /api/fanfiction/{novel_id}            - 获取小说的同人文列表 -> []
  POST /api/fanfiction                       - 创建同人文 -> 501
  POST /api/fanfiction/{id}/continue         - AI 续写（流式输出）-> 501
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/{novel_id}")
async def list_fanfictions(novel_id: str):
    """获取小说的同人文列表"""
    # TODO: 从 fan_fictions 表查询
    return []


@router.post("")
async def create_fanfiction(data: dict):
    """创建同人文（指定原作、标题、续写提示）"""
    raise HTTPException(status_code=501, detail="同人文创建尚未实现")


@router.post("/{fanfiction_id}/continue")
async def continue_writing(fanfiction_id: str, prompt: dict):
    """AI 续写（流式输出，基于 RAG 上下文注入）"""
    raise HTTPException(status_code=501, detail="AI 续写尚未实现")
