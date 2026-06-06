"""人物关系 API"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/{novel_id}")
async def get_characters(novel_id: str):
    """获取小说人物列表"""
    # TODO: 查询数据库
    return []


@router.get("/{novel_id}/relations")
async def get_relations(novel_id: str):
    """获取人物关系网络"""
    # TODO: 查询关系数据
    return []


@router.post("/{novel_id}/extract")
async def extract_characters(novel_id: str):
    """AI 自动识别人物及关系"""
    # TODO:
    # 1. 加载小说内容
    # 2. AI 识别人物、关系
    # 3. 结构化存储
    return {"novel_id": novel_id, "status": "extracting"}
