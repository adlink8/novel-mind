"""
同人文旧 API 废弃端点测试（NM-API-003）

所有端点统一返回 HTTP 410，响应体携带 canonical route 迁移声明。
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient


def _assert_deprecated(response):
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["status"] == "deprecated"
    assert detail["canonical_route"]
    assert detail["reason"] == "legacy_fanfiction_route_not_connected"
    assert detail["detail"]


@pytest.mark.asyncio
async def test_list_fanfictions_deprecated(auth_client: AsyncClient):
    """旧列表入口返回 410 deprecated（不再返回看似合法的空数组）"""
    response = await auth_client.get("/api/fanfiction/1")
    _assert_deprecated(response)


@pytest.mark.asyncio
async def test_create_fanfiction_deprecated(auth_client: AsyncClient):
    """旧创建入口返回 410 deprecated"""
    response = await auth_client.post("/api/fanfiction", json={"title": "test"})
    _assert_deprecated(response)


@pytest.mark.asyncio
async def test_continue_writing_deprecated(auth_client: AsyncClient):
    """旧续写入口返回 410，并指向 route-skill"""
    response = await auth_client.post(
        "/api/fanfiction/1/continue", json={"prompt": "继续写"}
    )
    _assert_deprecated(response)
    assert response.json()["detail"]["canonical_route"].endswith("route-skill")
