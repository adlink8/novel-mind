"""
设置中心 API 路由

端点列表:
  GET /api/settings/routing - 获取 AI 路由全局偏好
  PUT /api/settings/routing - 更新 AI 路由全局偏好（同步更新内存中的 ai_router）

说明:
  - 偏好持久化在 app_settings 键值表（key = routing_preference）
  - 应用启动时从库中读取并恢复（见 app/main.py lifespan）
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.schemas.settings import RoutingPreferenceResponse, RoutingPreferenceUpdate
from app.services.ai_router import ai_router
from app.services.settings_service import get_routing_preference, set_routing_preference

router = APIRouter(dependencies=[Depends(require_user)])


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
