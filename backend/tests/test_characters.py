"""
人物关系 API 占位端点测试

当前所有生成类端点返回 HTTP 501。
查询类端点返回空数组。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_characters_empty(auth_client: AsyncClient):
    """获取人物列表返回空数组"""
    response = await auth_client.get("/api/characters/1")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_relations_empty(auth_client: AsyncClient):
    """获取人物关系网络返回空数组"""
    response = await auth_client.get("/api/characters/1/relations")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_extract_characters_not_implemented(auth_client: AsyncClient):
    """人物抽取返回 501"""
    response = await auth_client.post("/api/characters/1/extract")
    assert response.status_code == 501
