"""
剧情分析 API 当前契约测试（未实现桩时代的 501 契约已随真正实现更替）。

- 小说不存在时，分析触发/查询返回 404（归属校验先于业务逻辑）。
- 流式分析端点仍为 501 占位。
"""

import pytest

pytestmark = pytest.mark.unit
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_analyze_novel_missing_novel_returns_404(auth_client: AsyncClient):
    """小说不存在时触发整本分析返回 404"""
    response = await auth_client.post("/api/analysis/99999999/analyze")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_analysis_missing_novel_returns_404(auth_client: AsyncClient):
    """小说不存在时获取分析结果返回 404"""
    response = await auth_client.get("/api/analysis/99999999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_analyze_chapter_missing_novel_returns_404(auth_client: AsyncClient):
    """小说不存在时触发章节分析返回 404"""
    response = await auth_client.post("/api/analysis/99999999/chapters/1/analyze")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_analyze_stream_endpoint_removed(auth_client: AsyncClient):
    """流式分析占位端点已删除（NM-API-002）：返回 404"""
    response = await auth_client.post("/api/analysis/1/analyze/stream")
    assert response.status_code == 404
