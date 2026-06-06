"""AI 模型配置 API"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_models():
    """获取已配置的 AI 模型列表"""
    # TODO: 查询数据库
    return []


@router.post("/")
async def create_model(data: dict):
    """添加 AI 模型配置"""
    # TODO: 存储到数据库
    return {"id": "new-id", "status": "created"}


@router.post("/{model_id}/test")
async def test_model(model_id: str):
    """测试模型连接"""
    # TODO: 使用 LiteLLM 测试连接
    return {"model_id": model_id, "status": "ok", "latency_ms": 0}


@router.post("/{model_id}/default")
async def set_default_model(model_id: str):
    """设为默认模型"""
    # TODO: 更新数据库
    return {"model_id": model_id, "is_default": True}


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    """删除模型配置"""
    # TODO: 删除数据库记录
    return {"message": "已删除"}
