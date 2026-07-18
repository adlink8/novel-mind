"""
时间线 API 当前契约测试（未实现桩时代的 501 契约已随真正实现更替）。

- 小说不存在时，时间线查询/抽取返回 404（归属校验先于业务逻辑）。
- 旧版平铺事件端点 /api/timeline/events/{id} 已移除（现为
  /api/timeline/{novel_id}/events/{logical_event_id}），访问返回 404。
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_timeline_missing_novel_returns_404(auth_client: AsyncClient):
    """小说不存在时获取时间线返回 404"""
    response = await auth_client.get("/api/timeline/99999999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_extract_timeline_missing_novel_returns_404(auth_client: AsyncClient):
    """小说不存在时触发抽取返回 404"""
    response = await auth_client.post("/api/timeline/99999999/extract")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_update_event_route_removed(auth_client: AsyncClient):
    """旧版 PUT /api/timeline/events/{id} 路由已移除"""
    response = await auth_client.put("/api/timeline/events/1", json={"title": "test"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_delete_event_route_removed(auth_client: AsyncClient):
    """旧版 DELETE /api/timeline/events/{id} 路由已移除"""
    response = await auth_client.delete("/api/timeline/events/1")
    assert response.status_code == 404
