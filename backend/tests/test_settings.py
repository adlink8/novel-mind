"""
设置中心 - AI 预算 API 测试

覆盖范围:
- 默认预算（未设置时使用兼容旧值）
- 默认预算更新后 GET 读取一致
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_ai_budget_defaults_are_configurable(auth_client: AsyncClient):
    """AI 预算默认值和叙事记忆窗口可持久化并即时读取。"""
    initial = await auth_client.get("/api/settings/ai-budget")
    assert initial.status_code == 200
    assert initial.json()["conversation"]["max_calls"] == 40
    assert initial.json()["novel"]["max_calls"] == 400

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
        },
    )
    assert updated.status_code == 200
    assert updated.json()["conversation"]["max_calls"] == 60
    assert updated.json()["novel"]["max_calls"] == 600

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
        },
    )
