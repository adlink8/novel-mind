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
  - 自定义 base_url 经过 SSRF 白名单校验
  - 测试连接错误已脱敏，不暴露内部细节
"""

import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.core.url_security import validate_ai_base_url
from app.models import User
from app.models.ai_model import AIModelConfig
from app.schemas.ai_model import (
    AIModelConfigCreate,
    AIModelConfigUpdate,
    AIModelConfigResponse,
    AIModelTestResponse,
)

router = APIRouter()


def _owned_model_query(model_id: int, current_user: User):
    query = select(AIModelConfig).where(AIModelConfig.id == model_id)
    if not current_user.is_superuser:
        query = query.where(AIModelConfig.owner_id == current_user.id)
    return query


def _sanitize_test_error(error: Exception) -> str:
    """
    脱敏模型测试错误信息，防止暴露内部细节。

    策略:
    - 隐藏原始 API Key（替换为 ***）
    - 隐藏内部文件路径
    - 提供用户友好的错误分类
    """
    error_msg = str(error)

    # 替换可能的 API Key 泄露
    import re

    error_msg = re.sub(r"sk-[a-zA-Z0-9_-]+", "***", error_msg)
    error_msg = re.sub(r"sk-ant-[a-zA-Z0-9_-]+", "***", error_msg)

    # 分类错误信息
    if (
        "Authentication" in error_msg
        or "Unauthorized" in error_msg
        or "401" in error_msg
    ):
        return "认证失败：请检查 API Key 是否正确"
    if (
        "Connection" in error_msg
        or "ConnectTimeout" in error_msg
        or "timeout" in error_msg.lower()
    ):
        return "连接超时：无法访问该 API 地址，请检查网络或 URL"
    if "NameResolutionError" in error_msg or "getaddrinfo" in error_msg:
        return "DNS 解析失败：无法找到该 API 地址对应的服务器"
    if "404" in error_msg:
        return "端点不存在：请检查模型标识和 API 地址是否正确"

    # 默认：返回通用错误（不暴露原始异常细节）
    return "连接测试失败：请检查 API Key 和地址配置"


@router.get("", response_model=list[AIModelConfigResponse])
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """获取已配置的 AI 模型列表（仅返回激活状态的模型）"""
    query = select(AIModelConfig).where(AIModelConfig.is_active.is_(True))
    if not current_user.is_superuser:
        query = query.where(AIModelConfig.owner_id == current_user.id)
    result = await db.execute(query.order_by(AIModelConfig.created_at.desc()))
    models = list(result.scalars().all())
    return [AIModelConfigResponse.model_validate(m) for m in models]


@router.post("", response_model=AIModelConfigResponse)
async def create_model(
    data: AIModelConfigCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """添加 AI 模型配置（含 SSRF 校验）"""
    # 检查名称唯一性
    existing = await db.execute(
        select(AIModelConfig).where(
            AIModelConfig.owner_id == current_user.id,
            AIModelConfig.name == data.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"模型名称 '{data.name}' 已存在")

    # SSRF 防护：验证自定义 base_url
    base_url = await validate_ai_base_url(data.base_url)

    model = AIModelConfig(
        name=data.name,
        owner_id=current_user.id,
        provider=data.provider,
        model_id=data.model_id,
        api_key=data.api_key,
        base_url=base_url,
        tier=data.tier,
        max_tokens=data.max_tokens,
        temperature=data.temperature,
        is_default=data.is_default,
        extra_params=data.extra_params,
    )

    # 如果设为默认，先取消所有其他模型的默认状态
    if data.is_default:
        await db.execute(
            update(AIModelConfig)
            .where(AIModelConfig.owner_id == current_user.id)
            .values(is_default=False)
        )

    db.add(model)
    await db.flush()
    await db.refresh(model)
    return AIModelConfigResponse.model_validate(model)


@router.put("/{model_id}", response_model=AIModelConfigResponse)
async def update_model(
    model_id: int,
    data: AIModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """更新 AI 模型配置（含 SSRF 校验）"""
    result = await db.execute(_owned_model_query(model_id, current_user))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # SSRF 防护：验证更新的 base_url
    if data.base_url is not None:
        data.base_url = await validate_ai_base_url(data.base_url)

    # 仅更新传入的字段（exclude_unset=True 排除未传的字段）
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(model, key, value)

    # 如果设为默认，取消其他模型的默认状态
    if data.is_default:
        await db.execute(
            update(AIModelConfig)
            .where(
                AIModelConfig.owner_id == model.owner_id, AIModelConfig.id != model_id
            )
            .values(is_default=False)
        )

    await db.flush()
    await db.refresh(model)
    return AIModelConfigResponse.model_validate(model)


@router.post("/{model_id}/test", response_model=AIModelTestResponse)
async def test_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """测试模型连通性（错误信息已脱敏）"""
    result = await db.execute(_owned_model_query(model_id, current_user))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    try:
        from app.services.ai_service import ai_service

        await validate_ai_base_url(model.base_url)
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
        # 错误脱敏：不暴露原始异常细节给前端
        safe_error = _sanitize_test_error(e)
        return AIModelTestResponse(
            success=False,
            model_name=model.name,
            error=safe_error,
        )


@router.post("/{model_id}/default")
async def set_default_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """设为默认模型（取消其他模型的默认状态）"""
    result = await db.execute(_owned_model_query(model_id, current_user))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 取消所有默认
    await db.execute(
        update(AIModelConfig)
        .where(AIModelConfig.owner_id == model.owner_id)
        .values(is_default=False)
    )
    model.is_default = True
    await db.flush()

    return {"id": model_id, "name": model.name, "is_default": True}


@router.delete("/{model_id}")
async def delete_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """删除模型配置（软删除：is_active=False）"""
    result = await db.execute(_owned_model_query(model_id, current_user))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="模型配置不存在")

    # 软删除：标记为不激活，不物理删除
    model.is_active = False
    await db.flush()
    return {"message": "已删除"}
