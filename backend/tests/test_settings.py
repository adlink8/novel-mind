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


@pytest.mark.asyncio
async def test_ai_budget_defaults_are_configurable(auth_client: AsyncClient):
    """AI 预算默认值和叙事记忆窗口可持久化并即时读取。"""
    initial = await auth_client.get("/api/settings/ai-budget")
    assert initial.status_code == 200
    assert initial.json()["conversation"]["max_calls"] == 40
    assert initial.json()["novel"]["max_calls"] == 400
    assert initial.json()["arc_window_size"] == 3

    updated = await auth_client.put(
        "/api/settings/ai-budget",
        json={
            "conversation": {
                "max_calls": 60,
                "max_input_tokens": 500000,
                "max_output_tokens": 100000,
                "max_cost_usd": 6,
            },
            "novel": {
                "max_calls": 600,
                "max_input_tokens": 5000000,
                "max_output_tokens": 1000000,
                "max_cost_usd": 60,
            },
            "arc_window_size": 4,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["conversation"]["max_calls"] == 60
    assert updated.json()["novel"]["max_calls"] == 600
    assert updated.json()["arc_window_size"] == 4

    await auth_client.put(
        "/api/settings/ai-budget",
        json={
            "conversation": {
                "max_calls": 40,
                "max_input_tokens": 400000,
                "max_output_tokens": 80000,
                "max_cost_usd": 5,
            },
            "novel": {
                "max_calls": 400,
                "max_input_tokens": 4000000,
                "max_output_tokens": 800000,
                "max_cost_usd": 50,
            },
            "arc_window_size": 3,
        },
    )
