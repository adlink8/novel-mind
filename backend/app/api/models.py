"""AI 模型配置 API — 接入数据库"""

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
    """获取已配置的 AI 模型列表"""
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

    # 如果设为默认，先取消其他默认
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

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(model, key, value)

    if data.is_default:
        await db.execute(
            update(AIModelConfig).where(AIModelConfig.id != model_id).values(is_default=False)
        )

    await db.flush()
    await db.refresh(model)
    return AIModelConfigResponse.model_validate(model)


@router.post("/{model_id}/test", response_model=AIModelTestResponse)
async def test_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """测试模型连接"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 使用 litellm 测试连接
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
    """设为默认模型"""
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
    """删除模型配置"""
    result = await db.execute(select(AIModelConfig).where(AIModelConfig.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    model.is_active = False
    await db.flush()
    return {"message": "已删除"}
