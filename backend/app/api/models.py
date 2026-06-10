"""
AI 模型配置 API 路由

端点列表:
  GET    /api/models                   - 获取已配置的 AI 模型列表
  POST   /api/models                   - 添加 AI 模型配置
  PUT    /api/models/{model_id}        - 更新 AI 模型配置
  POST   /api/models/{model_id}/test   - 测试模型连通性
  POST   /api/models/{model_id}/default - 设为默认模型
  DELETE /api/models/{model_id}        - 删除模型配置（软删除）

安全注意:
  - 删除使用软删除（is_active=False），不物理删除记录
  - 设置默认模型时会先取消其他模型的默认状态
  - api_key 不在 GET 响应中暴露
"""

import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.ai_model import AIModelConfig
from app.schemas.ai_model import (
    AIModelConfigCreate,
    AIModelConfigUpdate,
    AIModelConfigResponse,
    AIModelTestResponse,
)

router = APIRouter()


@router.get("", response_model=list[AIModelConfigResponse])
async def list_models(db: AsyncSession = Depends(get_db)):
    """获取已配置的 AI 模型列表（仅返回激活状态的模型）"""
    result = await db.execute(
        select(AIModelConfig).where(AIModelConfig.is_active == True).order_by(AIModelConfig.created_at.desc())
    )
    models = list(result.scalars().all())
    return [AIModelConfigResponse.model_validate(m) for m in models]


@router.post("", response_model=AIModelConfigResponse)
async def create_model(data: AIModelConfigCreate, db: AsyncSession = Depends(get_db)):
    """添加 AI 模型配置"""
    # 检查名称唯一性
    existing = await db.execute(select(AIModelConfig).where(AIModelConfig.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"模型名称 '{data.name}' 已存在")

    model = AIModelConfig(
        name=data.name,
        provider=data.provider,
        model_id=data.model_id,
        api_key=data.api_key,
        base_url=data.base_url,
        tier=data.tier,
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        is_default=data.is_default,
        extra_params=data.extra_params,
    )

    # 如果设为默认，先取消所有其他模型的默认状态
    if data.is_default:
        await db.execute(update(AIModelConfig).values(is_default=False))

    db.add(model)
    await db.flush()
    await db.refresh(model)
    return AIModelConfigResponse.model_validate(model)


@router.put("/{model_id}", response_model=AIModelConfigResponse)
async def update_model(model_id: int, data: AIModelConfigUpdate, db: AsyncSession = Depends(get_db)):
    """更新 AI 模型配置"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 仅更新传入的字段（exclude_unset=True 排除未传的字段）
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(model, key, value)

    # 如果设为默认，取消其他模型的默认状态
    if data.is_default:
        await db.execute(
            update(AIModelConfig).where(AIModelConfig.id != model_id).values(is_default=False)
        )

    await db.flush()
    await db.refresh(model)
    return AIModelConfigResponse.model_validate(model)


@router.post("/{model_id}/test", response_model=AIModelTestResponse)
async def test_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """测试模型连通性（发送简单请求验证 API Key 和端点）"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    try:
        from app.services.ai_service import ai_service
        start = time.perf_counter()
        response = await ai_service.test_connection(model)
        latency = int((time.perf_counter() - start) * 1000)

        return AIModelTestResponse(
            success=True,
            model_name=model.name,
            latency_ms=latency,
            response_text=response[:200] if response else "OK",
        )
    except Exception as e:
        return AIModelTestResponse(
            success=False,
            model_name=model.name,
            error=str(e),
        )


@router.post("/{model_id}/default")
async def set_default_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """设为默认模型（取消其他模型的默认状态）"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 取消所有默认
    await db.execute(update(AIModelConfig).values(is_default=False))
    model.is_default = True
    await db.flush()

    return {"id": model_id, "name": model.name, "is_default": True}


@router.delete("/{model_id}")
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """删除模型配置（软删除：is_active=False）"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 软删除：标记为不激活，不物理删除
    model.is_active = False
    await db.flush()
    return {"message": "已删除"}
