"""
应用设置服务 - app_settings 键值表读写

当前管理的设置:
  - routing_preference: AI 路由全局偏好（quality / balanced / budget）

说明:
  - 读取时键不存在返回默认值，不自动落库
  - 写入采用 upsert（存在则更新 value）
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting

ROUTING_PREFERENCE_KEY = "routing_preference"
DEFAULT_ROUTING_PREFERENCE = "balanced"
VALID_ROUTING_PREFERENCES = ("quality", "balanced", "budget")


async def get_routing_preference(db: AsyncSession) -> str:
    """读取路由偏好；未设置时返回默认值 "balanced"。"""
    result = await db.execute(
        select(AppSetting.value).where(AppSetting.key == ROUTING_PREFERENCE_KEY)
    )
    value = result.scalar_one_or_none()
    if value in VALID_ROUTING_PREFERENCES:
        return value
    return DEFAULT_ROUTING_PREFERENCE


async def set_routing_preference(db: AsyncSession, preference: str) -> str:
    """
    写入路由偏好（upsert）。

    Args:
        db: 数据库会话
        preference: 偏好值，必须是 quality / balanced / budget 之一

    Returns:
        已写入的偏好值

    Raises:
        ValueError: 非法偏好值
    """
    if preference not in VALID_ROUTING_PREFERENCES:
        raise ValueError(
            f"无效的偏好值: {preference}，可选: quality / balanced / budget"
        )
    result = await db.execute(
        select(AppSetting).where(AppSetting.key == ROUTING_PREFERENCE_KEY)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        db.add(AppSetting(key=ROUTING_PREFERENCE_KEY, value=preference))
    else:
        setting.value = preference
    await db.flush()
    return preference
