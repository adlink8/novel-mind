"""
人物关系 API 路由（Phase 3/5 实现）

当前状态: 占位实现，生成类端点返回 HTTP 501，查询类返回空数组。

端点列表:
  GET  /api/characters/{novel_id}           - 获取小说人物列表 -> []
  GET  /api/characters/{novel_id}/relations - 获取人物关系网络 -> []
  POST /api/characters/{novel_id}/extract   - AI 自动识别人物及关系 -> 501
"""

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/{novel_id}")
async def get_characters(novel_id: str):
    """获取小说人物列表"""
    # TODO: 从 characters 表查询
    return []


@router.get("/{novel_id}/relations")
async def get_relations(novel_id: str):
    """获取人物关系网络（用于 AntV G6 图谱可视化）"""
    # TODO: 从 character_relations 表查询
    return []


@router.post("/{novel_id}/extract")
async def extract_characters(novel_id: str):
    """AI 自动识别人物及关系（NER + LLM 混合抽取）"""
    raise HTTPException(status_code=501, detail="人物与关系抽取尚未实现")
