"""
设置中心 API 路由

端点列表:
  GET /api/settings/routing - 获取 AI 路由全局偏好
  PUT /api/settings/routing - 更新 AI 路由全局偏好（同步更新内存中的 ai_router）

说明:
  - 偏好持久化在 app_settings 键值表（key = routing_preference）
  - 应用启动时从库中读取并恢复（见 app/main.py lifespan）
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import User
from app.schemas.agent_settings import AgentSettingsResponse, AgentSettingsUpdate
from app.schemas.settings import RoutingPreferenceResponse, RoutingPreferenceUpdate
from app.services.ai_router import ai_router
from app.services.agent_settings_service import get_agent_settings, set_agent_settings
from app.services.settings_service import get_routing_preference, set_routing_preference

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/agent", response_model=AgentSettingsResponse)
async def get_agent(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Return the authenticated owner's Agent settings or typed defaults."""

    return await get_agent_settings(db, owner_id=current_user.id)


@router.put("/agent", response_model=AgentSettingsResponse)
async def update_agent(
    data: AgentSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Replace the authenticated owner's typed Agent settings."""
    try:
        return await set_agent_settings(db, owner_id=current_user.id, data=data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/routing", response_model=RoutingPreferenceResponse)
async def get_routing(db: AsyncSession = Depends(get_db)):
    """获取当前 AI 路由全局偏好（未设置时返回默认 "balanced"）"""
    preference = await get_routing_preference(db)
    return RoutingPreferenceResponse(preference=preference)


@router.put("/routing", response_model=RoutingPreferenceResponse)
async def update_routing(
    data: RoutingPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新 AI 路由全局偏好（落库 + 同步内存中的路由器单例）"""
    preference = await set_routing_preference(db, data.preference)
    ai_router.update_preference(preference)
    return RoutingPreferenceResponse(preference=preference)
