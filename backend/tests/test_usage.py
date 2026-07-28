"""
AI 用量统计 API 测试

覆盖范围:
- 空表时汇总为零
- today / week / month 时间窗费用聚合
- total_tokens 为全部 input + output 之和
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_usage_log import AIUsageLog


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_usage_summary_empty(auth_client: AsyncClient):
    """空表时所有汇总为零"""
    response = await auth_client.get("/api/usage/summary")
    assert response.status_code == 200
    assert response.json() == {
        "today_cost_usd": 0.0,
        "week_cost_usd": 0.0,
        "month_cost_usd": 0.0,
        "total_tokens": 0,
    }


@pytest.mark.asyncio
async def test_usage_summary_windows(
    auth_client: AsyncClient, db_session: AsyncSession
):
    """today（当天 0 点起）/ week（近 7 天）/ month（近 30 天）窗口聚合"""
    now = _naive_utc_now()
    rows = [
        # 今天：cost 0.01, tokens 100 + 50
        AIUsageLog(
            model_name="gpt-4o",
            provider="openai",
            task_type="analysis",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            created_at=now,
        ),
        # 3 天前：cost 0.02, tokens 200 + 100
        AIUsageLog(
            model_name="gpt-4o-mini",
            provider="openai",
            task_type="summary",
            input_tokens=200,
            output_tokens=100,
            cost_usd=0.02,
            created_at=now - timedelta(days=3),
        ),
        # 20 天前：cost 0.04, tokens 400 + 200
        AIUsageLog(
            model_name="ollama/qwen2:7b",
            provider="ollama",
            task_type="embedding",
            input_tokens=400,
            output_tokens=200,
            cost_usd=0.04,
            created_at=now - timedelta(days=20),
        ),
    ]
    db_session.add_all(rows)
    await db_session.flush()

    response = await auth_client.get("/api/usage/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["today_cost_usd"] == pytest.approx(0.01)
    assert data["week_cost_usd"] == pytest.approx(0.03)
    assert data["month_cost_usd"] == pytest.approx(0.07)
    assert data["total_tokens"] == 1050
