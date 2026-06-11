"""
同人文 API 占位端点测试

当前所有生成类端点返回 HTTP 501。
查询类端点返回空数组。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_fanfictions_empty(auth_client: AsyncClient):
    """获取同人文列表返回空数组"""
    response = await auth_client.get("/api/fanfiction/1")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_fanfiction_not_implemented(auth_client: AsyncClient):
    """创建同人文返回 501"""
    response = await auth_client.post("/api/fanfiction", json={"title": "test"})
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_continue_writing_not_implemented(auth_client: AsyncClient):
    """AI 续写返回 501"""
    response = await auth_client.post(
        "/api/fanfiction/1/continue", json={"prompt": "继续写"}
    )
    assert response.status_code == 501
