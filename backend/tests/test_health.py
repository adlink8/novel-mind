"""
基础冒烟测试

验证:
- FastAPI 应用能正常启动
- /api/health 端点返回正确响应
- 基本的请求/响应流程正常
- 占位端点正确返回 501
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient):
    """测试健康检查端点"""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_novels_list_empty(auth_client: AsyncClient):
    """测试空数据库下的小说列表"""
    response = await auth_client.get("/api/novels")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_models_list_empty(auth_client: AsyncClient):
    """测试空数据库下的模型列表"""
    response = await auth_client.get("/api/models")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_analysis_not_implemented(auth_client: AsyncClient):
    """测试占位端点返回 501"""
    response = await auth_client.post("/api/analysis/1/analyze")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_timeline_not_implemented(auth_client: AsyncClient):
    """测试时间线提取端点返回 501"""
    response = await auth_client.post("/api/timeline/1/extract")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_characters_not_implemented(auth_client: AsyncClient):
    """测试人物抽取端点返回 501"""
    response = await auth_client.post("/api/characters/1/extract")
    assert response.status_code == 501
