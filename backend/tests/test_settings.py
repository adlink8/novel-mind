"""
设置中心 - 路由偏好 API 测试

覆盖范围:
- 默认偏好（未设置时返回 balanced）
- 更新偏好（落库 + 同步 ai_router 单例）
- 非法偏好值返回 422
- 更新后 GET 读取一致
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient

from app.services.ai_router import ai_router


@pytest.mark.asyncio
async def test_get_routing_default(auth_client: AsyncClient):
    """未设置时返回默认偏好 balanced"""
    response = await auth_client.get("/api/settings/routing")
    assert response.status_code == 200
    assert response.json() == {"preference": "balanced"}


@pytest.mark.asyncio
async def test_update_routing(auth_client: AsyncClient):
    """更新偏好：响应正确、GET 一致、ai_router 单例同步"""
    response = await auth_client.put(
        "/api/settings/routing", json={"preference": "quality"}
    )
    assert response.status_code == 200
    assert response.json() == {"preference": "quality"}

    # GET 读取一致（已落库）
    get_resp = await auth_client.get("/api/settings/routing")
    assert get_resp.json() == {"preference": "quality"}

    # 内存中的路由器单例已同步
    assert ai_router.routing_preference == "quality"

    # 恢复默认，避免污染其他测试
    await auth_client.put("/api/settings/routing", json={"preference": "balanced"})
    assert ai_router.routing_preference == "balanced"


@pytest.mark.asyncio
async def test_update_routing_idempotent(auth_client: AsyncClient):
    """重复更新同一偏好为 upsert，不报错"""
    for _ in range(2):
        resp = await auth_client.put(
            "/api/settings/routing", json={"preference": "budget"}
        )
        assert resp.status_code == 200
        assert resp.json() == {"preference": "budget"}

    await auth_client.put("/api/settings/routing", json={"preference": "balanced"})


@pytest.mark.asyncio
async def test_update_routing_invalid(auth_client: AsyncClient):
    """非法偏好值返回 422"""
    response = await auth_client.put(
        "/api/settings/routing", json={"preference": "premium"}
    )
    assert response.status_code == 422
