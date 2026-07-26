"""
基础冒烟测试

验证:
- FastAPI 应用能正常启动
- /api/health 端点返回正确响应
- 基本的请求/响应流程正常
- 未实现占位端点正确返回 501；已实现端点对不存在的小说返回 404
"""

import pytest

pytestmark = pytest.mark.unit
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
async def test_analysis_missing_novel_returns_404(auth_client: AsyncClient):
    """分析端点已实现：小说不存在时返回 404（不再是 501 桩）"""
    response = await auth_client.post("/api/analysis/99999999/analyze")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_timeline_missing_novel_returns_404(auth_client: AsyncClient):
    """时间线端点已实现：小说不存在时返回 404（不再是 501 桩）"""
    response = await auth_client.post("/api/timeline/99999999/extract")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_characters_extract_retired(auth_client: AsyncClient):
    """人物抽取端点已退役：返回 410 并给出 successor 指引"""
    response = await auth_client.post("/api/characters/1/extract")
    assert response.status_code == 410
    assert response.json()["detail"]["successor"] == "/api/relationships/1/graph"
