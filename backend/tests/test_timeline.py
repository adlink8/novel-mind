"""
时间线 API 占位端点测试

当前所有生成类/修改类端点返回 HTTP 501。
查询类端点返回空数组。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_timeline_empty(auth_client: AsyncClient):
    """获取时间线返回空数组"""
    response = await auth_client.get("/api/timeline/1")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_extract_timeline_not_implemented(auth_client: AsyncClient):
    """时间线抽取返回 501"""
    response = await auth_client.post("/api/timeline/1/extract")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_update_event_not_implemented(auth_client: AsyncClient):
    """更新时间线事件返回 501"""
    response = await auth_client.put("/api/timeline/events/1", json={"title": "test"})
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_delete_event_not_implemented(auth_client: AsyncClient):
    """删除时间线事件返回 501"""
    response = await auth_client.delete("/api/timeline/events/1")
    assert response.status_code == 501
