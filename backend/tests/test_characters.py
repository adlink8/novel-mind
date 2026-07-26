"""
人物关系 API 退役端点测试（NM-API-001）

所有端点显式返回 HTTP 410 Gone，响应体携带结构化迁移指引
{"detail": ..., "successor": "/api/relationships/..."}。
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient


def _assert_gone_with_successor(response, successor: str):
    assert response.status_code == 410
    detail = response.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["successor"] == successor
    assert "已退役" in detail["detail"]


@pytest.mark.asyncio
async def test_get_characters_gone(auth_client: AsyncClient):
    """人物列表端点返回 410 并指向关系图 successor"""
    response = await auth_client.get("/api/characters/1")
    _assert_gone_with_successor(response, "/api/relationships/1/graph")


@pytest.mark.asyncio
async def test_get_relations_gone(auth_client: AsyncClient):
    """人物关系网络端点返回 410 并指向关系图 successor"""
    response = await auth_client.get("/api/characters/1/relations")
    _assert_gone_with_successor(response, "/api/relationships/1/graph")


@pytest.mark.asyncio
async def test_extract_characters_gone(auth_client: AsyncClient):
    """人物抽取端点返回 410 并指向关系图 successor"""
    response = await auth_client.post("/api/characters/1/extract")
    _assert_gone_with_successor(response, "/api/relationships/1/graph")


@pytest.mark.asyncio
async def test_characters_endpoints_not_silently_removed(auth_client: AsyncClient):
    """路由仍注册：返回显式 410，而非 404 静默消失"""
    for method, path in [
        ("GET", "/api/characters/1"),
        ("GET", "/api/characters/1/relations"),
        ("POST", "/api/characters/1/extract"),
    ]:
        response = await auth_client.request(method, path)
        assert response.status_code == 410, path
