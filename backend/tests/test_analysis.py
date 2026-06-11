"""
剧情分析 API 占位端点测试

当前所有生成类端点返回 HTTP 501。
查询类端点返回空状态。
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_analyze_novel_not_implemented(auth_client: AsyncClient):
    """整本小说分析返回 501"""
    response = await auth_client.post("/api/analysis/1/analyze")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_get_analysis_empty(auth_client: AsyncClient):
    """获取分析结果返回空状态"""
    response = await auth_client.get("/api/analysis/1")
    assert response.status_code == 200
    data = response.json()
    assert data["novel_id"] == 1
    assert data["status"] == "not_analyzed"


@pytest.mark.asyncio
async def test_analyze_chapter_not_implemented(auth_client: AsyncClient):
    """章节分析返回 501"""
    response = await auth_client.post("/api/analysis/1/chapters/1/analyze")
    assert response.status_code == 501


@pytest.mark.asyncio
async def test_analyze_stream_not_implemented(auth_client: AsyncClient):
    """流式分析返回 501"""
    response = await auth_client.post("/api/analysis/1/analyze/stream")
    assert response.status_code == 501
