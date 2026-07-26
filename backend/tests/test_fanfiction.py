"""
同人文 API 延期端点测试（NM-API-003）

所有端点统一返回 HTTP 501，响应体携带结构化延期声明
{"detail": ..., "status": "deferred", "planned_milestone": "v1.4"}。
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient


def _assert_deferred(response):
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["status"] == "deferred"
    assert detail["planned_milestone"] == "v1.4"
    assert detail["detail"]


@pytest.mark.asyncio
async def test_list_fanfictions_deferred(auth_client: AsyncClient):
    """同人文列表返回 501 deferred（不再返回看似合法的空数组）"""
    response = await auth_client.get("/api/fanfiction/1")
    _assert_deferred(response)


@pytest.mark.asyncio
async def test_create_fanfiction_deferred(auth_client: AsyncClient):
    """创建同人文返回 501 deferred"""
    response = await auth_client.post("/api/fanfiction", json={"title": "test"})
    _assert_deferred(response)


@pytest.mark.asyncio
async def test_continue_writing_deferred(auth_client: AsyncClient):
    """AI 续写返回 501 deferred"""
    response = await auth_client.post(
        "/api/fanfiction/1/continue", json={"prompt": "继续写"}
    )
    _assert_deferred(response)
